import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { sampleRule } from "./api-fixture.mjs";

const project = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
let nextPort = 44000 + process.pid % 1000;

async function withApi(action) {
  const memoryRoot = await mkdtemp(path.join(os.tmpdir(), "super-memory-api-"));
  const port = nextPort++;
  const base = "http://127.0.0.1:" + port;
  const output = [];
  const child = spawn(process.execPath, [path.join(project, "web/local-api.mjs")], {
    env: { ...process.env, SUPER_MEMORY_HOME: memoryRoot, SUPER_MEMORY_API_PORT: String(port), SUPER_MEMORY_WEB_PORT: "3000" },
    windowsHide: true,
    stdio: ["ignore", "pipe", "pipe"],
  });
  let startupError;
  child.on("error", (error) => { startupError = error; });
  child.stdout.on("data", (chunk) => output.push(chunk.toString()));
  child.stderr.on("data", (chunk) => output.push(chunk.toString()));
  async function request(method, route, body, revision) {
    const response = await fetch(base + route, {
      method,
      headers: { "Content-Type": "application/json", ...(revision ? { "If-Match": revision } : {}) },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    return { status: response.status, body: await response.json(), etag: response.headers.get("etag") };
  }
  try {
    const deadline = Date.now() + 15000;
    while (true) {
      if (startupError) throw startupError;
      if (child.exitCode !== null) throw new Error(output.join(""));
      try {
        const response = await fetch(base + "/api/health", { signal: AbortSignal.timeout(500) });
        if (response.ok) break;
      } catch { /* 服务正在启动。 */ }
      if (Date.now() >= deadline) throw new Error("启动超时：" + output.join(""));
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
    await action({ request, base, memoryRoot });
  } finally {
    if (child.pid && child.exitCode === null) {
      const exited = new Promise((resolve) => child.once("exit", resolve));
      child.kill();
      await exited;
    }
    assert.equal(path.dirname(path.resolve(memoryRoot)), path.resolve(os.tmpdir()));
    await rm(memoryRoot, { recursive: true, force: true });
  }
}

test("API 使用内容版本号并保护证据、归档和删除", async () => withApi(async ({ request, base }) => {
  assert.equal((await request("GET", "/api/health")).body.apiVersion, 2);
  assert.deepEqual((await request("GET", "/api/rules")).body.records, []);
  const created = await request("POST", "/api/rules", { rule: sampleRule() });
  assert.equal(created.status, 201, JSON.stringify(created.body));
  assert.equal(created.body.revision.length, 64);
  assert.equal(created.etag, '"' + created.body.revision + '"');
  const rule = structuredClone(created.body.rule);
  rule.evidence = [{ ...rule.evidence[0], id: "EVIDENCE-API-NEW" }];
  rule.status = "archived";
  const url = "/api/rules/" + rule.id;
  assert.equal((await request("PUT", url, { rule })).status, 400);
  const updated = await request("PUT", url, { rule }, created.body.revision);
  assert.equal(updated.status, 200, JSON.stringify(updated.body));
  assert.equal(updated.body.rule.evidence.length, 2);
  assert.equal(updated.body.rule.lifecycle.last_validated_at, null);
  assert.equal((await request("GET", url)).body.revision, updated.body.revision);
  assert.equal((await request("DELETE", url, undefined, created.body.revision)).status, 409);
  assert.equal((await request("DELETE", url, undefined, updated.body.revision)).status, 200);
  assert.deepEqual((await request("GET", "/api/rules")).body.records, []);
  const denied = await fetch(base + "/api/health", { headers: { Origin: "https://example.invalid" } });
  assert.equal(denied.status, 403);
}));

test("API 并发编辑只接受同一版本中的一个修改", async () => withApi(async ({ request }) => {
  const created = (await request("POST", "/api/rules", { rule: sampleRule() })).body;
  const updates = await Promise.all([1, 2].map((index) => request("PUT", "/api/rules/" + created.rule.id, { rule: { ...created.rule, title: "并发修改 " + index } }, created.revision)));
  assert.deepEqual(updates.map((result) => result.status).sort(), [200, 409]);
  assert.equal((await request("GET", "/api/rules")).body.records.length, 1);
}));

test("API 导入整批校验并保留作用域和旧证据", async () => withApi(async ({ request }) => {
  const created = (await request("POST", "/api/rules", { rule: sampleRule() })).body;
  const exported = (await request("GET", "/api/export")).body;
  const invalid = { ...sampleRule(), id: "RULE-INVALID", confidence: "bad" };
  const valid = { ...sampleRule(), id: "RULE-NEW" };
  const rejected = await request("POST", "/api/import", { bundle: { ...exported, rules: [valid, invalid] }, conflict: "replace" });
  assert.equal(rejected.status, 400);
  assert.equal((await request("GET", "/api/rules")).body.records.length, 1);
  const incoming = structuredClone(created.rule);
  incoming.evidence[0].id = "EVIDENCE-IMPORT";
  const bundle = { ...exported, rules: [incoming], scope_hierarchy: [{ id: " server ", modules: [{ id: " scene " }] }] };
  const imported = await request("POST", "/api/import", { bundle, conflict: "replace" });
  assert.equal(imported.status, 200, JSON.stringify(imported.body));
  const snapshot = (await request("GET", "/api/rules")).body;
  assert.equal(snapshot.records[0].rule.evidence.length, 2);
  assert.equal(snapshot.scopes[0].label, "server");
  assert.equal(snapshot.scopes[0].modules[0].id, "scene");
}));

test("Python 等待规则锁时 HTTP 健康检查仍能响应", async () => withApi(async ({ request, base, memoryRoot }) => {
  const code = "import sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); from super_memory.repository import RuleStore; store=RuleStore(Path(sys.argv[2]));\nwith store.session():\n print('locked',flush=True)\n sys.stdin.readline()";
  const locker = spawn(process.platform === "win32" ? "python" : "python3", ["-B", "-X", "utf8", "-c", code, path.join(project, "skills/super-memory/scripts"), memoryRoot], { windowsHide: true, stdio: ["pipe", "pipe", "pipe"] });
  const exited = new Promise((resolve) => locker.once("exit", resolve));
  let pending;
  try {
    await new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error("等待测试锁超时")), 5000);
      locker.stdout.once("data", () => { clearTimeout(timeout); resolve(); });
      locker.once("error", (error) => { clearTimeout(timeout); reject(error); });
    });
    pending = request("POST", "/api/rules", { rule: sampleRule() });
    await new Promise((resolve) => setTimeout(resolve, 150));
    const health = await fetch(base + "/api/health", { signal: AbortSignal.timeout(1500) });
    assert.equal(health.status, 200);
  } finally {
    locker.stdin.end();
    await exited;
    if (pending) assert.equal((await pending).status, 201);
  }
}));
