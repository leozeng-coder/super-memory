import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync } from "node:fs";
import { createServer } from "node:http";
import os from "node:os";
import {
  appendFile,
  mkdtemp,
  mkdir,
  readFile,
  readdir,
  rename,
  rm,
  unlink,
  writeFile,
} from "node:fs/promises";
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
const MEMORY_CONFIG_PATH = path.join(MEMORY_ROOT, "memory.config.json");
const RULE_ROOT = path.join(MEMORY_ROOT, "graph", "rules");
const EVENT_PATH = path.join(MEMORY_ROOT, "history", "events.jsonl");
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
const VALID_STATUSES = new Set([
  "candidate",
  "active",
  "suspect",
  "deprecated",
  "superseded",
  "archived",
]);
const ID_PATTERN = /^RULE-[A-Z0-9][A-Z0-9-]*$/;

function directoryForStatus(status) {
  if (status === "active") return "active";
  if (status === "archived" || status === "superseded") return "archive";
  return "candidate";
}

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

async function findRuleFiles() {
  const files = [];
  for (const directory of ["active", "candidate", "archive"]) {
    const folder = path.join(RULE_ROOT, directory);
    await mkdir(folder, { recursive: true });
    const entries = await readdir(folder, { withFileTypes: true });
    for (const entry of entries) {
      if (entry.isFile() && entry.name.endsWith(".json")) {
        files.push(path.join(folder, entry.name));
      }
    }
  }
  return files;
}

async function readRules() {
  const rules = [];
  for (const file of await findRuleFiles()) {
    const rule = JSON.parse(await readFile(file, "utf8"));
    rule.lifecycle.updated_at ??=
      rule.lifecycle.last_validated_at || rule.lifecycle.created_at;
    rules.push(rule);
  }
  return rules.sort(
    (a, b) =>
      Number(b.status === "active") - Number(a.status === "active") ||
      a.title.localeCompare(b.title, "zh-CN"),
  );
}

async function readScopeHierarchy() {
  const config = JSON.parse(await readFile(MEMORY_CONFIG_PATH, "utf8"));
  return Array.isArray(config.scope_hierarchy) ? config.scope_hierarchy : [];
}

function normalizeScopeHierarchy(value) {
  if (!Array.isArray(value)) return [];
  return value.map((project) => {
    if (!project || typeof project !== "object" || !project.id?.trim()) {
      throw new Error("导入文件包含无效的项目作用域");
    }
    const modules = Array.isArray(project.modules) ? project.modules : [];
    return {
      id: project.id.trim(),
      label: String(project.label || project.id).trim(),
      created_at: project.created_at || new Date().toISOString(),
      updated_at: project.updated_at || new Date().toISOString(),
      modules: modules.map((module) => {
        if (!module || typeof module !== "object" || !module.id?.trim()) {
          throw new Error("导入文件包含无效的模块作用域");
        }
        return {
          id: module.id.trim(),
          label: String(module.label || module.id).trim(),
          created_at: module.created_at || new Date().toISOString(),
          updated_at: module.updated_at || new Date().toISOString(),
        };
      }),
    };
  });
}

async function writeScopeHierarchy(scopes) {
  const config = JSON.parse(await readFile(MEMORY_CONFIG_PATH, "utf8"));
  const temporaryPath = `${MEMORY_CONFIG_PATH}.tmp`;
  await writeFile(
    temporaryPath,
    `${JSON.stringify({ ...config, scope_hierarchy: scopes }, null, 2)}\n`,
    "utf8",
  );
  await rename(temporaryPath, MEMORY_CONFIG_PATH);
}

async function exportBundle() {
  return {
    format: "super-memory-export",
    schema_version: 1,
    exported_at: new Date().toISOString(),
    scope_hierarchy: await readScopeHierarchy(),
    rules: await readRules(),
  };
}

async function importBundle(payload) {
  const bundle = payload?.bundle;
  const overwrite = payload?.overwrite === true;
  if (
    !bundle ||
    bundle.format !== "super-memory-export" ||
    bundle.schema_version !== 1 ||
    !Array.isArray(bundle.rules)
  ) {
    throw new Error("不是有效的 Super Memory 导出文件");
  }
  if (bundle.rules.length > 10_000) throw new Error("一次最多导入 10000 条规则");

  const seenIds = new Set();
  for (const rule of bundle.rules) {
    validateRule(rule, rule?.id);
    if (seenIds.has(rule.id)) throw new Error(`导入文件中的规则 ID 重复：${rule.id}`);
    seenIds.add(rule.id);
  }
  await validateImportedRules(bundle.rules);

  const scopes = normalizeScopeHierarchy(bundle.scope_hierarchy);
  const imported = [];
  const skipped = [];
  for (const rule of bundle.rules) {
    const existing = await findRulePath(rule.id);
    if (existing && !overwrite) {
      skipped.push(rule.id);
      continue;
    }
    if (existing) {
      await updateRule(rule.id, rule);
    } else {
      await createRule(rule);
    }
    imported.push(rule.id);
  }
  if (scopes.length) await writeScopeHierarchy(scopes);
  return { imported, skipped };
}

function validateRule(rule, expectedId) {
  if (!rule || typeof rule !== "object") throw new Error("规则内容必须是对象");
  if (!ID_PATTERN.test(rule.id) || rule.id !== expectedId) {
    throw new Error("规则 ID 无效或不可修改");
  }
  if (!VALID_STATUSES.has(rule.status)) throw new Error("规则状态无效");
  if (!rule.title?.trim()) throw new Error("规则标题不能为空");
  if ((rule.scope?.projects?.length ?? 0) > 1 || (rule.scope?.modules?.length ?? 0) > 1) {
    throw new Error("作用域只能选择一个项目和一个服务模块");
  }
  if (rule.scope?.modules?.length && !rule.scope?.projects?.length) {
    throw new Error("服务模块必须归属于项目");
  }
  if (!rule.instruction?.do?.trim() || !rule.instruction?.avoid?.trim()) {
    throw new Error("应该怎么做和应该避免均不能为空");
  }
}

async function findRulePath(id) {
  const files = await findRuleFiles();
  return files.find((file) => path.basename(file, ".json") === id);
}

function runMemoryCommand(command, memoryRoot = MEMORY_ROOT) {
  const candidates = process.platform === "win32" ? ["python"] : ["python3", "python"];
  let lastError = "找不到 Python 运行环境";
  for (const executable of candidates) {
    const result = spawnSync(
      executable,
      ["-X", "utf8", MEMORY_CLI, "--memory-root", memoryRoot, command],
      { encoding: "utf8", windowsHide: true },
    );
    if (result.error?.code === "ENOENT") continue;
    if (result.status === 0) return;
    lastError = (result.stderr || result.stdout || "规则校验失败").trim();
    break;
  }
  throw new Error(lastError);
}

async function validateImportedRules(rules) {
  const temporaryRoot = await mkdtemp(path.join(os.tmpdir(), "super-memory-import-"));
  try {
    runMemoryCommand("init", temporaryRoot);
    for (const rule of rules) {
      const directory = path.join(
        temporaryRoot,
        "graph",
        "rules",
        directoryForStatus(rule.status),
      );
      await mkdir(directory, { recursive: true });
      await writeFile(
        path.join(directory, `${rule.id}.json`),
        `${JSON.stringify(rule, null, 2)}\n`,
        "utf8",
      );
    }
    runMemoryCommand("validate", temporaryRoot);
  } finally {
    await rm(temporaryRoot, { recursive: true, force: true });
  }
}

async function updateRule(id, rule) {
  const files = await findRuleFiles();
  const originalPath = files.find((file) => path.basename(file, ".json") === id);
  if (!originalPath) throw new Error("找不到需要编辑的规则");

  const originalContent = await readFile(originalPath, "utf8");
  const originalRule = JSON.parse(originalContent);
  const updatedRule = {
    ...rule,
    lifecycle: {
      ...rule.lifecycle,
      created_at: originalRule.lifecycle.created_at,
      updated_at: new Date().toISOString(),
    },
  };
  validateRule(updatedRule, id);
  const targetDirectory = path.join(RULE_ROOT, directoryForStatus(updatedRule.status));
  const targetPath = path.join(targetDirectory, `${id}.json`);
  const temporaryPath = `${targetPath}.tmp`;

  await mkdir(targetDirectory, { recursive: true });
  await writeFile(temporaryPath, `${JSON.stringify(updatedRule, null, 2)}\n`, "utf8");
  await rename(temporaryPath, targetPath);
  if (originalPath !== targetPath) await unlink(originalPath);

  try {
    runMemoryCommand("validate");
    runMemoryCommand("reindex");
  } catch (error) {
    await writeFile(originalPath, originalContent, "utf8");
    if (originalPath !== targetPath) await unlink(targetPath).catch(() => {});
    throw error;
  }

  const event = {
    event_id: `EVT-${Date.now()}-WEB-EDIT`,
    time: new Date().toISOString(),
    action: "updated",
    node_id: id,
    actor: "user",
    reason: "通过 Super Memory 管理台编辑规则",
  };
  await appendFile(EVENT_PATH, `${JSON.stringify(event)}\n`, "utf8");
  return updatedRule;
}

async function createRule(rule) {
  const now = new Date().toISOString();
  const createdRule = {
    ...rule,
    lifecycle: {
      ...rule.lifecycle,
      created_at: now,
      updated_at: now,
    },
  };
  validateRule(createdRule, createdRule.id);
  if (await findRulePath(createdRule.id)) throw new Error("规则 ID 已存在");

  const targetDirectory = path.join(RULE_ROOT, directoryForStatus(createdRule.status));
  const targetPath = path.join(targetDirectory, `${createdRule.id}.json`);
  const temporaryPath = `${targetPath}.tmp`;
  await mkdir(targetDirectory, { recursive: true });
  await writeFile(temporaryPath, `${JSON.stringify(createdRule, null, 2)}\n`, "utf8");
  await rename(temporaryPath, targetPath);

  try {
    runMemoryCommand("validate");
    runMemoryCommand("reindex");
  } catch (error) {
    await unlink(targetPath).catch(() => {});
    throw error;
  }

  await appendFile(
    EVENT_PATH,
    `${JSON.stringify({
      event_id: `EVT-${Date.now()}-WEB-CREATE`,
      time: new Date().toISOString(),
      action: "created",
      node_id: createdRule.id,
      actor: "user",
      reason: "通过 Super Memory 管理台新增规则",
    })}\n`,
    "utf8",
  );
  return createdRule;
}

async function deleteRule(id) {
  if (!ID_PATTERN.test(id)) throw new Error("规则 ID 无效");
  const sourcePath = await findRulePath(id);
  if (!sourcePath) throw new Error("找不到需要删除的规则");

  const trashDirectory = path.join(RULE_ROOT, "trash");
  const trashPath = path.join(trashDirectory, `${id}-${Date.now()}.json`);
  await mkdir(trashDirectory, { recursive: true });
  await rename(sourcePath, trashPath);

  try {
    runMemoryCommand("validate");
    runMemoryCommand("reindex");
  } catch (error) {
    await rename(trashPath, sourcePath);
    throw error;
  }

  await appendFile(
    EVENT_PATH,
    `${JSON.stringify({
      event_id: `EVT-${Date.now()}-WEB-DELETE`,
      time: new Date().toISOString(),
      action: "deleted-to-trash",
      node_id: id,
      actor: "user",
      reason: "通过 Super Memory 管理台删除规则并移入本地回收区",
      trash_path: path.relative(MEMORY_ROOT, trashPath).replaceAll("\\", "/"),
    })}\n`,
    "utf8",
  );
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
