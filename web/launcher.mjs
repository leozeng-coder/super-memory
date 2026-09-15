import { createServer } from "node:net";
import { spawn } from "node:child_process";

export async function readHealth(url) {
  try {
    const response = await fetch(url, { signal: AbortSignal.timeout(1500) });
    return response.ok ? await response.json() : null;
  } catch {
    return null;
  }
}

export function matchesApi(health, memoryId) {
  return health?.ok === true && health.app === "super-memory" &&
    health.apiVersion === 2 && health.memoryId === memoryId;
}

export function assertPortAvailable(port, label) {
  return new Promise((resolve, reject) => {
    const server = createServer();
    server.once("error", (error) => {
      reject(new Error(error.code === "EADDRINUSE"
        ? `${label}端口 ${port} 已被占用。请关闭占用该端口的服务，或设置其他端口后重试。`
        : `${label}端口 ${port} 无法使用：${error.message}`));
    });
    server.listen(port, "127.0.0.1", () => server.close(resolve));
  });
}

export async function inspectApi(port, memoryId) {
  const health = await readHealth(`http://127.0.0.1:${port}/api/health`);
  if (matchesApi(health, memoryId)) return true;
  if (health?.app === "super-memory") {
    if (health.memoryId !== memoryId) {
      throw new Error(`端口 ${port} 上的 Super Memory 使用了其他规则库。请关闭该实例，或设置 SUPER_MEMORY_API_PORT 后重试。`);
    }
    if (health.apiVersion !== 2) {
      throw new Error(`端口 ${port} 上仍在运行旧版或不匹配的 Super Memory API。请先在原管理台窗口按 Ctrl+C 关闭，再重新启动。原窗口已关闭时，请停止该项目占用端口的进程。`);
    }
    throw new Error(`端口 ${port} 上的 Super Memory API 状态异常，请检查原管理台窗口和 API 日志。`);
  }
  await assertPortAvailable(port, "记忆 API ");
  return false;
}

export async function isWebReady(url) {
  try {
    const response = await fetch(url, { signal: AbortSignal.timeout(2500) });
    return response.ok && /<title>Super Memory<\/title>/i.test(await response.text());
  } catch {
    return false;
  }
}

// Only terminate processes started by this invocation, including Next's worker.
export async function stopManaged(child) {
  if (!child?.pid) return;
  const exited = child.exitCode !== null || child.signalCode !== null;
  const closed = exited ? Promise.resolve() : new Promise((resolve) => child.once("close", resolve));
  if (process.platform === "win32") {
    if (exited) return;
    await new Promise((resolve, reject) => {
      const killer = spawn("taskkill.exe", ["/PID", String(child.pid), "/T", "/F"], {
        windowsHide: true, stdio: "ignore",
      });
      killer.once("error", reject);
      killer.once("exit", resolve);
    });
  } else {
    try { process.kill(-child.pid, "SIGTERM"); }
    catch (error) { if (error.code !== "ESRCH") throw error; }
  }
  let timer;
  try {
    await Promise.race([closed, new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error(`子进程 ${child.pid} 未能停止。`)), 5000);
    })]);
  } finally { clearTimeout(timer); }
}
