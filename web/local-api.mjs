import { createHash } from "node:crypto";
import { createServer } from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { MemoryClient, MemoryCommandError } from "./memory-client.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const client = new MemoryClient(path.join(root, "skills", "super-memory", "scripts", "memory.py"));
const info = await client.run("init");
const memoryId = createHash("sha256").update(info.memory_root).digest("hex").slice(0, 16);
const port = Number(process.env.SUPER_MEMORY_API_PORT || 4318);
const webPort = Number(process.env.SUPER_MEMORY_WEB_PORT || 3000);
const origins = new Set(["http://127.0.0.1:" + webPort, "http://localhost:" + webPort]);
const statuses = { revision_conflict: 409, already_exists: 409, not_found: 404, busy: 423, not_initialized: 409, storage_error: 500, unavailable: 503, timeout: 504 };

function send(request, response, status, body) {
  const headers = {
    "Access-Control-Allow-Origin": request.headers.origin || "http://127.0.0.1:" + webPort,
    "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type,If-Match",
    "Content-Type": "application/json; charset=utf-8",
    Vary: "Origin",
  };
  if (body?.revision) headers.ETag = '"' + body.revision + '"';
  response.writeHead(status, headers);
  response.end(status === 204 ? undefined : JSON.stringify(body));
}

async function bodyJson(request) {
  const chunks = [];
  let size = 0;
  for await (const chunk of request) {
    size += chunk.length;
    if (size > 10_000_000) throw new MemoryCommandError("invalid_input", "请求内容过大。");
    chunks.push(chunk);
  }
  const value = JSON.parse(Buffer.concat(chunks).toString("utf8"));
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new MemoryCommandError("invalid_input", "请求内容必须是对象。");
  }
  return value;
}

function revision(request) {
  const value = request.headers["if-match"]?.replace(/^"|"$/g, "");
  if (!value || !/^[a-f0-9]{64}$/.test(value)) {
    throw new MemoryCommandError("invalid_input", "更新或删除必须提供读取时的 If-Match 内容版本号。");
  }
  return value;
}

createServer(async (request, response) => {
  if (request.headers.origin && !origins.has(request.headers.origin)) {
    response.writeHead(403, { "Content-Type": "application/json; charset=utf-8" });
    response.end(JSON.stringify({ error: { code: "forbidden", message: "不允许的请求来源。" } }));
    return;
  }
  if (request.method === "OPTIONS") return send(request, response, 204);
  try {
    const url = new URL(request.url, "http://127.0.0.1:" + port);
    if (request.method === "GET" && url.pathname === "/api/health") {
      return send(request, response, 200, { ok: true, app: "super-memory", apiVersion: 2, memoryId, memoryRoot: info.memory_root });
    }
    if (request.method === "GET" && url.pathname === "/api/rules") {
      return send(request, response, 200, await client.run("list"));
    }
    if (request.method === "GET" && url.pathname === "/api/export") {
      return send(request, response, 200, await client.run("export"));
    }
    if (request.method === "POST" && url.pathname === "/api/import") {
      const body = await bodyJson(request);
      if (!["skip", "replace"].includes(body.conflict)) throw new MemoryCommandError("invalid_input", "导入策略必须为 skip 或 replace。");
      return send(request, response, 200, await client.run("import", ["--conflict", body.conflict, "--reason", "用户通过管理台导入规则"], body.bundle));
    }
    if (request.method === "POST" && url.pathname === "/api/rules") {
      const body = await bodyJson(request);
      return send(request, response, 201, await client.run("create", ["--reason", "用户通过管理台新增规则"], body.rule));
    }
    const match = url.pathname.match(/^\/api\/rules\/([^/]+)$/);
    if (match) {
      const id = decodeURIComponent(match[1]);
      if (request.method === "GET") return send(request, response, 200, await client.run("get", [id]));
      if (request.method === "PUT") {
        const expected = revision(request);
        const body = await bodyJson(request);
        return send(request, response, 200, await client.run("update", [id, "--revision", expected, "--reason", "用户通过管理台编辑规则"], body.rule));
      }
      if (request.method === "DELETE") {
        return send(request, response, 200, await client.run("delete", [id, "--revision", revision(request), "--reason", "用户通过管理台删除规则"]));
      }
    }
    send(request, response, 404, { error: { code: "not_found", message: "接口不存在。" } });
  } catch (error) {
    const code = error.code || "invalid_input";
    send(request, response, statuses[code] || 400, { error: { code, message: error.message || "请求处理失败。" } });
  }
}).listen(port, "127.0.0.1", () => {
  console.log("记忆管理 API 已启动：http://127.0.0.1:" + port);
});
