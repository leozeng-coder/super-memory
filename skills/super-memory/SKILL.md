---
name: super-memory
description: 基于人工代码审查、Git 差异、测试失败和故障证据，维护由 Codex 与本地可视化管理台共用的开发规则记忆。当 Codex 需要编码前检索生效规则、复盘人工修正、生成待审核规则、按用户选择保存 active 或 candidate、依据规则自检，或维护规则生命周期与冲突时使用。不要用于保存秘密、完整源码、闲聊事实或未经审核的个人偏好。
---

# Super Memory

将本记忆仓库作为开发规则的唯一事实来源。维护记忆时不得修改源码项目，除非用户另外明确要求修改代码。

所有面向用户的说明、复盘结论、规则标题、规则正文、证据摘要和检查结果必须使用中文。程序字段名、状态值、命令名、技术名称及代码标识符可保留英文。

## 定位 Skill 与记忆目录

将包含本文件的目录记为 `SKILL_ROOT`，所有命令均调用 `SKILL_ROOT/scripts/memory_cli.py`。

规则数据目录按以下优先级确定：命令行 `--memory-root`、环境变量 `SUPER_MEMORY_HOME`、含有 `memory.config.json` 的源码检出根目录、用户目录下的 `.super-memory`。首次使用时运行 `init`。Skill、命令行和管理台必须指向同一个规则数据目录；源码检出根目录中的本地规则数据必须由 `.gitignore` 排除。

## 唯一事实源

- `graph/rules/active`、`graph/rules/candidate` 和 `graph/rules/archive` 中的规则 JSON 是唯一事实数据。Codex 审核落库、命令行检索和可视化管理台直接共用这些文件，不执行同步、镜像或双向复制。
- `indexes/rules.json` 只是可重建的派生索引；检索必须以当前规则 JSON 为准。`graph/rules/trash` 是本地回收区，不得参与校验、索引或检索。
- 不得把本 Skill 的开发规则写入或读取 Codex 原生的 `$CODEX_HOME/memories`、`MEMORY.md`、`rollout_summaries` 或 `extensions/ad_hoc/notes`，避免形成第二套开发记忆。
- 用户审核确认保存后，必须使用本 Skill 的 `save` 命令直接写入上述唯一规则库；不得另建待同步副本。

## 选择操作

- `reflect`：检查用户提供的审查意见或 Git 差异，区分已确认事实和推断，并判断是否值得创建候选规则。
- `retrieve`：编码前使用任务关键词运行 `scripts/memory_cli.py retrieve`，只加载匹配度最高的生效规则。
- `check`：依据检索到的规则检查生成内容，报告违规项或明确适用的例外。
- `maintain`：校验并重建索引，检查重复和冲突，并使用证据更新规则生命周期。

## 复盘人工修正

1. 阅读差异或审查证据。没有证据时，不得推断人工修正原因。
2. 将修改归类为需求变化、缺陷修正、项目约定、等价重构或暂时无法确定。
3. 分别评估再现概率、影响程度、泛化能力和证据强度。
4. 不要为临时需求、无解释偏好、等价改写或一次性兼容代码创建规则。
5. 在写入任何规则前，先生成中文审核草案，不得直接修改记忆库。
6. 审核草案必须以“检测到值得沉淀的开发经验，建议进行持久化记忆审核”开头，并包含事实、原因、适用范围、例外、评分、证据和建议状态。
7. 明确提供三个选择：`保存为 active`、`保存为 candidate`、`不保存`。用户也可以要求修改草案。
8. 只有用户明确选择保存后才写入规则；没有明确答复时，不得视为同意。保存时将完整规则写入临时 JSON 草案，再调用统一 `save` 命令落入唯一规则库。
9. 如果用户选择 `active`，本次明确确认即满足激活条件并使用 `--status active`；否则使用 `--status candidate`。
10. 只记录抽象规则和证据引用，不得保存秘密信息或不必要的源码。
11. 规则的 `title`、`trigger.description`、`instruction`、`rationale`、`exceptions` 和 `evidence.summary` 必须使用中文。

## 自动触发条件

在代码生成、修改、调试或审查任务开始时，自动检索与语言、技术、项目、模块和操作相关的生效规则。没有匹配规则时安静继续，不要产生无意义提示。

在出现以下任一证据时，任务结束前自动执行复盘：

- 用户修改了 Codex 刚生成的代码并提供了差异；
- 用户给出明确的代码审查意见或纠正原因；
- 编译、测试或故障证据证明生成代码存在可复现问题；
- 相同错误或人工纠正再次出现；
- 用户明确要求总结、沉淀或记住开发经验。

需求临时变化、无理由的个人偏好、等价重构、格式化变化和一次性兼容处理不得触发持久化审核。

只有存在足够证据且评分达到 6 分时才提示用户审核。0 至 5 分只在当前任务中简短说明，不进入审核流程。

## 规则优先级

按照以下顺序应用规则：

1. 用户当前明确指令
2. 项目或模块规则
3. 技术或语言规则
4. 全局规则
5. 候选规则

候选规则只用于提示，不得直接约束代码生成。

## 确定性命令

使用 Skill 中的脚本运行：

```powershell
python "<SKILL_ROOT>/scripts/memory_cli.py" init
python "<SKILL_ROOT>/scripts/memory_cli.py" validate
python "<SKILL_ROOT>/scripts/memory_cli.py" reindex
python "<SKILL_ROOT>/scripts/memory_cli.py" retrieve --query "C++ 网络 序列化"
python "<SKILL_ROOT>/scripts/memory_cli.py" save --input "<规则草案.json>" --status active --actor user --reason "用户明确选择保存为 active"
```

`save` 会执行完整校验、原子写入、事件记录和索引重建。手工编辑规则后仍须运行 `validate`；只有校验成功后才运行 `reindex`。

## 编写规则

遵循 `schemas/memory-rule.schema.json`。保留不可变的规则 ID 和证据历史。明确记录例外。替换规则时，将旧规则标记为 `superseded` 并引用替代规则，不要删除历史。

将生命周期观察结果追加到 `history/events.jsonl`。Git 负责文件变更历史；事件日志只记录触发、验证成功、误报、人工覆盖、弃用或被替代等领域事件。事件的说明文字必须使用中文。
