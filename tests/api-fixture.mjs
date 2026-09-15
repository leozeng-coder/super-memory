export function sampleRule() {
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
