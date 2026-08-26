# Super Memory

Super Memory 是一套面向 Codex 开发流程的持久化记忆工具，包含：

- 一个负责检索、复盘、审核和维护开发规则的 Codex Skill；
- 一套确定性的 Python 命令行工具；
- 一个与命令行共用同一份数据的本地可视化管理台。

本仓库只提交程序、Skill 定义、数据结构和通用界面。你的本地规则、审核记录、索引及项目配置属于私有数据，不纳入 Git，也不随开源仓库发布。

> Super Memory 是社区项目，并非 OpenAI 官方项目。

## 工作方式

Super Memory 将 `graph/rules/active`、`graph/rules/candidate` 和 `graph/rules/archive` 中的规则 JSON 作为唯一事实源。Codex、命令行和可视化管理台直接读写同一个记忆目录，不需要同步或维护第二份副本；`indexes/rules.json` 仅是可以随时重建的派生索引。

新经验不会自动写入规则库。Super Memory 会先生成中文审核草案，只有在用户明确选择“保存为 active”或“保存为 candidate”后才执行保存；未确认或选择“不保存”时不会落库。

## 环境要求

- Python 3.10 或更高版本
- Node.js 22 或更高版本（包含 npm）

## 记忆数据放在哪里

有两种常见用法：

| 用法 | 记忆目录 | 适用场景 |
| --- | --- | --- |
| 本地源码检出 | 仓库根目录 | 继续维护本项目，同时把程序提交到本地 Git |
| 安装发行版 | `~/.super-memory` | 从发布仓库安装 Skill 后直接使用 |

记忆目录按以下顺序确定：命令行参数 `--memory-root`、环境变量 `SUPER_MEMORY_HOME`、已经存在 `memory.config.json` 的源码检出根目录、`~/.super-memory`。

当记忆放在仓库根目录时，会出现以下私有内容：

```text
memory.config.json
graph/
history/
indexes/
schemas/
```

这些路径由仓库的 `.gitignore` 排除。不要使用 `git add -f` 强制提交它们；公开代码前也应再次检查 Git 待提交文件，确保没有规则数据、源码片段、密钥或内部项目路径。

## 初始化

在仓库根目录执行以下命令，可以将本地记忆直接初始化在当前仓库中：

```powershell
python ./skills/super-memory/scripts/memory_cli.py --memory-root . init
```

如果希望使用默认的用户级目录，则省略 `--memory-root`：

```powershell
python ./skills/super-memory/scripts/memory_cli.py init
```

也可以将 Skill、命令行和管理台统一指向自定义目录：

```powershell
$env:SUPER_MEMORY_HOME = "D:\my-super-memory"
python ./skills/super-memory/scripts/memory_cli.py init
```

在 macOS 或 Linux 中可使用：

```bash
export SUPER_MEMORY_HOME="$HOME/my-super-memory"
python3 ./skills/super-memory/scripts/memory_cli.py init
```

`init --force` 只会覆盖通用配置和规则 Schema 模板，不会删除已有规则、索引或事件记录。

## 启动可视化管理台

源码检出根目录已经包含 `memory.config.json` 时，直接运行一键启动程序：

```powershell
.\start-super-memory.cmd
```

也可以在 PowerShell、macOS 或 Linux 中分别运行 `./start-super-memory.ps1`、`./start-super-memory.sh`，或直接执行 `node ./start-web.mjs`。首次启动会安装 Web 依赖。页面与本地 API 默认只在本机回环地址运行，并直接使用同一个唯一规则库。

管理台支持新增、编辑、移入回收区，以及通过 JSON 文件导入和导出规则。导出内容不包含本机绝对目录配置。

## 命令行

以下示例使用默认记忆目录；源码检出模式可在脚本路径后统一加上 `--memory-root .`。

```powershell
# 校验当前规则
python ./skills/super-memory/scripts/memory_cli.py validate

# 重建派生索引
python ./skills/super-memory/scripts/memory_cli.py reindex

# 编码前检索相关的生效规则
python ./skills/super-memory/scripts/memory_cli.py retrieve --query "C++ 网络 序列化" --limit 8

# 将已审核的完整规则草案保存为 active
python ./skills/super-memory/scripts/memory_cli.py save `
  --input "./rule-draft.json" `
  --status active `
  --actor user `
  --reason "用户明确选择保存为 active"
```

`save` 会校验完整规则、原子写入数据、记录事件并重建索引。`save` 只接受 `active` 或 `candidate`，并要求提供中文保存原因。

## 安装到 Codex

仓库发布到 GitHub 后，可以让 Codex 使用 `$skill-installer` 从你的仓库安装 `skills/super-memory` 子目录。请将下面的占位符替换为真实仓库信息：

```text
使用 $skill-installer 从 GitHub 仓库 <GitHub账号>/<仓库名> 的 skills/super-memory 子目录安装 Super Memory
```

安装后首次运行 `init` 即可创建默认的 `~/.super-memory`。如需复用已有规则库，请在启动 Codex 和管理台时设置相同的 `SUPER_MEMORY_HOME`。

## 数据与隐私边界

- 开源范围只包括通用功能，不包括任何本地规则数据。
- 不要将密码、令牌、私钥、完整源码或不必要的业务数据写入规则。
- 规则证据应保存最小必要摘要或可追溯引用，避免复制敏感内容。
- `graph/rules/trash` 是本地回收区，不参与校验、索引或检索。
- `.gitignore` 只能阻止未跟踪文件被普通 Git 操作加入；发布前仍需人工检查提交内容。

## 许可证

代码以 [MIT License](./LICENSE) 开源。
