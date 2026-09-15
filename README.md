# Super Memory

Super Memory 是一套持久化记忆工具。它把人工代码审查、Git 差异、测试失败和故障证据沉淀为可审核、可检索、可持续演化的开发规则。

它包含：

- 一个负责检索、复盘、审核和维护开发规则的 Codex Skill；
- 一套确定性的 Python 命令行工具；
- 一个与 Codex 和命令行共用同一份数据的本地可视化管理台。

> Super Memory 是社区项目，并非 OpenAI 官方项目。

![Super Memory 知识图谱管理台](./docs/images/super-memory-knowledge-graph.png)

管理台以作用域和分类为中心展示开发规则。可以按层级下钻，区分生效规则与候选记忆，并完成搜索、新增、编辑、删除、导入和导出。图谱、规则列表、Codex Skill 与命令行直接读写同一份规则数据。

## 它解决什么问题

普通对话可以记住当前上下文，却很难稳定管理这些长期开发经验：

- 哪些代码审查意见已经被确认；
- 一条经验适用于全局、项目还是具体模块；
- 哪些规则已经生效，哪些仍需观察；
- 规则为什么产生、有哪些证据和例外；
- 旧规则何时被废弃或被新规则替代。

Super Memory 将这个过程组织成闭环：

```text
编码前检索生效规则
        ↓
Codex 生成或修改代码
        ↓
人工审查 / 测试失败 / 故障证据
        ↓
检测是否具有通用性并生成中文记忆审核草案
        ↓
用户选择 active / candidate / 不保存（直接忽略即可）
        ↓
后续任务再次检索、应用和校验
```

## 从一次修正形成可审核记忆

![Codex 生成持久化记忆审核草案](./docs/images/super-memory-review-draft.png)

当人工审查、测试结果或故障证据揭示了可复现问题时，Super Memory 不会直接写入规则库，而是先让 Codex 生成中文审核草案。草案包含规则、证据、适用范围、例外、评分和建议状态，并明确提供三个选择：

- `保存为 active`：立即作为生效规则参与后续编码；
- `保存为 candidate`：保留为候选经验，只用于提示和继续观察；
- `不保存`：本次经验不进入持久化规则库。

只有用户明确确认后，规则才会落库。

## 编码前自动检索

![Codex 在编码前检索 Super Memory 规则](./docs/images/super-memory-rule-retrieval.png)

配置自动调用后，Codex 在代码生成、修改、调试或审查开始前，会按语言、技术、项目、模块和操作关键词检索最相关的生效规则。命中的规则进入本次任务上下文，候选规则只作提示，不会直接约束代码生成。

检索支持 `C++/cpp/cxx` 等语言别名和中文句子。项目、模块、语言和技术可通过独立参数过滤，项目名和模块名不会作为相关度加分项。使用 `--format context` 可以一次取得完整要求、触发条件、例外、冲突关系及规则来源；默认摘要输出仍然保留。

任务结束前，Codex 还会依据命中的规则做一次自检；如果新的人工纠正具有充分证据，再进入记忆审核流程。

## 与 Codex 自身持久化能力配合

Codex 的 `AGENTS.md`、Memories 和 Skills 是互补能力：`AGENTS.md` 提供持久指令，Memories 延续有用上下文，Skills 封装可复用流程。Super Memory 使用这三层能力协作，但不会把同一批开发规则复制成多份。

| 层级 | 作用 | 建议保存的内容 |
| --- | --- | --- |
| Codex Memories | 延续跨任务的有用上下文 | 轻量背景、稳定偏好和工作上下文 |
| `AGENTS.md` | 让仓库或开发者级指令持续生效 | 要求编码任务自动调用 Super Memory |
| Super Memory Skill | 执行确定性的记忆工作流 | 检索、复盘、审核、保存、自检和生命周期维护 |
| Super Memory 规则库 | 结构化开发规则的唯一事实源 | `active`、`candidate`、`archive` 规则及证据历史 |

推荐边界：

- Codex 原生 Memories 可以正常开启，用于延续一般上下文；
- `AGENTS.md` 负责稳定触发 Super Memory；
- 经审核的开发规则只保存在 Super Memory 规则库；
- 不把同一规则同时写入 Codex Memories、`MEMORY.md` 或其他笔记，避免出现两套不一致的规则。

如果使用 Codex CLI，可以通过 `/memories` 配置原生记忆的注入与生成。开启或关闭它都不会改变 Super Memory 的规则数据；两者没有同步关系。

Codex 对这些能力的官方定位可参考 [Customization](https://learn.chatgpt.com/docs/customization/overview)。

## 安装到 Codex

在 Codex 中发送下面这句话：

```text
使用 $skill-installer 从 GitHub 仓库 leozeng-coder/super-memory 的 skills/super-memory 子目录安装 Super Memory
```

安装完成后，Skill 会在下一轮任务中可用。首次初始化默认规则库：

```powershell
python "$HOME/.codex/skills/super-memory/scripts/memory_cli.py" init
```

默认数据目录是 `~/.super-memory`。如需使用其他目录，请让 Codex、命令行和管理台都设置同一个 `SUPER_MEMORY_HOME`。

### 安装不会自动修改 Codex 记忆

安装和初始化只会产生以下变更：

- `$skill-installer` 将 Skill 文件安装到 Codex 的 Skills 目录；
- `init` 在 `~/.super-memory` 或 `SUPER_MEMORY_HOME` 指定的位置创建 Super Memory 规则库；
- 管理台和命令行只读写这套独立规则库。

Super Memory 不会自动修改 Codex 原生的 `~/.codex/memories/`、`MEMORY.md`，也不会访问其他开发工具的持久化记忆。它同样不会自动修改用户已有的 `AGENTS.md`；需要自动调用时，请按下一节手动追加配置。

## 配置自动调用

根据需要选择一种配置方式：

- 全局生效：手动编辑 `~/.codex/AGENTS.md`；Windows 默认位置是 `C:\Users\<你的用户名>\.codex\AGENTS.md`；
- 单个项目生效：手动编辑该项目根目录的 `AGENTS.md`。

如果文件不存在，可以新建；如果已经存在，请保留原内容并在末尾追加下面这段，不要覆盖用户已有指令：

```md
进行代码生成、修改、调试或审查任务时，自动隐式使用全局 `super-memory` Skill。所有开发持久化记忆相关规范，包括检索、复盘、审核、保存和生命周期管理，均以该 Skill 的 `SKILL.md` 为唯一依据。
```

保存后开启一个新的 Codex 任务进行验证，例如直接提出普通开发请求：

```text
请帮我修改这段 C++ 容器填充逻辑，并检查是否符合项目已有开发规则。
```

如果配置生效，Codex 会在编码前调用 Super Memory 检索相关 `active` 规则；没有匹配规则时会安静继续，不会产生无意义提示。

### 配置后会达到什么效果

- 开始代码生成、修改、调试或审查前，Codex 会先按语言、技术、项目、模块和操作检索相关 `active` 规则；
- 只加载匹配度最高的规则，避免把整个规则库塞入任务上下文；
- 在不冲突用户当前明确指令的前提下，`active` 规则可以约束本次实现，`candidate` 只用于提示，不会直接约束代码；
- 没有匹配规则时安静继续，不影响正常编码；
- 完成修改后，Codex 会依据命中的规则自检；
- 遇到有充分证据的新经验时，Codex 先展示中文审核草案，只有用户明确选择 `active` 或 `candidate` 后才保存；
- 整个过程只读写 Super Memory 规则库，不会修改 Codex 原生 Memories 或其他工具的持久化记忆。

### 不修改 `AGENTS.md` 时如何使用

不配置 `AGENTS.md` 也可以使用 Super Memory。Skill 的描述允许 Codex 根据任务语义隐式选择，但如果希望确保本次任务一定调用，请在请求中显式写出 `$super-memory`：

```text
使用 $super-memory 检索与当前 C++ 修改相关的生效规则。
```

```text
使用 $super-memory 复盘这次人工代码审查，并生成中文记忆审核草案。
```

如果要明确要求保存，请先审核 Codex 给出的草案，再回复“保存为 active”或“保存为 candidate”。未作明确选择时，Super Memory 不会落库。

## 启动可视化管理台

如果需要图谱、规则编辑以及导入导出功能，克隆完整仓库：

```powershell
git clone https://github.com/leozeng-coder/super-memory.git
cd super-memory
python ./skills/super-memory/scripts/memory_cli.py init
.\start-super-memory.cmd
```

首次启动会安装 Web 依赖。页面与本地 API 默认只监听本机回环地址；未设置自定义数据目录且源码根目录没有本地配置时，使用 `~/.super-memory`。

如果源码检出根目录已经有 `memory.config.json`，或设置了 `SUPER_MEMORY_HOME`，会优先使用相应目录。请核对启动输出和检索结果中的 `memory_root`，确保管理台与已安装的 Skill 指向同一个规则库。

也可以在 PowerShell、macOS 或 Linux 中分别运行 `./start-super-memory.ps1`、`./start-super-memory.sh`，或直接执行 `node ./start-web.mjs`。

如需把规则数据保存在自定义目录，启动 Codex 和管理台前设置相同的环境变量：

```powershell
$env:SUPER_MEMORY_HOME = "D:\my-super-memory"
python ./skills/super-memory/scripts/memory_cli.py init
.\start-super-memory.cmd
```

## 工作方式

Super Memory 将以下规则 JSON 作为唯一事实源：

```text
graph/rules/active/
graph/rules/candidate/
graph/rules/archive/
```

Codex、命令行和可视化管理台直接读写同一个记忆目录。`indexes/rules.json` 只是可以随时重建的派生索引，`graph/rules/trash` 是不参与检索的本地回收区。

规则数据目录按以下顺序确定：

1. 命令行参数 `--memory-root`；
2. 环境变量 `SUPER_MEMORY_HOME`；
3. 已经包含 `memory.config.json` 的源码检出根目录；
4. 用户目录下的 `~/.super-memory`。

## 命令行用法

以下示例使用默认记忆目录；需要指定目录时，在脚本路径后增加 `--memory-root <目录>`。

```powershell
# 初始化规则库
python ./skills/super-memory/scripts/memory_cli.py init

# 校验当前规则
python ./skills/super-memory/scripts/memory_cli.py validate

# 重建派生索引
python ./skills/super-memory/scripts/memory_cli.py reindex

# 返回相关规则的摘要，兼容原有调用方式
python ./skills/super-memory/scripts/memory_cli.py retrieve --query "C++ 网络 序列化" --limit 8

# 编码前按实际项目、模块和语言检索，读取完整要求与例外
python ./skills/super-memory/scripts/memory_cli.py retrieve `
  --query "异步回调 shared_ptr 生命周期" `
  --project my-project `
  --module my-module `
  --language cpp `
  --format context

# 将已经审核的完整规则草案保存为 active
python ./skills/super-memory/scripts/memory_cli.py save `
  --input "./rule-draft.json" `
  --status active `
  --actor user `
  --reason "用户明确选择保存为 active"
```

### 检索范围与输出预算

示例中的项目和模块须替换成规则库中的实际 ID。未提供的作用域条件不会自动推断，跨项目检索的结果仍需核对适用范围。

| 参数 | 用途 |
| --- | --- |
| `--project` | 当前项目 ID；保留适用的全局、语言和技术规则 |
| `--module` | 当前模块 ID，必须同时提供项目；可重复传入 |
| `--language` / `--technology` | 按语言或技术过滤；可重复传入 |
| `--format context` | 返回规则正文、例外、来源、更新时间和匹配原因 |
| `--limit` | 普通匹配规则数量上限，默认 8 |
| `--max-chars` | context 输出字符预算，默认 16000，最小 512 |
| `--include-candidates` | 复盘时同时检索候选规则，避免重复创建；候选规则不约束代码 |

指定项目但未指定模块时，只返回项目及更广范围的规则。context 输出中的 `omitted_count` 和 `warnings` 会说明因数量或字符预算遗漏的规则；需要时缩小查询范围或提高预算，正文和例外不会被截断。

经用户确认某规则在其适用范围内始终生效后，可设置可选字段 `trigger.always_apply: true`。这类 active 规则在提供完整适用上下文后不受普通数量上限影响；字符预算不足会报错，需要增大预算。

### 更新规则与保留证据

`save` 要求中文保存原因，支持 `active`、`candidate`、`suspect`、`deprecated`、`superseded`、`archived`。首次审核通常选择 active 或 candidate，其余状态用于后续维护。规则字段以 [Schema](./skills/super-memory/assets/memory-rule.schema.json) 为准，审核流程见 [Skill 定义](./skills/super-memory/SKILL.md)。

更新已有规则时，使用读取时的更新时间防止覆盖其他修改：

```powershell
python ./skills/super-memory/scripts/memory_cli.py save `
  --input "./rule-draft.json" `
  --mode update `
  --expected-updated-at "<读取时的 updated_at>" `
  --reason "用户确认更新规则"
```

- 更新时间可从 context 的 `updated_at` 或完整规则的 `lifecycle.updated_at` 取得；没有时间时传入字面量 `null`。版本冲突后先重新读取、合并，再保存。管理台编辑自动执行该检查。
- `--mode create` 只允许新增，`--mode update` 只允许更新；省略时为 `upsert`，按 ID 新增或更新。`--json` 返回保存后的完整规则。
- 更新会合并已有证据并按证据 ID 去重。已有证据内容不可覆盖；需要纠正时用新证据 ID 追加修正说明。
- CLI 与管理台共用读写锁和保存入口。保存采用单文件原子替换，并在操作异常时恢复规则、索引和事件。
- 保存不会自动刷新 `last_validated_at`；它表示规则经过实践验证的时间。`review_after` 到期表示需要复查，不会自动让规则失效。

### 导入、导出与回收

```powershell
# 输出包含规则和作用域的 JSON 快照
python ./skills/super-memory/scripts/memory_cli.py list --export

# 导入管理台导出的 JSON 文件；已有 ID 默认跳过
python ./skills/super-memory/scripts/memory_cli.py import --input "./rules-export.json"

# 移入本地回收区，并更新索引和事件
python ./skills/super-memory/scripts/memory_cli.py delete `
  --id RULE-EXAMPLE `
  --reason "用户确认删除该规则"
```

导入前会校验整批规则，随后在一次事务中提交规则和作用域；`--overwrite` 允许更新已有 ID，但仍保留旧证据。需要保存导出文件时可直接使用管理台的导出功能；自行保存命令输出时应使用 UTF-8 无 BOM 编码。

## 环境要求

- Python 3.10 或更高版本；
- Node.js 22.13 或更高版本（只有可视化管理台需要）。

## 开发验证

在仓库根目录运行：

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
node --test tests/api-smoke.test.mjs
```

测试使用临时规则库，覆盖检索、作用域、证据保留、并发保存、版本冲突、失败回滚及管理台导入导出，不会改写现有规则。前端校验命令为 `npm --prefix web run lint` 和 `npm --prefix web run build`，需要先安装 Web 依赖。

## 本地规则与开源代码隔离

本仓库只提交程序、Skill 定义、数据结构、通用界面和说明图片。实际使用时产生的本地规则、审核记录、索引、日志和项目配置不会随 Git 提交。

图中可见的项目标识和规则片段仅用于功能演示；对应的结构化规则文件并未提交。

源码检出根目录中的这些路径已经由 `.gitignore` 排除：

```text
memory.config.json
graph/
history/
indexes/
schemas/
logs/
```

注意：

- 不要使用 `git add -f` 强制提交本地规则；
- 不要把密码、令牌、私钥、完整源码或不必要的业务数据写入规则；
- 规则证据只保留最小必要摘要或可追溯引用；
- 发布前仍应检查 Git 变更，确认没有误加入本地数据。

## 许可证

代码以 [MIT License](./LICENSE) 开源。
