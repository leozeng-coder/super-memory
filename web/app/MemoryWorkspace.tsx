"use client";

import {
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  MiniMap,
  Position,
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps,
  useEdgesState,
  useNodesState,
} from "@xyflow/react";
import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  Boxes,
  BrainCircuit,
  Check,
  ChevronRight,
  CircleDot,
  Download,
  FileText,
  GitBranch,
  Globe2,
  LayoutGrid,
  Link2,
  List,
  Network,
  Pencil,
  Plus,
  RefreshCw,
  Save,
  Search,
  ShieldCheck,
  Sparkles,
  Tag,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

type Status =
  | "candidate"
  | "active"
  | "suspect"
  | "deprecated"
  | "superseded"
  | "archived";

type Rule = {
  id: string;
  type: "Rule";
  title: string;
  status: Status;
  priority: "low" | "medium" | "high" | "critical";
  confidence: number;
  scope: {
    level: "global" | "language" | "technology" | "project" | "module" | "feature";
    languages?: string[];
    technologies?: string[];
    projects?: string[];
    modules?: string[];
    features?: string[];
  };
  trigger: { description: string; keywords: string[]; always_apply?: boolean };
  instruction: { do: string; avoid: string };
  rationale: { problem: string; root_cause: string };
  exceptions: Array<{ condition: string; requirement: string }>;
  relations: {
    conflicts_with?: string[];
    supersedes?: string[];
    replacement?: string | null;
  };
  evidence: Array<{
    id: string;
    source: string;
    summary: string;
    project?: string | null;
    commit?: string | null;
    files?: string[];
    observed_at: string;
  }>;
  lifecycle: {
    created_at: string;
    updated_at?: string | null;
    last_validated_at?: string | null;
    review_after?: string | null;
    success_count?: number;
    violation_count?: number;
    exception_count?: number;
  };
  tags: string[];
};

type RuleRecord = { rule: Rule; revision: string };

type ViewMode = "graph" | "list";
type GraphDisplayMode = "level" | "all";
type NodeKind = "core" | "scope" | "rule";
type KnowledgeNodeData = {
  label: string;
  caption: string;
  kind: NodeKind;
  parentId?: string;
  scopeKey?: string;
  createdAt: string;
  updatedAt: string;
  status?: Status;
  priority?: Rule["priority"];
  ruleId?: string;
};
type KnowledgeNode = Node<KnowledgeNodeData>;
type ScopeCatalogFeature = {
  id: string;
  label: string;
  created_at: string;
  updated_at: string;
  aliases?: string[];
  paths?: string[];
};
type ScopeCatalogModule = ScopeCatalogFeature & { features: ScopeCatalogFeature[] };
type ScopeCatalogProject = {
  id: string;
  label: string;
  created_at: string;
  updated_at: string;
  modules: ScopeCatalogModule[];
};

const API = process.env.NEXT_PUBLIC_SUPER_MEMORY_API_URL || "http://127.0.0.1:4318/api";
const statusLabel: Record<Status, string> = {
  active: "已生效",
  candidate: "候选",
  suspect: "待复查",
  deprecated: "已弃用",
  superseded: "已替代",
  archived: "已归档",
};
const priorityLabel: Record<Rule["priority"], string> = {
  critical: "关键",
  high: "高",
  medium: "中",
  low: "低",
};
const scopeLabel: Record<Rule["scope"]["level"], string> = {
  global: "全局",
  language: "语言",
  technology: "技术",
  project: "项目",
  module: "模块",
  feature: "功能模块",
};

function formatDate(value?: string | null) {
  if (!value) return "暂无";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "short",
    day: "numeric",
  }).format(new Date(value));
}

function formatDateTime(value?: string | null) {
  if (!value) return "暂无";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function ruleUpdatedAt(rule: Rule) {
  return rule.lifecycle.updated_at || rule.lifecycle.last_validated_at || rule.lifecycle.created_at;
}

function timeRange(rules: Rule[]) {
  const created = rules.map((rule) => rule.lifecycle.created_at).filter(Boolean).sort();
  const updated = rules.map(ruleUpdatedAt).filter(Boolean).sort();
  const fallback = new Date().toISOString();
  return {
    createdAt: created[0] || fallback,
    updatedAt: updated.at(-1) || created.at(-1) || fallback,
  };
}

function normalizeProjectName(value?: string) {
  return value?.trim() || "";
}

function ruleProject(rule: Rule) {
  return normalizeProjectName(rule.scope.projects?.[0]);
}

function ruleModule(rule: Rule) {
  return rule.scope.modules?.[0]?.trim() || "";
}

function ruleFeature(rule: Rule) {
  return rule.scope.features?.[0]?.trim() || "";
}

function projectScopeId(project: string) {
  return `project:${project}`;
}

function moduleScopeId(project: string, moduleName: string) {
  return `module:${project}/${moduleName}`;
}

function featureScopeId(project: string, moduleName: string, feature: string) {
  return `feature:${project}/${moduleName}/${feature}`;
}

function scopeSummary(rule: Rule) {
  const project = ruleProject(rule);
  const moduleName = ruleModule(rule);
  const path = [project || "全部", moduleName, ruleFeature(rule)].filter(Boolean).join(" › ");
  const details = Array.from(new Set([
    ...(rule.scope.languages ?? []),
    ...(rule.scope.technologies ?? []),
  ])).filter(Boolean);
  return [path, ...details].join(" · ");
}

function ruleMatchesScope(rule: Rule, scopeId: string) {
  if (scopeId === "all") return true;
  const project = ruleProject(rule);
  const moduleName = ruleModule(rule);
  if (scopeId.startsWith("project:")) {
    return project === scopeId.slice("project:".length);
  }
  if (scopeId.startsWith("module:")) {
    return `${project}/${moduleName}` === scopeId.slice("module:".length);
  }
  if (scopeId.startsWith("feature:")) {
    return `${project}/${moduleName}/${ruleFeature(rule)}` === scopeId.slice("feature:".length);
  }
  return false;
}

function KnowledgeNodeCard({ data }: NodeProps<KnowledgeNode>) {
  const icon =
    data.kind === "core" ? (
      <BrainCircuit size={24} />
    ) : data.kind === "scope" ? (
      <Boxes size={20} />
    ) : data.status === "active" ? (
      <ShieldCheck size={18} />
    ) : data.status === "candidate" ? (
      <Sparkles size={18} />
    ) : data.status === "suspect" || data.status === "deprecated" ? (
      <AlertTriangle size={18} />
    ) : (
      <GitBranch size={18} />
    );
  return (
    <div
      className={`knowledge-node node-${data.kind} node-${data.status ?? "default"}`}
      title={`创建：${formatDateTime(data.createdAt)}\n更新：${formatDateTime(data.updatedAt)}`}
    >
      <Handle type="target" position={Position.Top} />
      <span className="node-orb">{icon}</span>
      <span className="node-copy">
        <strong>{data.label}</strong>
        <small>{data.caption}</small>
        <small className="node-time">更新于 {formatDate(data.updatedAt)}</small>
      </span>
      {data.kind === "rule" && <ChevronRight size={14} className="node-arrow" />}
      <Handle type="source" position={Position.Bottom} />
    </div>
  );
}

const nodeTypes = { knowledge: KnowledgeNodeCard };

type HierarchyItem = {
  id: string;
  kind: NodeKind;
  label: string;
  caption: string;
  parentId?: string;
  scopeKey?: string;
  rule?: Rule;
  sourceRules: Rule[];
  children: HierarchyItem[];
  createdAt?: string;
  updatedAt?: string;
};

function buildScopeHierarchy(rules: Rule[], scopeCatalog: ScopeCatalogProject[]): HierarchyItem {
  const root: HierarchyItem = {
    id: "memory-core",
    kind: "core",
    label: "全部",
    caption: `${rules.length} 条记忆`,
    sourceRules: rules,
    children: [],
  };
  const projects = Array.from(new Set(rules.map(ruleProject).filter(Boolean)))
    .sort((a, b) => a.localeCompare(b, "zh-CN"));

  projects.forEach((project) => {
    const projectRules = rules.filter((rule) => ruleProject(rule) === project);
    const projectId = projectScopeId(project);
    const projectCatalog = scopeCatalog.find((item) => item.id === project);
    const modules = Array.from(new Set(projectRules.map(ruleModule).filter(Boolean)))
      .sort((a, b) => a.localeCompare(b, "zh-CN"));
    const projectNode: HierarchyItem = {
      id: projectId,
      kind: "scope",
      label: project,
      caption: `${projectRules.length} 条记忆 · ${modules.length} 个模块`,
      parentId: root.id,
      scopeKey: projectId,
      sourceRules: projectRules,
      children: [],
      createdAt: projectCatalog?.created_at,
      updatedAt: projectCatalog?.updated_at,
    };

    modules.forEach((module) => {
      const moduleRules = projectRules.filter((rule) => ruleModule(rule) === module);
      const moduleId = moduleScopeId(project, module);
      const moduleCatalog = projectCatalog?.modules.find((item) => item.id === module);
      const moduleNode: HierarchyItem = {
        id: moduleId,
        kind: "scope",
        label: module,
        caption: `${moduleRules.length} 条记忆 · 点击下钻`,
        parentId: projectId,
        scopeKey: moduleId,
        sourceRules: moduleRules,
        createdAt: moduleCatalog?.created_at,
        updatedAt: moduleCatalog?.updated_at,
        children: moduleRules.filter((rule) => !ruleFeature(rule)).map((rule) => ({
          id: `rule-${rule.id}`,
          kind: "rule",
          label: rule.title,
          caption: `${statusLabel[rule.status]} · ${priorityLabel[rule.priority]}优先级`,
          parentId: moduleId,
          rule,
          sourceRules: [rule],
          children: [],
        })),
      };
      const features = Array.from(new Set(moduleRules.map(ruleFeature).filter(Boolean)))
        .sort((a, b) => a.localeCompare(b, "zh-CN"));
      for (const feature of features) {
        const featureRules = moduleRules.filter((rule) => ruleFeature(rule) === feature);
        const featureId = featureScopeId(project, module, feature);
        const featureCatalog = moduleCatalog?.features.find((item) => item.id === feature);
        moduleNode.children.unshift({
          id: featureId, kind: "scope", label: featureCatalog?.label || feature,
          caption: `${featureRules.length} 条功能记忆 · 点击下钻`, parentId: moduleId,
          scopeKey: featureId, sourceRules: featureRules,
          createdAt: featureCatalog?.created_at, updatedAt: featureCatalog?.updated_at,
          children: featureRules.map((rule) => ({
            id: `rule-${rule.id}`, kind: "rule", label: rule.title,
            caption: `${statusLabel[rule.status]} · ${priorityLabel[rule.priority]}优先级`,
            parentId: featureId, rule, sourceRules: [rule], children: [],
          })),
        });
      }
      projectNode.children.push(moduleNode);
    });

    projectNode.children.push(...projectRules
      .filter((rule) => !ruleModule(rule))
      .map((rule) => ({
        id: `rule-${rule.id}`,
        kind: "rule" as const,
        label: rule.title,
        caption: `${statusLabel[rule.status]} · ${priorityLabel[rule.priority]}优先级`,
        parentId: projectId,
        rule,
        sourceRules: [rule],
        children: [],
      })));
    root.children.push(projectNode);
  });

  root.children.push(...rules
    .filter((rule) => !ruleProject(rule))
    .map((rule) => ({
      id: `rule-${rule.id}`,
      kind: "rule" as const,
      label: rule.title,
      caption: `${statusLabel[rule.status]} · ${priorityLabel[rule.priority]}优先级`,
      parentId: root.id,
      rule,
      sourceRules: [rule],
      children: [],
    })));
  return root;
}

function findHierarchyItem(root: HierarchyItem, id: string): HierarchyItem | undefined {
  if (root.id === id) return root;
  for (const child of root.children) {
    const match = findHierarchyItem(child, id);
    if (match) return match;
  }
  return undefined;
}

function hierarchyBreadcrumb(scopeId: string) {
  if (scopeId.startsWith("project:")) return [scopeId.slice("project:".length)];
  if (scopeId.startsWith("module:")) {
    return scopeId.slice("module:".length).split("/");
  }
  if (scopeId.startsWith("feature:")) {
    return scopeId.slice("feature:".length).split("/");
  }
  return [];
}

function buildGraph(root: HierarchyItem, displayMode: GraphDisplayMode, focusedScope: string) {
  const nodes: KnowledgeNode[] = [];
  const edges: Edge[] = [];
  const center = { x: 620, y: 410 };

  const addNode = (item: HierarchyItem, position: { x: number; y: number }) => {
    const nodeTime = timeRange(item.sourceRules);
    const scopeCreatedAt = item.sourceRules.length
      ? [item.createdAt, nodeTime.createdAt].filter(Boolean).sort()[0]
      : item.createdAt || nodeTime.createdAt;
    const scopeUpdatedAt = item.sourceRules.length
      ? [item.updatedAt, nodeTime.updatedAt].filter(Boolean).sort().at(-1)
      : item.updatedAt || nodeTime.updatedAt;
    nodes.push({
      id: item.id,
      type: "knowledge",
      position,
      data: {
        kind: item.kind,
        label: item.label,
        caption: item.caption,
        parentId: item.parentId,
        scopeKey: item.scopeKey,
        createdAt: item.rule?.lifecycle.created_at || scopeCreatedAt || nodeTime.createdAt,
        updatedAt: item.rule ? ruleUpdatedAt(item.rule) : scopeUpdatedAt || nodeTime.updatedAt,
        status: item.rule?.status,
        priority: item.rule?.priority,
        ruleId: item.rule?.id,
      },
    });
  };

  const addEdge = (parent: HierarchyItem, child: HierarchyItem) => {
    edges.push({
      id: `edge-${parent.id}-${child.id}`,
      source: parent.id,
      target: child.id,
      animated: child.rule ? child.rule.status === "active" : true,
      className: child.rule ? `edge-rule edge-${child.rule.status}` : "edge-core",
    });
  };

  if (displayMode === "level") {
    const current = focusedScope ? findHierarchyItem(root, focusedScope) || root : root;
    addNode(current, center);
    current.children.forEach((child, index) => {
      const angle = (Math.PI * 2 * index) / Math.max(current.children.length, 1) - Math.PI / 2;
      const radiusX = current.children.length <= 4 ? 390 : 465;
      const radiusY = current.children.length <= 4 ? 285 : 350;
      addNode(child, {
        x: center.x + Math.cos(angle) * radiusX,
        y: center.y + Math.sin(angle) * radiusY,
      });
      addEdge(current, child);
    });
    return { nodes, edges };
  }

  addNode(root, center);
  const leafWeight = (item: HierarchyItem): number =>
    item.children.length ? item.children.reduce((sum, child) => sum + leafWeight(child), 0) : 1;
  const placeChildren = (parent: HierarchyItem, startAngle: number, endAngle: number, depth: number) => {
    const totalWeight = parent.children.reduce((sum, child) => sum + leafWeight(child), 0);
    let cursor = startAngle;
    parent.children.forEach((child) => {
      const span = totalWeight ? (endAngle - startAngle) * leafWeight(child) / totalWeight : 0;
      const angle = cursor + span / 2;
      addNode(child, {
        x: center.x + Math.cos(angle) * depth * 340,
        y: center.y + Math.sin(angle) * depth * 275,
      });
      addEdge(parent, child);
      placeChildren(child, cursor, cursor + span, depth + 1);
      cursor += span;
    });
  };
  placeChildren(root, -Math.PI * 1.5, Math.PI * 0.5, 1);

  return { nodes, edges };
}

function StatusBadge({ status }: { status: Status }) {
  return (
    <span className={`status-badge status-${status}`}>
      <CircleDot size={11} />
      {statusLabel[status]}
    </span>
  );
}

function newRuleDraft(scopeId = ""): Rule {
  const [project, moduleName, feature] = hierarchyBreadcrumb(scopeId);
  const timestamp = Date.now();
  const now = new Date().toISOString();
  return {
    id: `RULE-CUSTOM-${timestamp}`,
    type: "Rule",
    title: "",
    status: "candidate",
    priority: "medium",
    confidence: 0.7,
    scope: {
      level: feature ? "feature" : moduleName ? "module" : project ? "project" : "global",
      languages: [],
      technologies: [],
      projects: project ? [project] : [],
      modules: moduleName ? [moduleName] : [],
      features: feature ? [feature] : [],
    },
    trigger: { description: "", keywords: [] },
    instruction: { do: "", avoid: "" },
    rationale: { problem: "待补充", root_cause: "待补充" },
    exceptions: [],
    relations: { conflicts_with: [], supersedes: [], replacement: null },
    evidence: [
      {
        id: `EVIDENCE-${timestamp}`,
        source: "user-instruction",
        summary: "用户通过 Super Memory 管理台新增规则。",
        project: null,
        commit: null,
        files: [],
        observed_at: now,
      },
    ],
    lifecycle: {
      created_at: now,
      updated_at: now,
      last_validated_at: null,
      review_after: null,
      success_count: 0,
      violation_count: 0,
      exception_count: 0,
    },
    tags: [],
  };
}

function RuleEditor({
  initial,
  revision,
  mode,
  scopeCatalog,
  onClose,
  onSaved,
}: {
  initial: Rule;
  revision?: string;
  mode: "create" | "edit";
  scopeCatalog: ScopeCatalogProject[];
  onClose: () => void;
  onSaved: (record: RuleRecord) => void;
}) {
  const [draft, setDraft] = useState(initial);
  const [expectedRevision] = useState(revision);
  const [keywordText, setKeywordText] = useState(initial.trigger.keywords.join("，"));
  const [tagText, setTagText] = useState(initial.tags.join("，"));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  async function save() {
    if (!draft.title.trim() || !draft.instruction.do.trim() || !draft.instruction.avoid.trim()) {
      setError("请完整填写规则标题、应该怎么做和应该避免。");
      return;
    }
    if (!draft.trigger.description.trim() || !draft.trigger.keywords.length) {
      setError("请填写触发条件和至少一个检索关键词。");
      return;
    }
    setSaving(true);
    setError("");
    try {
      const response = await fetch(
        mode === "create" ? `${API}/rules` : `${API}/rules/${encodeURIComponent(draft.id)}`,
        {
          method: mode === "create" ? "POST" : "PUT",
          headers: { "Content-Type": "application/json", ...(mode === "edit" && expectedRevision ? { "If-Match": expectedRevision } : {}) },
          body: JSON.stringify({ rule: draft }),
        },
      );
      const result = await response.json();
      if (!response.ok) throw new Error(result.error?.message || "保存失败");
      onSaved(result);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  const setInstruction = (key: "do" | "avoid", value: string) =>
    setDraft((current) => ({
      ...current,
      instruction: { ...current.instruction, [key]: value },
    }));

  const setScopeProject = (value: string) =>
    setDraft((current) => ({
      ...current,
      scope: {
        ...current.scope,
        level: value ? "project" : "global",
        projects: value ? [value] : [],
        modules: [],
        features: [],
      },
    }));

  const setScopeModule = (value: string) =>
    setDraft((current) => ({
      ...current,
      scope: {
        ...current.scope,
        level: value ? "module" : current.scope.projects?.[0] ? "project" : "global",
        modules: value ? [value] : [],
        features: [],
      },
    }));

  const setScopeFeature = (value: string) =>
    setDraft((current) => ({
      ...current,
      scope: {
        ...current.scope,
        level: value ? "feature" : "module",
        features: value ? [value] : [],
      },
    }));
  const selectedModules = scopeCatalog.find((project) => project.id === draft.scope.projects?.[0])?.modules || [];
  const selectedFeatures = selectedModules.find((module) => module.id === draft.scope.modules?.[0])?.features || [];

  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <section className="editor-modal" onMouseDown={(event) => event.stopPropagation()}>
        <header className="modal-head">
          <div>
            <span className="micro-label">{mode === "create" ? "创建新节点" : "编辑记忆节点"}</span>
            <h2>{mode === "create" ? "新增记忆" : draft.title}</h2>
          </div>
          <button className="icon-button" onClick={onClose} aria-label="关闭">
            <X size={18} />
          </button>
        </header>
        <div className="editor-grid">
          <label className="field field-span">
            <span>规则标题</span>
            <input value={draft.title} onChange={(event) => setDraft({ ...draft, title: event.target.value })} />
          </label>
          <label className="field">
            <span>状态</span>
            <select value={draft.status} onChange={(event) => setDraft({ ...draft, status: event.target.value as Status })}>
              {Object.entries(statusLabel).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </label>
          <label className="field">
            <span>优先级</span>
            <select value={draft.priority} onChange={(event) => setDraft({ ...draft, priority: event.target.value as Rule["priority"] })}>
              {Object.entries(priorityLabel).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </label>
          <label className="field">
            <span>置信度</span>
            <input type="number" min="0" max="1" step="0.05" value={draft.confidence} onChange={(event) => setDraft({ ...draft, confidence: Number(event.target.value) })} />
          </label>
          <label className="field">
            <span>项目层级（留空表示全部）</span>
            <input list="scope-project-options" placeholder="例如：backend" value={normalizeProjectName(draft.scope.projects?.[0])} onChange={(event) => setScopeProject(event.target.value.trim())} />
            <datalist id="scope-project-options">{scopeCatalog.map((project) => <option key={project.id} value={project.id} />)}</datalist>
          </label>
          <label className="field">
            <span>服务模块（留空表示整个项目）</span>
            <input list="scope-module-options" placeholder="例如：scene、gateway" disabled={!draft.scope.projects?.[0]} value={draft.scope.modules?.[0] ?? ""} onChange={(event) => setScopeModule(event.target.value.trim())} />
            <datalist id="scope-module-options">{selectedModules.map((module) => <option key={module.id} value={module.id} />)}</datalist>
          </label>
          <label className="field">
            <span>功能模块（留空表示整个服务模块）</span>
            <input list="scope-feature-options" placeholder="例如：SceneEvent" disabled={!draft.scope.modules?.[0]} value={draft.scope.features?.[0] ?? ""} onChange={(event) => setScopeFeature(event.target.value.trim())} />
            <datalist id="scope-feature-options">{selectedFeatures.map((feature) => <option key={feature.id} value={feature.id}>{feature.label}</option>)}</datalist>
          </label>
          <label className="field field-span">
            <span>触发条件</span>
            <textarea rows={2} value={draft.trigger.description} onChange={(event) => setDraft({ ...draft, trigger: { ...draft.trigger, description: event.target.value } })} />
          </label>
          <label className="field field-span">
            <span>检索关键词（逗号分隔）</span>
            <input value={keywordText} onChange={(event) => { setKeywordText(event.target.value); setDraft({ ...draft, trigger: { ...draft.trigger, keywords: [...new Set(event.target.value.split(/[,，]/).map((item) => item.trim()).filter(Boolean))] } }); }} />
          </label>
          <label className="field field-span">
            <span>应该怎么做</span>
            <textarea rows={3} value={draft.instruction.do} onChange={(event) => setInstruction("do", event.target.value)} />
          </label>
          <label className="field field-span">
            <span>应该避免</span>
            <textarea rows={3} value={draft.instruction.avoid} onChange={(event) => setInstruction("avoid", event.target.value)} />
          </label>
          <label className="field field-span">
            <span>标签（逗号分隔）</span>
            <input value={tagText} onChange={(event) => { setTagText(event.target.value); setDraft({ ...draft, tags: [...new Set(event.target.value.split(/[,，]/).map((item) => item.trim()).filter(Boolean))] }); }} />
          </label>
          {error && <p className="form-error">{error}</p>}
        </div>
        <footer className="modal-actions">
          <span>保存后自动校验并重建知识图谱</span>
          <div>
            <button className="button button-muted" onClick={onClose}>取消</button>
            <button className="button button-primary" onClick={save} disabled={saving}>
              {saving ? <RefreshCw className="spin" size={16} /> : <Save size={16} />}
              {saving ? "保存中" : "保存规则"}
            </button>
          </div>
        </footer>
      </section>
    </div>
  );
}

function RuleDetail({
  rule,
  rules,
  onClose,
  onEdit,
  onDelete,
  onSelect,
}: {
  rule: Rule;
  rules: Rule[];
  onClose: () => void;
  onEdit: () => void;
  onDelete: () => void;
  onSelect: (id: string) => void;
}) {
  const related = rules
    .filter((item) => item.id !== rule.id)
    .map((item) => ({ item, shared: item.tags.filter((tag) => rule.tags.includes(tag)) }))
    .filter(({ shared }) => shared.length)
    .sort((a, b) => b.shared.length - a.shared.length)
    .slice(0, 5);

  return (
    <div className="drawer-backdrop" onMouseDown={onClose}>
    <aside className="detail-drawer" onMouseDown={(event) => event.stopPropagation()}>
      <header className="drawer-head">
        <button className="icon-button" onClick={onClose} aria-label="关闭详情"><ArrowLeft size={18} /></button>
        <div className="drawer-actions">
          <button className="button button-muted" onClick={onEdit}><Pencil size={15} />编辑</button>
          <button className="button button-danger" onClick={onDelete}><Trash2 size={15} />删除</button>
        </div>
      </header>
      <div className="drawer-scroll">
        <div className="detail-identity">
          <div className={`detail-icon status-${rule.status}`}><BrainCircuit size={25} /></div>
          <StatusBadge status={rule.status} />
          <span className={`priority-text priority-${rule.priority}`}>{priorityLabel[rule.priority]}优先级</span>
          <h2>{rule.title}</h2>
          <code>{rule.id}</code>
        </div>

        <div className="detail-metrics">
          <span><strong>{Math.round(rule.confidence * 100)}%</strong>置信度</span>
          <span><strong>{rule.evidence.length}</strong>证据</span>
          <span><strong>{rule.lifecycle.violation_count ?? 0}</strong>历史违反</span>
        </div>

        <section className="detail-section">
          <h3><Activity size={15} />节点时间</h3>
          <div className="node-timestamps">
            <span><small>创建时间</small><strong>{formatDateTime(rule.lifecycle.created_at)}</strong></span>
            <span><small>更新时间</small><strong>{formatDateTime(ruleUpdatedAt(rule))}</strong></span>
          </div>
        </section>

        <section className="detail-section">
          <h3><Check size={15} />规则指令</h3>
          <div className="directive directive-do"><small>应该</small><p>{rule.instruction.do}</p></div>
          <div className="directive directive-avoid"><small>避免</small><p>{rule.instruction.avoid}</p></div>
        </section>

        <section className="detail-section">
          <h3><Activity size={15} />触发条件</h3>
          <p>{rule.trigger.description}</p>
        </section>

        <section className="detail-section">
          <h3><LayoutGrid size={15} />作用域</h3>
          <div className="scope-pills"><span>{scopeLabel[rule.scope.level]}</span><span>{scopeSummary(rule)}</span></div>
        </section>

        <section className="detail-section">
          <h3><Link2 size={15} />关联节点</h3>
          <div className="related-stack">
            {related.length ? related.map(({ item, shared }) => (
              <button key={item.id} onClick={() => onSelect(item.id)}>
                <span className="relation-dot" />
                <span><strong>{item.title}</strong><small>共同标签：{shared.join("、")}</small></span>
                <ChevronRight size={15} />
              </button>
            )) : <p className="muted-copy">暂无相似节点</p>}
          </div>
        </section>

        <section className="detail-section">
          <h3><FileText size={15} />证据</h3>
          {rule.evidence.map((evidence) => (
            <article className="evidence-card" key={evidence.id}>
              <span><Link2 size={14} /></span>
              <div><small>{evidence.source} · {formatDate(evidence.observed_at)}</small><p>{evidence.summary}</p></div>
            </article>
          ))}
        </section>

        <section className="detail-section detail-tags">
          <h3><Tag size={15} />标签</h3>
          <div>{rule.tags.map((tag) => <span key={tag}>{tag}</span>)}</div>
        </section>
      </div>
    </aside>
    </div>
  );
}

export function MemoryWorkspace() {
  const [records, setRecords] = useState<RuleRecord[]>([]);
  const rules = useMemo(() => records.map((record) => record.rule), [records]);
  const [scopeCatalog, setScopeCatalog] = useState<ScopeCatalogProject[]>([]);
  const [nodes, setNodes, onNodesChange] = useNodesState<KnowledgeNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [view, setView] = useState<ViewMode>("graph");
  const [graphDisplayMode, setGraphDisplayMode] = useState<GraphDisplayMode>("level");
  const [focusedScope, setFocusedScope] = useState("");
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<"all" | Status>("all");
  const [scopeFilter, setScopeFilter] = useState("all");
  const [selectedId, setSelectedId] = useState("");
  const [editorMode, setEditorMode] = useState<"create" | "edit" | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [transferring, setTransferring] = useState<"import" | "export" | "">("");
  const importInput = useRef<HTMLInputElement>(null);

  const loadRules = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const response = await fetch(`${API}/rules`);
      const result = await response.json();
      if (!response.ok) throw new Error(result.error?.message || "读取记忆失败");
      setRecords(result.records);
      setScopeCatalog(result.scopes);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "读取记忆失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(loadRules, 0);
    return () => window.clearTimeout(timer);
  }, [loadRules]);

  useEffect(() => {
    const reloadWhenVisible = () => {
      if (document.visibilityState === "visible") loadRules();
    };
    window.addEventListener("focus", loadRules);
    document.addEventListener("visibilitychange", reloadWhenVisible);
    return () => {
      window.removeEventListener("focus", loadRules);
      document.removeEventListener("visibilitychange", reloadWhenVisible);
    };
  }, [loadRules]);

  useEffect(() => {
    const closeWindow = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      if (editorMode) {
        setEditorMode(null);
        return;
      }
      setSelectedId("");
    };
    window.addEventListener("keydown", closeWindow);
    return () => window.removeEventListener("keydown", closeWindow);
  }, [editorMode]);

  const filteredRules = useMemo(() => {
    const term = query.trim().toLowerCase();
    return rules.filter((rule) => {
      if (statusFilter !== "all" && rule.status !== statusFilter) return false;
      if (!ruleMatchesScope(rule, scopeFilter)) return false;
      if (!term) return true;
      return [rule.id, rule.title, rule.trigger.description, rule.instruction.do, ...rule.tags, scopeSummary(rule)]
        .join(" ").toLowerCase().includes(term);
    });
  }, [rules, query, statusFilter, scopeFilter]);

  const scopeHierarchy = useMemo(
    () => buildScopeHierarchy(filteredRules, scopeCatalog),
    [filteredRules, scopeCatalog],
  );
  const focusedHierarchyItem = focusedScope
    ? findHierarchyItem(scopeHierarchy, focusedScope)
    : scopeHierarchy;
  const focusedBreadcrumb = hierarchyBreadcrumb(focusedScope);
  const scopeOptions = useMemo(() => {
    const projects = Array.from(new Set(rules.map(ruleProject).filter(Boolean)))
      .sort((a, b) => a.localeCompare(b, "zh-CN"));
    return projects.map((project) => ({
      id: projectScopeId(project),
      label: project,
      count: rules.filter((rule) => ruleProject(rule) === project).length,
      modules: Array.from(new Set(rules
        .filter((rule) => ruleProject(rule) === project)
        .map(ruleModule)
        .filter(Boolean)))
        .sort((a, b) => a.localeCompare(b, "zh-CN"))
        .map((module) => ({
          id: moduleScopeId(project, module),
          label: module,
          count: rules.filter((rule) => ruleProject(rule) === project && ruleModule(rule) === module).length,
          features: Array.from(new Set(rules.filter((rule) => ruleProject(rule) === project && ruleModule(rule) === module).map(ruleFeature).filter(Boolean)))
            .sort((a, b) => a.localeCompare(b, "zh-CN"))
            .map((feature) => ({
              id: featureScopeId(project, module, feature), label: feature,
              count: rules.filter((rule) => ruleProject(rule) === project && ruleModule(rule) === module && ruleFeature(rule) === feature).length,
            })),
        })),
    }));
  }, [rules]);

  useEffect(() => {
    const graph = buildGraph(scopeHierarchy, graphDisplayMode, focusedScope);
    setNodes(graph.nodes);
    setEdges(graph.edges);
  }, [scopeHierarchy, graphDisplayMode, focusedScope, setNodes, setEdges]);

  useEffect(() => {
    if (focusedScope && !findHierarchyItem(scopeHierarchy, focusedScope)) {
      const timer = window.setTimeout(() => setFocusedScope(""), 0);
      return () => window.clearTimeout(timer);
    }
  }, [scopeHierarchy, focusedScope]);

  const selectedRecord = records.find((record) => record.rule.id === selectedId);
  const selected = selectedRecord?.rule;
  const counts = {
    active: rules.filter((rule) => rule.status === "active").length,
    candidate: rules.filter((rule) => rule.status === "candidate").length,
    critical: rules.filter((rule) => rule.priority === "critical").length,
  };

  function handleSaved(record: RuleRecord) {
    setRecords((current) => {
      const exists = current.some((item) => item.rule.id === record.rule.id);
      return exists ? current.map((item) => item.rule.id === record.rule.id ? record : item) : [...current, record];
    });
    setSelectedId(record.rule.id);
    setEditorMode(null);
  }

  async function deleteRule(record: RuleRecord) {
    const rule = record.rule;
    if (!window.confirm(`确定删除“${rule.title}”吗？\n规则会移动到本地回收区。`)) return;
    const response = await fetch(`${API}/rules/${encodeURIComponent(rule.id)}`, { method: "DELETE", headers: { "If-Match": record.revision } });
    const result = await response.json();
    if (!response.ok) {
      window.alert(result.error?.message || "删除失败");
      return;
    }
    setRecords((current) => current.filter((item) => item.rule.id !== rule.id));
    setSelectedId("");
  }

  async function exportRules() {
    setTransferring("export");
    try {
      const response = await fetch(`${API}/export`);
      const bundle = await response.json();
      if (!response.ok) throw new Error(bundle.error?.message || "导出失败");
      const blob = new Blob([`${JSON.stringify(bundle, null, 2)}\n`], {
        type: "application/json;charset=utf-8",
      });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `super-memory-${new Date().toISOString().slice(0, 10)}.json`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
    } catch (reason) {
      window.alert(reason instanceof Error ? reason.message : "导出失败");
    } finally {
      setTransferring("");
    }
  }

  async function importRules(file?: File) {
    if (!file) return;
    setTransferring("import");
    try {
      const bundle = JSON.parse(await file.text());
      const incomingRules = Array.isArray(bundle?.rules) ? bundle.rules : [];
      const existingIds = new Set(rules.map((rule) => rule.id));
      const conflictCount = incomingRules.filter((rule: { id?: string }) =>
        rule.id ? existingIds.has(rule.id) : false,
      ).length;
      const overwrite = conflictCount > 0;
      if (
        overwrite &&
        !window.confirm(`检测到 ${conflictCount} 条同 ID 规则。继续会用导入内容覆盖这些规则，是否继续？`)
      ) {
        return;
      }
      const response = await fetch(`${API}/import`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ bundle, conflict: overwrite ? "replace" : "skip" }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error?.message || "导入失败");
      await loadRules();
      window.alert(`导入完成：写入 ${result.imported.length} 条，跳过 ${result.skipped.length} 条。`);
    } catch (reason) {
      window.alert(reason instanceof Error ? reason.message : "导入失败");
    } finally {
      setTransferring("");
      if (importInput.current) importInput.current.value = "";
    }
  }

  function selectGraphNode(node: KnowledgeNode) {
    if (node.data.kind === "core") {
      setGraphDisplayMode("level");
      setFocusedScope("");
      setSelectedId("");
      return;
    }
    if (node.data.kind === "scope" && node.data.scopeKey) {
      setGraphDisplayMode("level");
      setFocusedScope(node.data.scopeKey);
      setSelectedId("");
      return;
    }
    if (node.data.ruleId) setSelectedId(node.data.ruleId);
  }

  function selectScope(scopeId: string) {
    setScopeFilter(scopeId);
    setGraphDisplayMode("level");
    setFocusedScope(scopeId === "all" ? "" : scopeId);
    setSelectedId("");
  }

  return (
    <main className="neural-shell">
      <aside className="neural-sidebar">
        <div className="brand-lockup"><span><BrainCircuit size={23} /></span><div><strong>Super Memory</strong><small>REVIEW · LEARN · EVOLVE</small></div></div>
        <nav>
          <button className={view === "graph" ? "active" : ""} onClick={() => setView("graph")}><Network size={18} /><span>知识图谱</span></button>
          <button className={view === "list" ? "active" : ""} onClick={() => setView("list")}><List size={18} /><span>规则列表</span></button>
        </nav>
        <div className="sidebar-section"><small>状态过滤</small>
          {(["all", "active", "candidate"] as const).map((status) => (
            <button key={status} className={statusFilter === status ? "active" : ""} onClick={() => setStatusFilter(status)}>
              {status === "all" ? <LayoutGrid size={17} /> : status === "active" ? <ShieldCheck size={17} /> : <Sparkles size={17} />}
              <span>{status === "all" ? "全部节点" : statusLabel[status]}</span>
              <em>{status === "all" ? rules.length : rules.filter((rule) => rule.status === status).length}</em>
            </button>
          ))}
        </div>
        <div className="sidebar-section scope-filter-section"><small>作用域</small>
          <button className={scopeFilter === "all" ? "active" : ""} onClick={() => selectScope("all")}>
            <Globe2 size={17} /><span>全部</span><em>{rules.length}</em>
          </button>
          {scopeOptions.map((project) => (
            <div className="scope-tree-group" key={project.id}>
              <button className={scopeFilter === project.id ? "active" : ""} onClick={() => selectScope(project.id)}>
                <Boxes size={17} /><span>{project.label}</span><em>{project.count}</em>
              </button>
              {project.modules.map((module) => (
                <div className="scope-tree-group" key={module.id}>
                  <button className={`scope-child ${scopeFilter === module.id ? "active" : ""}`} onClick={() => selectScope(module.id)}>
                    <GitBranch size={15} /><span>{module.label}</span><em>{module.count}</em>
                  </button>
                  {module.features.map((feature) => (
                    <button className={`scope-child scope-feature ${scopeFilter === feature.id ? "active" : ""}`} key={feature.id} onClick={() => selectScope(feature.id)}>
                      <GitBranch size={14} /><span>{feature.label}</span><em>{feature.count}</em>
                    </button>
                  ))}
                </div>
              ))}
            </div>
          ))}
        </div>
      </aside>

      <section className="neural-main">
        <header className="neural-header">
          <div><span className="micro-label">开发知识网络</span><h1>{view === "graph" ? "知识图谱" : "记忆规则"}</h1></div>
          <div className="header-actions">
            <label className="neural-search"><Search size={17} /><input placeholder="搜索节点、标签或作用域" value={query} onChange={(event) => setQuery(event.target.value)} />{query && <button onClick={() => setQuery("")}><X size={15} /></button>}</label>
            <input ref={importInput} type="file" accept="application/json,.json" hidden onChange={(event) => importRules(event.target.files?.[0])} />
            <button className="icon-button" onClick={() => importInput.current?.click()} disabled={Boolean(transferring)} aria-label="导入记忆" title="导入记忆"><Upload size={18} /></button>
            <button className="icon-button" onClick={exportRules} disabled={Boolean(transferring)} aria-label="导出记忆" title="导出记忆"><Download size={18} /></button>
            <button className="icon-button" onClick={loadRules} aria-label="刷新"><RefreshCw size={18} className={loading ? "spin" : ""} /></button>
            <button className="button button-primary" onClick={() => setEditorMode("create")}><Plus size={17} />新增记忆</button>
          </div>
        </header>

        <div className="stat-strip">
          <div><span className="stat-icon stat-cyan"><Network size={17} /></span><span><small>全部节点</small><strong>{rules.length}</strong></span></div>
          <div><span className="stat-icon stat-green"><ShieldCheck size={17} /></span><span><small>生效规则</small><strong>{counts.active}</strong></span></div>
          <div><span className="stat-icon stat-violet"><Sparkles size={17} /></span><span><small>候选记忆</small><strong>{counts.candidate}</strong></span></div>
          <div><span className="stat-icon stat-red"><AlertTriangle size={17} /></span><span><small>关键约束</small><strong>{counts.critical}</strong></span></div>
        </div>

        {error ? <div className="error-banner"><AlertTriangle size={18} /><span>{error}，请重新启动本地管理台。</span><button onClick={loadRules}>重试</button></div> : view === "graph" ? (
          <section className="graph-stage">
            <div className="graph-toolbar">
              <div className="graph-mode-switch" role="group" aria-label="图谱展示范围">
                <button className={graphDisplayMode === "level" ? "active" : ""} onClick={() => setGraphDisplayMode("level")}>当前层级</button>
                <button className={graphDisplayMode === "all" ? "active" : ""} onClick={() => { setGraphDisplayMode("all"); setFocusedScope(""); }}>展示全部</button>
              </div>
              <button className="graph-breadcrumb" onClick={() => { setGraphDisplayMode("level"); setFocusedScope(""); }}>
                全部
              </button>
              {focusedBreadcrumb.map((label, index) => {
                const target = index === 0
                  ? projectScopeId(label)
                  : index === 1 ? moduleScopeId(focusedBreadcrumb[0], label)
                    : featureScopeId(focusedBreadcrumb[0], focusedBreadcrumb[1], label);
                return <span className="breadcrumb-segment" key={target}><ChevronRight size={13} /><button className="graph-breadcrumb" onClick={() => { setGraphDisplayMode("level"); setFocusedScope(target); }}>{label}</button></span>;
              })}
              <em>{graphDisplayMode === "level" ? `${focusedHierarchyItem?.children.length ?? 0} 个子节点` : `${filteredRules.length} 个规则节点`}</em>
            </div>
            <ReactFlow
              key={`${graphDisplayMode}-${focusedScope || "root"}-${filteredRules.length}`}
              nodes={nodes}
              edges={edges}
              nodeTypes={nodeTypes}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              onNodeClick={(_, node) => selectGraphNode(node)}
              onPaneClick={() => setSelectedId("")}
              fitView
              fitViewOptions={{ padding: 0.22 }}
              minZoom={0.25}
              maxZoom={1.8}
              colorMode="light"
            >
              <Background variant={BackgroundVariant.Dots} gap={22} size={1} color="#c8d8ee" />
              <Controls showInteractive={false} />
              <MiniMap pannable zoomable nodeColor={(node) => node.data.kind === "scope" ? "#78a7d9" : node.data.status === "active" ? "#88a9cf" : node.data.status === "candidate" ? "#d1b58a" : "#b6bec8"} maskColor="rgba(239, 246, 255, .72)" />
            </ReactFlow>
            {!loading && !filteredRules.length && <div className="graph-empty"><Search size={27} /><strong>没有匹配的节点</strong><span>调整搜索词或状态过滤</span></div>}
          </section>
        ) : (
          <section className="memory-table">
            <div className="table-head"><span>记忆节点</span><span>状态</span><span>作用域</span><span>置信度</span><span /></div>
            {filteredRules.map((rule) => (
              <button className="table-row" key={rule.id} onClick={() => setSelectedId(rule.id)}>
                <span className="rule-cell"><span className={`rule-glyph status-${rule.status}`}><BrainCircuit size={17} /></span><span><strong>{rule.title}</strong><small>{rule.id}</small></span></span>
                <StatusBadge status={rule.status} />
                <span className="scope-cell"><Globe2 size={14} />{scopeSummary(rule)}</span>
                <span className="confidence"><i><b style={{ width: `${rule.confidence * 100}%` }} /></i>{Math.round(rule.confidence * 100)}%</span>
                <ChevronRight size={16} />
              </button>
            ))}
          </section>
        )}
      </section>

      {selectedRecord && <RuleDetail rule={selectedRecord.rule} rules={rules} onClose={() => setSelectedId("")} onEdit={() => setEditorMode("edit")} onDelete={() => deleteRule(selectedRecord)} onSelect={setSelectedId} />}
      {editorMode && <RuleEditor key={editorMode + ":" + selectedId} initial={editorMode === "edit" && selected ? selected : newRuleDraft(scopeFilter === "all" ? focusedScope : scopeFilter)} revision={editorMode === "edit" ? selectedRecord?.revision : undefined} mode={editorMode} scopeCatalog={scopeCatalog} onClose={() => setEditorMode(null)} onSaved={handleSaved} />}
    </main>
  );
}
