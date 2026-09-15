import { closeSync, existsSync, mkdirSync, openSync } from "node:fs";
import { createHash } from "node:crypto";
import path from "node:path";
import { spawn, spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { MemoryClient } from "./web/memory-client.mjs";
import { assertPortAvailable, inspectApi, isWebReady, matchesApi, readHealth, stopManaged } from "./web/launcher.mjs";

const root = path.dirname(fileURLToPath(import.meta.url));
const webRoot = path.join(root, "web");
const nextCli = path.join(webRoot, "node_modules", "next", "dist", "bin", "next");

function portNumber(name, fallback) {
  const port = Number(process.env[name] || fallback);
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error(name + " 必须是 1 到 65535 之间的端口号。");
  return port;
}

function openBrowser(url) {
  if (process.env.SUPER_MEMORY_NO_BROWSER === "1") return;
  const [command, args] = process.platform === "win32"
    ? ["cmd.exe", ["/c", "start", "", url]]
    : process.platform === "darwin" ? ["open", [url]] : ["xdg-open", [url]];
  spawnSync(command, args, { windowsHide: true, stdio: "ignore" });
}

async function main() {
  const client = new MemoryClient(path.join(root, "skills", "super-memory", "scripts", "memory.py"));
  const memoryRoot = (await client.run("info")).memory_root;
  const memoryId = createHash("sha256").update(memoryRoot).digest("hex").slice(0, 16);
  const apiPort = portNumber("SUPER_MEMORY_API_PORT", 4318);
  const webPort = portNumber("SUPER_MEMORY_WEB_PORT", 3000);
  if (apiPort === webPort) throw new Error("API 与页面必须使用不同端口。");
  const apiBaseUrl = "http://127.0.0.1:" + apiPort;
  const pageUrl = "http://127.0.0.1:" + webPort;
  const logRoot = path.join(memoryRoot, "logs", "console");
  const env = {
    ...process.env,
    SUPER_MEMORY_HOME: memoryRoot,
    SUPER_MEMORY_API_PORT: String(apiPort),
    SUPER_MEMORY_WEB_PORT: String(webPort),
    NEXT_PUBLIC_SUPER_MEMORY_API_URL: apiBaseUrl + "/api",
  };

  // Resolve occupied ports before spawning either service or overwriting logs.
  let apiReady = await inspectApi(apiPort, memoryId);
  let webReady = await isWebReady(pageUrl);
  if (!webReady) await assertPortAvailable(webPort, "页面 ");
  if (apiReady && webReady) {
    console.log("Super Memory 管理台已经运行：" + pageUrl);
    openBrowser(pageUrl);
    return;
  }

  if (!existsSync(nextCli)) {
    console.log("首次启动，正在安装页面依赖……");
    const command = process.platform === "win32" ? "cmd.exe" : "npm";
    const args = process.platform === "win32"
      ? ["/d", "/s", "/c", "npm install --no-audit --no-fund"]
      : ["install", "--no-audit", "--no-fund"];
    const install = spawnSync(command, args, { cwd: webRoot, env, stdio: "inherit", windowsHide: true });
    if (install.error || install.status !== 0) throw new Error("页面依赖安装失败。");
  }

  mkdirSync(logRoot, { recursive: true });
  console.log("正在启动 Super Memory 管理台……");
  const services = [];
  let stopping = false;
  let cleanup;
  const stopAll = () => {
    stopping = true;
    cleanup ??= Promise.all(services.map(({ child }) => stopManaged(child)));
    return cleanup;
  };
  const onSignal = () => { void stopAll().catch((error) => console.error(error.message)); };
  process.once("SIGINT", onSignal);
  process.once("SIGTERM", onSignal);

  function start(args, label, logName) {
    const stdout = openSync(path.join(logRoot, logName + ".log"), "w");
    let stderr;
    try {
      stderr = openSync(path.join(logRoot, logName + "-error.log"), "w");
      const child = spawn(process.execPath, args, {
        cwd: webRoot, env, detached: process.platform !== "win32", windowsHide: true,
        stdio: ["ignore", stdout, stderr],
      });
      const service = { child, label, logName, failure: null };
      child.once("error", (error) => { service.failure = error; });
      services.push(service);
    } finally {
      closeSync(stdout);
      if (stderr !== undefined) closeSync(stderr);
    }
  }

  function checkChildren() {
    for (const { child, label, logName, failure } of services) {
      if (failure || child.exitCode !== null || child.signalCode !== null) {
        throw new Error(label + "进程退出" + (failure ? "：" + failure.message : "") +
          "，请查看 " + path.join(logRoot, logName + "-error.log"));
      }
    }
  }

  try {
    if (!apiReady) start([path.join(webRoot, "local-api.mjs")], "记忆 API ", "api");
    if (!webReady) start([nextCli, "dev", "-H", "127.0.0.1", "-p", String(webPort)], "页面", "web");
    const deadline = Date.now() + 120_000;
    while (!stopping) {
      checkChildren();
      [apiReady, webReady] = await Promise.all([
        readHealth(apiBaseUrl + "/api/health").then((health) => matchesApi(health, memoryId)),
        isWebReady(pageUrl),
      ]);
      if (stopping) break;
      checkChildren();
      if (apiReady && webReady) {
        console.log("启动成功，正在打开：" + pageUrl);
        openBrowser(pageUrl);
        console.log("Super Memory 正在运行。请保持此窗口打开；按 Ctrl+C 可停止本次启动的服务。");
        while (!stopping) {
          checkChildren();
          await new Promise((resolve) => setTimeout(resolve, 500));
        }
        break;
      }
      if (Date.now() >= deadline) throw new Error("启动超时，请查看日志目录：" + logRoot);
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
  } finally {
    await stopAll();
    process.removeListener("SIGINT", onSignal);
    process.removeListener("SIGTERM", onSignal);
  }
  console.log("Super Memory 已停止。");
}

try {
  await main();
} catch (error) {
  console.error("Super Memory 启动失败：" + error.message);
  process.exitCode = 1;
}