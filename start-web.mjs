import { closeSync, existsSync, mkdirSync, openSync } from "node:fs";
import { createHash } from "node:crypto";
import path from "node:path";
import { spawn, spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { MemoryClient } from "./web/memory-client.mjs";

const root = path.dirname(fileURLToPath(import.meta.url));
const webRoot = path.join(root, "web");
const packageFile = path.join(webRoot, "package.json");
const nodeModules = path.join(webRoot, "node_modules");
const nextCli = path.join(nodeModules, "next", "dist", "bin", "next");
const apiScript = path.join(webRoot, "local-api.mjs");
const memoryClient = new MemoryClient(path.join(root, "skills", "super-memory", "scripts", "memory.py"));
const memoryRoot = (await memoryClient.run("info")).memory_root;
const apiPort = Number(process.env.SUPER_MEMORY_API_PORT || 4318);
const webPort = Number(process.env.SUPER_MEMORY_WEB_PORT || 3000);
const apiBaseUrl = `http://127.0.0.1:${apiPort}`;
const healthUrl = `${apiBaseUrl}/api/health`;
const pageUrl = `http://127.0.0.1:${webPort}`;
const memoryId = createHash("sha256").update(memoryRoot).digest("hex").slice(0, 16);
const logRoot = path.join(memoryRoot, "logs", "console");
const childEnvironment = {
  ...process.env,
  SUPER_MEMORY_HOME: memoryRoot,
  SUPER_MEMORY_API_PORT: String(apiPort),
  SUPER_MEMORY_WEB_PORT: String(webPort),
  NEXT_PUBLIC_SUPER_MEMORY_API_URL: `${apiBaseUrl}/api`,
};

if (!existsSync(packageFile)) {
  throw new Error(`找不到 Web 工程：${webRoot}`);
}

if (!existsSync(nodeModules)) {
  console.log("首次启动，正在安装页面依赖……");
  const npmCommand = process.platform === "win32" ? "npm.cmd" : "npm";
  const install = spawnSync(
    npmCommand,
    ["install", "--no-audit", "--no-fund"],
    {
      cwd: webRoot,
      env: childEnvironment,
      stdio: "inherit",
      windowsHide: process.platform === "win32",
    },
  );
  if (install.status !== 0) throw new Error("页面依赖安装失败。");
}

mkdirSync(logRoot, { recursive: true });

async function isApiReady() {
  try {
    const response = await fetch(healthUrl, { signal: AbortSignal.timeout(1500) });
    const result = await response.json();
    return (
      response.ok &&
      result.ok === true &&
      result.app === "super-memory" &&
      result.apiVersion === 2 &&
      result.memoryId === memoryId
    );
  } catch {
    return false;
  }
}

async function isWebReady() {
  try {
    const response = await fetch(pageUrl, { signal: AbortSignal.timeout(2500) });
    return response.ok;
  } catch {
    return false;
  }
}

function startManaged(command, args, stdoutName, stderrName) {
  const stdout = openSync(path.join(logRoot, stdoutName), "w");
  const stderr = openSync(path.join(logRoot, stderrName), "w");
  const child = spawn(command, args, {
    cwd: webRoot,
    detached: false,
    env: childEnvironment,
    windowsHide: true,
    stdio: ["ignore", stdout, stderr],
  });
  closeSync(stdout);
  closeSync(stderr);
  return child;
}

function stopProcess(child) {
  if (child && child.exitCode === null && !child.killed) {
    child.kill();
  }
}

function supervise(processes) {
  return new Promise((resolve, reject) => {
    let stopping = false;
    const stopAll = () => {
      if (stopping) return;
      stopping = true;
      for (const child of processes) stopProcess(child);
      resolve();
    };

    process.once("SIGINT", stopAll);
    process.once("SIGTERM", stopAll);
    for (const child of processes) {
      child.once("exit", (code, signal) => {
        if (stopping) return;
        stopping = true;
        for (const sibling of processes) {
          if (sibling !== child) stopProcess(sibling);
        }
        reject(
          new Error(
            `管理台进程意外退出（code=${code ?? "none"}, signal=${signal ?? "none"}），请查看日志目录：${logRoot}`,
          ),
        );
      });
    }
  });
}

function openBrowser() {
  const command =
    process.platform === "win32"
      ? ["cmd.exe", ["/c", "start", "", pageUrl]]
      : process.platform === "darwin"
        ? ["open", [pageUrl]]
        : ["xdg-open", [pageUrl]];
  spawnSync(command[0], command[1], {
    windowsHide: process.platform === "win32",
    stdio: "ignore",
  });
}

let apiReady = false;
let webReady = false;
for (let attempt = 0; attempt < 5; attempt += 1) {
  [apiReady, webReady] = await Promise.all([isApiReady(), isWebReady()]);
  if (apiReady && webReady) break;
  await new Promise((resolve) => setTimeout(resolve, 300));
}

if (apiReady && webReady) {
  console.log("Super Memory 管理台已经运行，正在打开页面……");
  openBrowser();
  process.exit(0);
}

console.log("正在启动 Super Memory 管理台……");

let apiProcess = null;
let webProcess = null;

if (!apiReady) {
  apiProcess = startManaged(
    process.execPath,
    [apiScript],
    "api.log",
    "api-error.log",
  );
}

if (!webReady) {
  webProcess = startManaged(
    process.execPath,
    [nextCli, "dev", "-H", "127.0.0.1", "-p", String(webPort)],
    "web.log",
    "web-error.log",
  );
}

const deadline = Date.now() + 120_000;
while (Date.now() < deadline) {
  [apiReady, webReady] = await Promise.all([isApiReady(), isWebReady()]);
  if (apiReady && webReady) {
    console.log(`启动成功，正在打开：${pageUrl}`);
    openBrowser();
    console.log("Super Memory 正在运行。请保持此窗口打开；按 Ctrl+C 可停止本次启动的服务。");
    await supervise([apiProcess, webProcess].filter(Boolean));
    console.log("Super Memory 已停止。");
    process.exit(0);
  }
  if (apiProcess?.exitCode !== null && !apiReady) {
    throw new Error(`记忆 API 启动失败，请查看 ${path.join(logRoot, "api-error.log")}`);
  }
  if (webProcess?.exitCode !== null && !webReady) {
    throw new Error(`页面启动失败，请查看 ${path.join(logRoot, "web-error.log")}`);
  }
  await new Promise((resolve) => setTimeout(resolve, 500));
}

throw new Error(`启动超时，请查看日志目录：${logRoot}`);
