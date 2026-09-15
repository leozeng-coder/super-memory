import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { cp, mkdir, mkdtemp, readFile, realpath, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { assertPortAvailable, stopManaged } from "../web/launcher.mjs";

const project = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function withLauncher(action) {
  const temporary = await realpath(await mkdtemp(path.join(os.tmpdir(), "super-memory-launcher-")));
  const root = path.join(temporary, "source");
  const memoryRoot = path.join(temporary, "data");
  const memoryId = createHash("sha256").update(memoryRoot).digest("hex").slice(0, 16);
  const servers = [];
  const children = [];
  await mkdir(path.join(root, "web/node_modules/next/dist/bin"), { recursive: true });
  await mkdir(memoryRoot);
  for (const file of ["start-web.mjs", "web/launcher.mjs", "web/memory-client.mjs", "web/local-api.mjs"]) {
    await cp(path.join(project, file), path.join(root, file));
  }
  await cp(path.join(project, "skills"), path.join(root, "skills"), { recursive: true });
  // A real child + worker tree keeps startup tests independent of Next compilation.
  await writeFile(path.join(root, "web/node_modules/next/dist/bin/next"), `
const { spawn } = require('node:child_process');
const worker = spawn(process.execPath, ['-e', \`
  const { createServer } = require('node:http');
  require('node:fs').writeFileSync(process.env.SUPER_MEMORY_HOME + '/worker.pid', String(process.pid));
  createServer((req, res) => {
    res.end('<title>Super Memory</title>');
    if (req.url === '/exit') setTimeout(() => process.exit(0), 20);
  }).listen(Number(process.env.SUPER_MEMORY_WEB_PORT), '127.0.0.1');
\`], { stdio: 'inherit', windowsHide: true });
worker.on('exit', code => process.exit(code ?? 1));
`);

  async function server(body) {
    const instance = createServer((_request, response) => response.end(typeof body === "string" ? body : JSON.stringify(body)));
    await new Promise((resolve) => instance.listen(0, "127.0.0.1", resolve));
    servers.push(instance);
    return instance.address().port;
  }
  async function freePort() {
    const instance = createServer();
    await new Promise((resolve) => instance.listen(0, "127.0.0.1", resolve));
    const port = instance.address().port;
    await new Promise((resolve) => instance.close(resolve));
    return port;
  }
  function launch(apiPort, webPort) {
    const child = spawn(process.execPath, [path.join(root, "start-web.mjs")], {
      env: { ...process.env, SUPER_MEMORY_HOME: memoryRoot, SUPER_MEMORY_API_PORT: String(apiPort), SUPER_MEMORY_WEB_PORT: String(webPort), SUPER_MEMORY_NO_BROWSER: "1" },
      detached: process.platform !== "win32", windowsHide: true, stdio: ["ignore", "pipe", "pipe"],
    });
    children.push(child);
    let output = "";
    for (const stream of [child.stdout, child.stderr]) stream.on("data", (chunk) => { output += chunk.toString(); });
    const exited = new Promise((resolve, reject) => { child.once("error", reject); child.once("close", resolve); });
    return {
      output: () => output,
      async exit() {
        let timer;
        try {
          const code = await Promise.race([exited, new Promise((_, reject) => { timer = setTimeout(() => reject(new Error("启动器未退出：" + output)), 15000); })]);
          return { code, output };
        } finally { clearTimeout(timer); }
      },
      async ready() {
        const deadline = Date.now() + 15000;
        while (!output.includes("启动成功")) {
          assert.equal(child.exitCode, null, output);
          assert.ok(Date.now() < deadline, output);
          await pause(50);
        }
      },
    };
  }
  try {
    await action({ memoryRoot, memoryId, server, freePort, launch });
  } finally {
    await Promise.all(children.map(stopManaged));
    await Promise.all(servers.map((instance) => new Promise((resolve) => { instance.closeAllConnections(); instance.close(resolve); })));
    assert.equal(path.dirname(temporary), await realpath(os.tmpdir()));
    await rm(temporary, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  }
}

test("旧 API 占用端口时说明重启方式，保留原日志且不启动新服务", async () => withLauncher(async (fixture) => {
  const { memoryRoot, memoryId, server, freePort, launch } = fixture;
  const log = path.join(memoryRoot, "logs/console/api-error.log");
  await mkdir(path.dirname(log), { recursive: true });
  await writeFile(log, "existing diagnostic");
  const apiPort = await server({ ok: true, app: "super-memory", memoryId });
  const webPort = await freePort();
  const result = await launch(apiPort, webPort).exit();
  assert.equal(result.code, 1);
  assert.match(result.output, /旧版.*API.*Ctrl\+C/);
  assert.equal(await readFile(log, "utf8"), "existing diagnostic");
  await assertPortAvailable(webPort, "测试页面");
}));

test("其他规则库占用 API 时明确拒绝混用", async () => withLauncher(async ({ server, freePort, launch }) => {
  const apiPort = await server({ ok: true, app: "super-memory", apiVersion: 2, memoryId: "another-library" });
  const result = await launch(apiPort, await freePort()).exit();
  assert.equal(result.code, 1);
  assert.match(result.output, /其他规则库/);
}));

test("非 Super Memory 程序占用 API 端口时提前失败", async () => withLauncher(async ({ server, freePort, launch }) => {
  const apiPort = await server("unrelated service");
  const result = await launch(apiPort, await freePort()).exit();
  assert.equal(result.code, 1);
  assert.match(result.output, /记忆 API 端口 .* 已被占用/);
}));

test("普通网页不能被当作已启动的管理台", async () => withLauncher(async ({ memoryRoot, server, freePort, launch }) => {
  const apiPort = await freePort();
  const webPort = await server("<title>Another application</title>");
  const result = await launch(apiPort, webPort).exit();
  assert.equal(result.code, 1);
  assert.match(result.output, /页面 端口 .* 已被占用/);
  await assertPortAvailable(apiPort, "测试 API");
  await assert.rejects(readFile(path.join(memoryRoot, "memory.config.json")), { code: "ENOENT" });
}));

test("重复启动复用相同规则库的就绪服务", async () => withLauncher(async ({ memoryId, server, launch }) => {
  const apiPort = await server({ ok: true, app: "super-memory", apiVersion: 2, memoryId });
  const webPort = await server("<title>Super Memory</title>");
  const result = await launch(apiPort, webPort).exit();
  assert.equal(result.code, 0, result.output);
  assert.match(result.output, /已经运行/);
  assert.equal((await fetch(`http://127.0.0.1:${apiPort}/api/health`)).status, 200);
}));

test("API 启动失败会清理同时启动的页面进程树", async () => withLauncher(async ({ memoryRoot, freePort, launch }) => {
  const ruleDir = path.join(memoryRoot, "graph/rules/active");
  await mkdir(ruleDir, { recursive: true });
  await writeFile(path.join(ruleDir, "broken.json"), "{}");
  const apiPort = await freePort(), webPort = await freePort();
  const result = await launch(apiPort, webPort).exit();
  assert.equal(result.code, 1);
  assert.match(result.output, /记忆 API 进程退出/);
  await assertPortAvailable(apiPort, "测试 API");
  await assertPortAvailable(webPort, "测试页面");
  const workerPid = Number(await readFile(path.join(memoryRoot, "worker.pid"), "utf8"));
  for (let attempt = 0; attempt < 20; attempt += 1) {
    try { process.kill(workerPid, 0); } catch { break; }
    await pause(50);
  }
  assert.throws(() => process.kill(workerPid, 0), { code: "ESRCH" });
}));

test("完整启动后页面退出会停止本次 API", async () => withLauncher(async ({ freePort, launch }) => {
  const apiPort = await freePort(), webPort = await freePort();
  const launcher = launch(apiPort, webPort);
  await launcher.ready();
  const health = await (await fetch(`http://127.0.0.1:${apiPort}/api/health`)).json();
  assert.equal(health.apiVersion, 2);
  assert.deepEqual((await (await fetch(`http://127.0.0.1:${apiPort}/api/rules`)).json()).records, []);
  await fetch(`http://127.0.0.1:${webPort}/exit`);
  const result = await launcher.exit();
  assert.equal(result.code, 1);
  assert.match(result.output, /页面进程退出/);
  await assertPortAvailable(apiPort, "测试 API");
  await assertPortAvailable(webPort, "测试页面");
}));
