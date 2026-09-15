import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync } from "node:fs";
import { createServer } from "node:http";
import os from "node:os";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const WEB_ROOT = path.dirname(fileURLToPath(import.meta.url));
const APP_ROOT = path.resolve(WEB_ROOT, "..");
const MEMORY_ROOT = path.resolve(
  process.env.SUPER_MEMORY_HOME ||
    (existsSync(path.join(APP_ROOT, "memory.config.json"))
      ? APP_ROOT
      : path.join(os.homedir(), ".super-memory")),
);
const MEMORY_CLI = path.join(
  APP_ROOT,
  "skills",
  "super-memory",
  "scripts",
  "memory_cli.py",
);
const PORT = Number(process.env.SUPER_MEMORY_API_PORT || 4318);
const WEB_PORT = Number(process.env.SUPER_MEMORY_WEB_PORT || 3000);
const MEMORY_ID = createHash("sha256")
  .update(MEMORY_ROOT)
  .digest("hex")
  .slice(0, 16);
const ALLOWED_ORIGINS = new Set([
  `http://127.0.0.1:${WEB_PORT}`,
  `http://localhost:${WEB_PORT}`,
]);
function send(request, response, status, body) {
  const requestOrigin = request.headers.origin;
  const allowedOrigin = ALLOWED_ORIGINS.has(requestOrigin)
    ? requestOrigin
    : `http://127.0.0.1:${WEB_PORT}`;
  response.writeHead(status, {
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Allow-Methods": "GET,PUT,POST,DELETE,OPTIONS",
    "Access-Control-Allow-Origin": allowedOrigin,
    "Content-Type": "application/json; charset=utf-8",
    Vary: "Origin",
  });
  response.end(status === 204 ? undefined : JSON.stringify(body));
}

async function bodyJson(request) {
  let body = "";
  for await (const chunk of request) {
    body += chunk;
    if (body.length > 10_000_000) throw new Error("请求内容过大");
  }
  return JSON.parse(body || "{}");
}

function runMemoryCommand(command, args = [], jsonOutput = false) {
  const candidates = process.platform === "win32" ? ["python"] : ["python3", "python"];
  let lastError = "找不到 Python 运行环境";
  for (const executable of candidates) {
    const result = spawnSync(
      executable,
      ["-X", "utf8", MEMORY_CLI, "--memory-root", MEMORY_ROOT, command, ...args],
      { encoding: "utf8", windowsHide: true, maxBuffer: 32 * 1024 * 1024 },
    );
    if (result.error?.code === "ENOENT") continue;
    if (result.status === 0) return jsonOutput ? JSON.parse(result.stdout) : result.stdout;
    lastError = (result.stderr || result.stdout || result.error?.message || "规则操作失败").trim();
    break;
  }
  throw new Error(lastError);
}

async function withInput(value, action) {
  const temporaryRoot = await mkdtemp(path.join(os.tmpdir(), "super-memory-input-"));
  const inputPath = path.join(temporaryRoot, "input.json");
  try {
    await writeFile(inputPath, JSON.stringify(value), "utf8");
    return action(inputPath);
  } finally {
    if (path.dirname(path.resolve(temporaryRoot)) !== path.resolve(os.tmpdir())) {
      throw new Error("临时目录超出预期范围，清理已停止。");
    }
    await rm(temporaryRoot, { recursive: true, force: true });
  }
}

async function readRules() {
  return runMemoryCommand("list", [], true).rules.sort(
    (a, b) => Number(b.status === "active") - Number(a.status === "active") || a.title.localeCompare(b.title, "zh-CN"),
  );
}

async function readScopeHierarchy() {
  return runMemoryCommand("list", ["--export"], true).scope_hierarchy;
}

async function exportBundle() {
  return runMemoryCommand("list", ["--export"], true);
}

async function importBundle(payload) {
  const args = payload?.overwrite === true ? ["--overwrite"] : [];
  return withInput(payload?.bundle ?? null, (inputPath) => runMemoryCommand("import", ["--input", inputPath, ...args], true));
}

async function updateRule(id, rule) {
  if (!rule || rule.id !== id) throw new Error("规则 ID 无效或不可修改");
  const expected = rule.lifecycle?.updated_at ?? "null";
  return withInput(rule, (inputPath) => runMemoryCommand("save", [
    "--input", inputPath, "--mode", "update", "--expected-updated-at", String(expected),
    "--reason", "通过 Super Memory 管理台编辑规则", "--json",
  ], true));
}

async function createRule(rule) {
  return withInput(rule, (inputPath) => runMemoryCommand("save", [
    "--input", inputPath, "--mode", "create", "--reason", "通过 Super Memory 管理台新增规则", "--json",
  ], true));
}

async function deleteRule(id) {
  return runMemoryCommand("delete", ["--id", id, "--reason", "通过 Super Memory 管理台删除规则并移入本地回收区"], true);
}


runMemoryCommand("init");

createServer(async (request, response) => {
  const origin = request.headers.origin;
  if (origin && !ALLOWED_ORIGINS.has(origin)) {
    response.writeHead(403, { "Content-Type": "application/json; charset=utf-8" });
    response.end(JSON.stringify({ error: "不允许的请求来源" }));
    return;
  }
  if (request.method === "OPTIONS") {
    send(request, response, 204, {});
    return;
  }

  try {
    const url = new URL(request.url, `http://${request.headers.host}`);
    if (request.method === "GET" && url.pathname === "/api/health") {
      send(request, response, 200, {
        ok: true,
        app: "super-memory",
        storage: "local",
        memoryId: MEMORY_ID,
      });
      return;
    }
    if (request.method === "GET" && url.pathname === "/api/rules") {
      send(request, response, 200, { rules: await readRules() });
      return;
    }
    if (request.method === "GET" && url.pathname === "/api/scopes") {
      send(request, response, 200, { scopes: await readScopeHierarchy() });
      return;
    }
    if (request.method === "GET" && url.pathname === "/api/export") {
      send(request, response, 200, await exportBundle());
      return;
    }
    if (request.method === "POST" && url.pathname === "/api/import") {
      send(request, response, 200, await importBundle(await bodyJson(request)));
      return;
    }
    if (request.method === "POST" && url.pathname === "/api/rules") {
      const rule = await bodyJson(request);
      send(request, response, 201, { rule: await createRule(rule) });
      return;
    }
    if (request.method === "PUT" && url.pathname.startsWith("/api/rules/")) {
      const id = decodeURIComponent(url.pathname.slice("/api/rules/".length));
      const rule = await bodyJson(request);
      send(request, response, 200, { rule: await updateRule(id, rule) });
      return;
    }
    if (request.method === "DELETE" && url.pathname.startsWith("/api/rules/")) {
      const id = decodeURIComponent(url.pathname.slice("/api/rules/".length));
      await deleteRule(id);
      send(request, response, 200, { ok: true });
      return;
    }
    send(request, response, 404, { error: "接口不存在" });
  } catch (error) {
    send(request, response, 400, {
      error: error instanceof Error ? error.message : "请求处理失败",
    });
  }
}).listen(PORT, "127.0.0.1", () => {
  console.log(`记忆管理 API 已启动：http://127.0.0.1:${PORT}`);
});
