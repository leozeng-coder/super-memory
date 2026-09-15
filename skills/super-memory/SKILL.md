---
name: super-memory
description: 在开发任务中检索并应用规则，根据人工纠正、Git 差异、测试或故障证据复盘，经用户审核后保存开发经验，并维护规则生命周期。Codex 与本地管理台共用一个规则库。不要用于保存秘密、完整源码或闲聊事实。
---

# Super Memory

面向用户的说明、规则正文、证据摘要和事件原因使用中文；程序字段、命令和代码标识符可保留英文。维护记忆时不得修改业务源码，除非用户已授权该代码任务。

## 日常开发：检索、应用、自检

在代码生成、修改、调试或审查开始时识别项目、模块、语言和具体操作，使用 `retrieve --format context` 读取匹配规则的完整要求、触发条件、例外、冲突关系和来源。

```powershell
python "<SKILL_ROOT>/scripts/memory_cli.py" retrieve --query "异步回调 shared_ptr 生命周期" --project server --module svr_scene --language cpp --format context
```

- 项目和模块使用库中实际的 ID；示例不是默认值。作用域放在独立参数中，查询词描述具体操作、技术或符号。`--module`、`--language`、`--technology` 可重复传入。
- 未提供的作用域不会自动推断；跨项目探索的结果须先确认适用。已指定项目但模块未知时，仅返回项目及更广范围的规则。
- 依次遵循用户当前明确指令、模块、项目、技术或语言、全局规则。候选规则仅供参考；冲突须结合范围、例外和替代关系判断，不能按检索分数决定。
- 完成后对照已加载规则自检。同一任务范围内复用结果，处理新模块或技术时补充检索。无匹配时安静继续；检索失败或存在遗漏提示时先排查或调整查询，不能把错误当作“没有规则”。

检索支持 `C++/cpp/cxx` 等别名和中文句子。默认最多 8 条普通规则，context 输出预算 16000 字符；可调整 `--limit`、`--max-chars`。正文和例外不截断，遗漏数量会明确报告。

只有用户明确确认某规则在其范围内始终适用时，才设置 `trigger.always_apply: true`。此类 active 规则仍受作用域过滤；完整上下文确认适用后不受普通数量上限影响，字符预算不足时报错，须增大预算。

## 定位与唯一事实源

`SKILL_ROOT` 是本文件所在目录，所有操作调用其 `scripts/memory_cli.py`。数据目录依次取 `--memory-root`、`SUPER_MEMORY_HOME`、含 `memory.config.json` 的 Skill 源码检出根目录、用户目录下的 `.super-memory`。context 输出包含实际目录。出现未初始化错误时，先核对管理台使用的目录，确认是新库后运行 `init`，避免误建第二套记忆。

- `graph/rules/active`、`candidate`、`archive` 内的 JSON 是唯一事实数据。检索读取当前规则；`indexes/rules.json` 可重建；`trash` 不参与校验、索引和检索。
- Skill、CLI 和管理台共用数据目录与保存入口，不建立同步副本。源码检出中的本地规则、配置、索引和日志必须由 `.gitignore` 排除。
- 不把这些开发规则写入或读取 Codex 原生 `$CODEX_HOME/memories`、`MEMORY.md`、`rollout_summaries` 或 `extensions/ad_hoc/notes`。

## 有纠正证据时：复盘与审核

用户提供人工修改差异、明确审查理由，编译/测试/故障证明生成内容有可复现问题，相同错误再次出现，或用户要求沉淀经验时，在任务结束前复盘。

1. 区分事实与推断，不臆测修改原因。区分需求变化、缺陷修正、项目约定、等价重构和原因未知。临时需求、无理由偏好、格式变化或一次性兼容处理不自动沉淀。
2. 先检索已有 active 和 candidate（`--include-candidates --format context`），优先补充证据或修正规则，避免重复创建。
3. 自动建议按下表评分：总分至少 6 分且证据强度为 2 分才发起审核；不足时仅在当前任务需要解释时简述。用户明确要求保存的长期约定按其指令处理，不用评分否决明确授权。

| 维度 | 0 分 | 1 分 | 2 分 |
|---|---|---|---|
| 再现概率 | 一次性 | 同类任务可能再现 | 已重复或属于持续流程 |
| 影响程度 | 无可说明影响 | 局部维护性或效率 | 正确性、稳定性或明确的重要约定 |
| 泛化能力 | 仅单次修改 | 同模块同类场景 | 有清晰可复用条件和边界 |
| 证据强度 | 猜测 | 有差异但原因不确定 | 明确审查理由、复现证据或用户明确约定 |

4. 写规则前给出中文草案，以“检测到值得沉淀的开发经验，建议进行持久化记忆审核”开头，包含事实、原因、范围、例外、评分、证据和建议状态。提供 `保存为 active`、`保存为 candidate`、`不保存`，允许修改草案。
5. 用户明确选择后，把完整规则写入临时 JSON 并调用 `save`。同一草案及状态已有明确确认时直接执行，不重复询问；没有答复不能视为同意。只保存抽象规则和必要证据引用，不保存秘密或完整源码。

## 保存与维护

规则遵循 Skill 的 `assets/memory-rule.schema.json`，初始化会复制到数据目录的 `schemas/`。规则 ID 不可变，证据 ID 唯一。更新会合并旧证据；已有证据不可覆盖，纠正时使用新证据 ID 追加修正说明。

```powershell
python "<SKILL_ROOT>/scripts/memory_cli.py" save --input "<规则草案.json>" --status active --actor user --reason "用户明确选择保存为 active"
python "<SKILL_ROOT>/scripts/memory_cli.py" validate
python "<SKILL_ROOT>/scripts/memory_cli.py" reindex
```

更新时从读取结果取得 `updated_at`，传入 `--expected-updated-at`（没有时间时传 `null`）。版本冲突后重新读取、合并，并确认原授权仍覆盖修改；不要绕过检查直接覆盖。管理台编辑自动检查版本。

CLI 与管理台读写共享进程锁，保存使用单文件原子替换，操作异常时恢复规则、索引和事件。`save --mode create/update` 限制新增或更新；`list --export` 导出快照，`import` 一次提交整批规则，`delete` 移入回收区。不要手工编辑以绕过证据或版本检查。

维护时检查重复、冲突和适用范围；有新事实依据时才调整置信度及成功/违反/例外计数。`last_validated_at` 表示实践验证时间，保存和结构校验不会自动刷新；`review_after` 到期表示需要复查，不表示自动失效。

经确认后可用 `save --status deprecated`、`superseded` 或 `archived` 保存完整规则。替换时旧规则填写 `relations.replacement`，新规则填写 `relations.supersedes`，保留证据和历史。`history/events.jsonl` 记录操作事件；实践观察须有中文原因及证据，不能把检索命中当作验证成功。
