---
name: super-memory
description: 在开发任务中检索并应用规则，根据人工纠正、差异、测试或故障证据复盘，经用户审核后保存开发经验，并维护规则生命周期。Codex 与本地管理台共用一个规则库。不要用于保存秘密、完整源码或闲聊事实。
---

# Super Memory

面向用户的说明、规则正文、证据摘要和事件原因使用中文；程序字段、命令和代码标识符可保留英文。维护记忆时不得修改业务源码，除非用户已授权该代码任务。

## 日常开发

代码生成、修改、调试或审查开始时，识别项目、服务模块、功能模块、语言及具体操作，检索适用规则。首次进入项目或功能不明确时，先运行 `scopes` 查看功能目录、别名和源码路径；同一任务可复用目录。

```powershell
python "<SKILL_ROOT>/scripts/memory.py" scopes
python "<SKILL_ROOT>/scripts/memory.py" search --query "异步回调 shared_ptr 生命周期" --project server --module svr_scene --feature SceneEvent --language cpp
# 也可用项目相对路径定位功能；仅匹配目录中明确配置的路径
python "<SKILL_ROOT>/scripts/memory.py" search --query "修改事件触发" --project server --module svr_scene --path "svr_scene/src/scene/logic_scene_event.cpp" --language cpp
```

- 项目、服务模块、功能使用库中实际 ID，示例不是默认值。查询描述操作、技术或符号，作用域放在独立参数中；`--feature` 和 `--path` 可重复，但必须指定项目和一个服务模块。功能也接受目录中的别名。
- 层级为“全部 → 项目 → 服务模块 → 功能模块 → 规则”。具体功能开发先定位功能，命中的功能规则与相关上层规则一起检索；其他功能的专用规则会被过滤。明确选中功能时，其规则可凭功能归属匹配，不要求查询再次出现每条规则的关键词，但仍受状态、数量和字符预算限制。
- `search` 只返回一种结构化结果：完整要求、触发条件、例外、冲突关系、来源和内容版本号 `revision`。支持语言别名和中文句子；项目、模块不作为相关度加分项。
- 未提供的作用域不会随意推断。路径定位结果显示在 `context.features`；未匹配路径会给出警告，不能当作功能没有规则。已指定项目但模块未知时，仅返回项目及更广范围的规则；已知服务模块但功能未知时，仅返回该模块及更广范围的规则。跨项目探索的结果须先确认适用。
- 依次遵循用户当前明确指令、功能、服务模块、项目、技术或语言、全局规则。候选规则仅供参考；冲突结合范围、例外和替代关系判断，不能按检索分数决定。
- 完成后对照规则自检。同一任务范围内复用结果，新模块或技术补充检索。无匹配时安静继续；失败和遗漏不能当作“没有规则”。

默认最多 8 条普通规则，输出预算 16000 字符，可用 `--limit`、`--max-chars` 调整。`omitted_count` 和 `warnings` 说明遗漏，正文与例外不截断。只有用户确认规则在其范围内始终适用时才设置 `trigger.always_apply: true`；完整上下文确认适用后，此类 active 规则不受普通数量上限影响，字符预算不足时报错。

## 定位与唯一事实源

`SKILL_ROOT` 为本文件所在目录。唯一命令入口是 `scripts/memory.py`；`info` 显示实际数据目录且不初始化。

数据目录依次取 `--root`、`SUPER_MEMORY_HOME`、含 `memory.config.json` 的 Skill 源码检出根目录、用户目录下 `.super-memory`。未初始化时先核对管理台目录，确认是新库后运行 `init`，避免误建第二套记忆。

- `graph/rules/active`、`candidate`、`archive` 中的 JSON 是唯一规则事实；检索读取当前文件，`indexes/rules.json` 可重建，`trash` 不参与校验、索引和检索。
- Skill、CLI 和管理台共用目录与业务服务，不创建同步副本。本地规则、配置、日志和索引由 `.gitignore` 排除。
- 不把这些开发规则写入或读取 Codex 原生 `$CODEX_HOME/memories`、`MEMORY.md`、`rollout_summaries` 或 `extensions/ad_hoc/notes`。

## 有纠正证据时复盘

用户提供人工修改差异、明确审查理由，编译/测试/故障证明生成内容有可复现问题，相同错误再次出现，或用户要求沉淀经验时，在任务结束前复盘。

1. 区分事实与推断，不臆测原因。区分需求变化、缺陷修正、项目约定、等价重构和原因未知。临时需求、无理由偏好、格式变化或一次性兼容处理不自动沉淀。
2. 用 `search --include-candidates` 检索已有规则，优先补充证据或修正规则，避免重复创建。
3. 自动建议按下表评分：总分至少 6 分且证据强度为 2 分才发起审核。用户明确要求保存的长期约定按其指令处理，不用评分否决明确授权。

| 维度 | 0 分 | 1 分 | 2 分 |
|---|---|---|---|
| 再现概率 | 一次性 | 同类任务可能再现 | 已重复或属于持续流程 |
| 影响程度 | 无可说明影响 | 局部维护性或效率 | 正确性、稳定性或明确的重要约定 |
| 泛化能力 | 仅单次修改 | 同模块同类场景 | 有清晰可复用条件和边界 |
| 证据强度 | 猜测 | 有差异但原因不确定 | 明确审查理由、复现证据或用户明确约定 |

4. 写入前给出中文草案，以“检测到值得沉淀的开发经验，建议进行持久化记忆审核”开头，包含事实、原因、范围、例外、评分、证据和建议状态。提供 `保存为 active`、`保存为 candidate`、`不保存`，允许修改。
5. 用户明确选择后，在完整规则 JSON 中设置对应 `status`，再执行新增或更新。同一草案及状态已有确认时直接执行，不重复询问；没有答复不能视为同意。只保存抽象规则和必要证据引用，不保存秘密或完整源码。

## 新增、更新和维护

规则由 `assets/memory-rule.schema.json` 定义并直接校验，不复制 Schema 到数据目录。规则 ID 不可变，证据按 ID 合并；旧证据不可覆盖，纠正时用新 ID 追加修正说明。

功能专用规则使用 `scope.level: feature`，并同时填写一个项目、一个服务模块和一个 `scope.features` 功能 ID。跨功能通用规则保留在服务模块或更高层，不因证据恰好来自某功能就缩窄范围，不复制同一规则到多个功能。功能目录的 `aliases` 和 `paths` 用于定位，可通过导出快照的 `scope_hierarchy[].modules[].features[]` 维护并导入；路径只登记归属明确的文件，共享入口按本次实际修改判断功能。

```powershell
python "<SKILL_ROOT>/scripts/memory.py" create --input "<完整规则.json>" --reason "用户确认新增生效规则"
python "<SKILL_ROOT>/scripts/memory.py" get RULE-EXAMPLE
python "<SKILL_ROOT>/scripts/memory.py" update RULE-EXAMPLE --revision "<读取时的 revision>" --input "<完整规则.json>" --reason "用户确认更新规则"
```

- 更新前用 `get` 读取完整规则和 `revision`。更新、删除必须传入该版本号；冲突时重新读取、合并并核对原授权，不绕过检查。管理台自动携带版本号。
- `list` 一次返回带版本的完整规则和作用域；`export --output <文件>` 直接写 UTF-8 JSON；`import --input <文件> --conflict skip|replace --reason <中文原因>` 原子提交整批导入；`delete <ID> --revision <版本> --reason <原因>` 移入回收区。
- CLI 通过 JSON 标准输入或 `--input` 文件接收规则，成功输出 JSON，业务失败在标准错误输出结构化错误。具体参数用子命令 `--help` 查询。
- 读写使用进程锁；事务日志记录原数据，操作异常会恢复，进程中断后下次访问先恢复未完成事务。不要手工编辑以绕过证据、版本或事务检查。
- `validate` 校验规则与配置，`reindex` 重建派生索引。冲突与替代关系仍须结合事实审核，不能将结构校验当作语义验证。
- 有事实依据时才调整置信度和成功/违反/例外计数。`last_validated_at` 表示实践验证时间，保存不刷新；`review_after` 到期表示需要复查，不自动失效。
- 经确认后，更新规则 JSON 的 `status` 完成弃用、替换或归档。替换时旧规则填写 `relations.replacement`，新规则填写 `relations.supersedes`。保留证据和历史；检索命中不等于验证成功。
