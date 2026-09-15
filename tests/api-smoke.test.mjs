import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const apiScript = path.join(projectRoot, "web", "local-api.mjs");

function sampleRule() {
  return {
    id: "RULE-SUPER-MEMORY-SMOKE",
    type: "Rule",
    title: "修改后运行相关测试",
    status: "active",
    priority: "high",
    confidence: 0.9,
    scope: {
      level: "global",
      languages: ["JavaScript"],
      technologies: ["Node.js"],
      projects: [],
      modules: [],
    },
    trigger: {
      description: "修改本地接口后需要验证读写流程",
      keywords: ["api", "test"],
    },
    instruction: {
      do: "运行与修改相关的接口测试。",
      avoid: "不要在未验证时交付修改。",
    },
    rationale: {
      problem: "未验证的接口修改可能破坏规则读写。",
      root_cause: "缺少端到端验证。",
    },
    exceptions: [],
    relations: {
      conflicts_with: [],
      supersedes: [],
      replacement: null,
    },
    evidence: [
      {
        id: "EVIDENCE-SMOKE-1",
        source: "user-instruction",
        summary: "自动化测试验证本地接口的新增、导出、删除和导入。",
        project: null,
        commit: null,
        files: [],
        observed_at: "2026-08-26T00:00:00+00:00",
      },
    ],
    lifecycle: {
      created_at: "2026-08-26T00:00:00+00:00",
      updated_at: null,
      last_validated_at: null,
      review_after: null,
      success_count: 0,
      violation_count: 0,
      exception_count: 0,
    },
    tags: ["接口", "测试"],
  };
}

async function waitForApi(baseUrl, child, output) {
  const deadline = Date.now() + 15_000;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) throw new Error(`API 提前退出：${output.join("")}`);
    try {
      const response = await fetch(`${baseUrl}/api/health`);
      if (response.ok) return await response.json();
    } catch {
      // 服务仍在启动。
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`等待 API 超时：${output.join("")}`);
}

test("本地 API 共用一套规则并支持导入导出", async () => {
  const memoryRoot = await mkdtemp(path.join(os.tmpdir(), "super-memory-api-"));
  const port = 44000 + (process.pid % 1000);
  const baseUrl = `http://127.0.0.1:${port}`;
  const output = [];
  const child = spawn(process.execPath, [apiScript], {
    env: {
      ...process.env,
      SUPER_MEMORY_HOME: memoryRoot,
      SUPER_MEMORY_API_PORT: String(port),
      SUPER_MEMORY_WEB_PORT: "3000",
    },
    stdio: ["ignore", "pipe", "pipe"],
    windowsHide: true,
  });
  child.stdout.on("data", (chunk) => output.push(chunk.toString()));
  child.stderr.on("data", (chunk) => output.push(chunk.toString()));

  try {
    const health = await waitForApi(baseUrl, child, output);
    assert.equal(health.app, "super-memory");

    const empty = await (await fetch(`${baseUrl}/api/rules`)).json();
    assert.deepEqual(empty.rules, []);

    const createdResponse = await fetch(`${baseUrl}/api/rules`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(sampleRule()),
    });
    const created = await createdResponse.json();
    assert.equal(createdResponse.status, 201, JSON.stringify(created));
    assert.equal(created.rule.lifecycle.last_validated_at, null);

    const updatedDraft = structuredClone(created.rule);
    updatedDraft.title = "管理台编辑追加证据";
    updatedDraft.evidence = [{ ...updatedDraft.evidence[0], id: "EVIDENCE-SMOKE-2", summary: "追加新的确认依据。" }];
    const updateResponse = await fetch(`${baseUrl}/api/rules/${updatedDraft.id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(updatedDraft),
    });
    const updated = await updateResponse.json();
    assert.equal(updateResponse.status, 200, JSON.stringify(updated));
    assert.deepEqual(updated.rule.evidence.map((item) => item.id), ["EVIDENCE-SMOKE-1", "EVIDENCE-SMOKE-2"]);
    assert.equal(updated.rule.lifecycle.created_at, created.rule.lifecycle.created_at);
    assert.equal(updated.rule.lifecycle.last_validated_at, null);

    const staleResponse = await fetch(`${baseUrl}/api/rules/${updatedDraft.id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...created.rule, title: "旧页面覆盖" }),
    });
    assert.equal(staleResponse.status, 400);
    assert.match((await staleResponse.json()).error, /已被其他操作修改/);

    const archivedResponse = await fetch(`${baseUrl}/api/rules/${updatedDraft.id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...updated.rule, status: "archived" }),
    });
    const archived = await archivedResponse.json();
    assert.equal(archivedResponse.status, 200, JSON.stringify(archived));
    assert.equal(archived.rule.status, "archived");

    const bundle = await (await fetch(`${baseUrl}/api/export`)).json();
    assert.equal(bundle.format, "super-memory-export");
    assert.equal(bundle.rules.length, 1);

    const deletedResponse = await fetch(
      `${baseUrl}/api/rules/${encodeURIComponent(sampleRule().id)}`,
      { method: "DELETE" },
    );
    assert.equal(deletedResponse.status, 200, await deletedResponse.text());

    const importedResponse = await fetch(`${baseUrl}/api/import`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ bundle: { ...bundle, scope_hierarchy: [{ id: " server ", modules: [{ id: " svr_scene " }] }] }, overwrite: false }),
    });
    const imported = await importedResponse.json();
    assert.equal(importedResponse.status, 200, JSON.stringify(imported));
    assert.deepEqual(imported.imported, [sampleRule().id]);

    const restored = await (await fetch(`${baseUrl}/api/rules`)).json();
    assert.equal(restored.rules.length, 1);
    assert.equal(restored.rules[0].status, "archived");
    assert.equal(restored.rules[0].evidence.length, 2);
    const scopes = await (await fetch(`${baseUrl}/api/scopes`)).json();
    assert.equal(scopes.scopes[0].id, "server");
    assert.equal(scopes.scopes[0].label, "server");
    assert.equal(scopes.scopes[0].modules[0].id, "svr_scene");

    const validImport = { ...sampleRule(), id: "RULE-IMPORT-FIRST" };
    const invalidImport = { ...sampleRule(), id: "RULE-IMPORT-INVALID", confidence: "invalid" };
    const rejectedImport = await fetch(`${baseUrl}/api/import`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ bundle: { ...bundle, rules: [validImport, invalidImport] } }),
    });
    assert.equal(rejectedImport.status, 400);
    const afterRejectedImport = await (await fetch(`${baseUrl}/api/rules`)).json();
    assert.equal(afterRejectedImport.rules.length, 1);

    const rejected = await fetch(`${baseUrl}/api/health`, {
      headers: { Origin: "https://example.invalid" },
    });
    assert.equal(rejected.status, 403);
  } finally {
    const exited = child.exitCode !== null ? Promise.resolve() : new Promise((resolve) => child.once("exit", resolve));
    child.kill();
    await exited;
    assert.equal(path.dirname(path.resolve(memoryRoot)), path.resolve(os.tmpdir()));
    await rm(memoryRoot, { recursive: true, force: true });
  }
});
