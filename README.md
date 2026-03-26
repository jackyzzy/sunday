# Sunday

> 你的个人边端 AI 智能体，运行在本地电脑，帮你完成日常办公的 AI 自动化任务。

Sunday 是一个**本地优先（local-first）**的个人 AI 智能体。以终端为主要交互界面，通过 THINK → PLAN → EXECUTE → VERIFY 完整循环，帮你处理邮件、日历、文件、代码等日常工作任务。数据全程不离开本机。

---

## 目录

- [特性](#特性)
- [快速开始](#快速开始)
- [CLI 用法](#cli-用法)
- [TUI 操作](#tui-操作)
- [技能系统](#技能系统)
- [记忆系统](#记忆系统)
- [配置参考](#配置参考)
- [邮件与日历配置](#邮件与日历配置)
- [定时任务](#定时任务)
- [架构概览](#架构概览)
- [目录结构](#目录结构)
- [开发指南](#开发指南)

---

## 特性

- **本地优先** — 运行在你的个人电脑，对话历史、记忆文件不上传任何云端
- **守护进程 + TUI** — Gateway 后台长驻，TUI 随时 attach/detach，不丢失上下文
- **完整推理循环** — THINK → PLAN → DECOMPOSE → EXECUTE（ReAct）→ VERIFY，不可跳过
- **持久记忆** — Markdown 文件记忆系统，SOUL / MEMORY / 每日日志分层存储，越用越懂你
- **不可逆操作保护** — 发邮件、写文件、推代码等操作必须经过用户确认
- **技能扩展** — SKILL.md 指令包，按需懒加载，不污染上下文
- **模型无关** — 支持 Anthropic Claude、OpenAI、Google Gemini、本地 Ollama

---

## 快速开始

### 1. 安装

```bash
# 需要 Python 3.12+，推荐 uv 管理依赖
pip install uv

git clone https://github.com/yourname/sunday
cd sunday
uv sync
```

### 2. 配置 API Key

```bash
cp .env.example .env
# 编辑 .env，填入至少一个 API Key
```

`.env` 示例：

```
ANTHROPIC_API_KEY=sk-ant-...
# 可选
OPENAI_API_KEY=sk-...
GOOGLE_API_KEY=AIza...
# 网络搜索（可选）
TAVILY_API_KEY=tvly-...
```

### 3. 验证安装

```bash
uv run sunday --version   # 输出 0.1.0
uv run sunday run "你好，介绍一下你自己"
```

### 4. 启动 TUI（推荐）

```bash
# 方式一：直接启动（自动连接 Gateway）
uv run sunday tui

# 方式二：先启动 Gateway 守护进程，再启动 TUI
uv run sunday gateway start
uv run sunday tui
```

---

## CLI 用法

### 单次任务

```bash
uv run sunday run "帮我列出当前目录下所有 Python 文件"

# 指定思考深度（off / minimal / low / medium / high）
uv run sunday run "分析这段代码的性能问题" --thinking high

# 临时切换模型
uv run sunday run "翻译以下内容" --model openai/gpt-4o
```

### Gateway 管理

```bash
uv run sunday gateway start    # 后台启动守护进程
uv run sunday gateway status   # 查看运行状态
uv run sunday gateway stop     # 停止守护进程
```

### 记忆管理

```bash
uv run sunday memory show MEMORY.md      # 查看长期记忆
uv run sunday memory show SOUL.md        # 查看身份配置
uv run sunday memory search "项目"        # 搜索记忆内容
```

### 技能管理

```bash
uv run sunday skills list     # 列出所有已发现的技能包
```

---

## TUI 操作

启动后进入全屏终端界面。

### 基本对话

直接在底部输入框输入任务，回车发送。Agent 会展示思考过程、执行步骤和最终结果。

### 键盘快捷键

| 快捷键 | 功能 |
|--------|------|
| `Ctrl+P` | 提示切换会话（输入 `/sessions` 查看列表） |
| `Ctrl+L` | 提示切换模型（输入 `/model` 命令） |
| `Ctrl+T` | 提示切换思考深度（输入 `/think` 命令） |
| `Escape` | 中止当前运行中的任务 |

### Slash 命令

在输入框中输入 `/` 开头的命令：

| 命令 | 说明 |
|------|------|
| `/help` | 显示所有可用命令 |
| `/new` | 创建新会话 |
| `/sessions` | 列出所有历史会话 |
| `/session <id>` | 切换到指定会话 |
| `/think <level>` | 设置思考深度（off/minimal/low/medium/high） |
| `/model <provider/id>` | 临时切换模型 |
| `/abort` | 中止当前任务（同 Escape） |
| `/memory` | 查看当前记忆摘要 |
| `/skills` | 列出已加载的技能 |
| `/reset` | 清空当前会话上下文 |

---

## 技能系统

技能（Skill）是给 Agent 的操作指南，告诉它"如何使用某类工具"。

### 内置技能

| 技能 | 功能 |
|------|------|
| `files` | 文件读写、目录列表、内容全文搜索、批量重命名 |
| `web_search` | 网络搜索（Tavily API）、URL 内容抓取 |
| `code` | 执行 Python 代码片段 |
| `email` | Gmail 收件箱管理、邮件阅读与发送（需 OAuth2） |
| `calendar` | Google 日历事件查看与创建（需 OAuth2） |

### 添加自定义技能

在 `skills/` 目录下新建文件夹，包含两个文件：

```
skills/my_skill/
├── SKILL.md     # 技能描述（frontmatter + 使用说明）
└── tools.py     # 工具实现（async 函数）
```

`SKILL.md` frontmatter 格式：

```markdown
---
name: my_skill
description: 我的自定义技能，用于 XXX
version: "1.0"
requires: []
author: your_name
---

# 我的技能

## 能力
- **do_something(param)**：做某件事
```

---

## 记忆系统

Sunday 使用分层文件记忆，所有文件存储在 `~/.sunday/workspace/`（生产）或 `workspace/`（开发）。

### 记忆层级

| 文件 | 层级 | 内容 | 更新频率 |
|------|------|------|----------|
| `SOUL.md` | L0 永久 | 身份、性格、工作原则 | 手动编辑 |
| `AGENTS.md` | L0 永久 | 操作规则、安全边界 | 手动编辑 |
| `MEMORY.md` | L1 长期 | 跨会话关键记忆 | AI 自动整合 |
| `USER.md` | L1 长期 | 用户画像与偏好 | AI 自动更新 |
| `memory/YYYY-MM-DD.md` | L2 每日 | 当天操作日志 | 每次会话写入 |
| `sessions/*.jsonl` | 临时 | 完整会话转录 | 每次会话生成 |

### 个性化配置

直接编辑 `workspace/SOUL.md` 修改 Sunday 的性格和工作原则。这是用户配置领域，AI 不会自动修改此文件。

```bash
# 用你喜欢的编辑器打开
vim ~/.sunday/workspace/SOUL.md
```

### 记忆整合

每日凌晨（默认 4:00）自动运行整合脚本，清理过期日志、更新 MEMORY.md：

```bash
# 手动触发
uv run python scripts/memory_consolidate.py
```

---

## 配置参考

主配置文件：`configs/agent.yaml`

```yaml
agent:
  name: Sunday
  workspace_dir: ~/.sunday/workspace   # 记忆工作区
  sessions_dir: ~/.sunday/sessions     # 会话存储

model:
  provider: anthropic                  # anthropic | openai | google | ollama
  id: claude-opus-4-5                  # 模型 ID
  temperature: 0.2
  max_tokens: 8192
  base_url: null                       # 本地 Ollama 填写 URL，如 http://localhost:11434

reasoning:
  max_steps: 10                        # ReAct 最大步数
  thinking_level: medium               # off | minimal | low | medium | high
  thinking_budget_tokens: 4096

memory:
  consolidation_cron: "0 4 * * *"     # 整合定时规则（cron 语法）
  log_retention_days: 30              # 每日日志保留天数
  l0_max_lines: 100                   # 注入上下文的最大行数

tools:
  default_timeout: 30                 # 工具执行超时（秒）
  max_output_chars: 4096              # 工具输出最大字符数
  sandbox_mode: true                  # 启用沙箱模式
  deny_list:                          # 禁止执行的命令模式
    - "rm -rf"
    - "dd if="

tasks:
  daily_brief:
    description: "每日简报：汇总未读邮件、日历事件、待处理事项"
    steps:
      - "列出今天未读邮件"
      - "读取今天日历事件"
      - "生成简洁日报摘要"
      - "写入今日日志"
```

### 切换模型

修改 `configs/agent.yaml` 的 `model` 节：

```yaml
# 使用 OpenAI
model:
  provider: openai
  id: gpt-4o

# 使用本地 Ollama
model:
  provider: ollama
  id: llama3.2
  base_url: http://localhost:11434
```

---

## 邮件与日历配置

邮件（Gmail）和日历（Google Calendar）技能需要 Google OAuth2 授权。

### 步骤

1. 在 [Google Cloud Console](https://console.cloud.google.com/) 创建项目
2. 启用 Gmail API 和 Google Calendar API
3. 创建 OAuth2 凭证（桌面应用类型），下载 JSON 文件
4. 将凭证文件放到指定位置：

```bash
mkdir -p ~/.sunday/credentials
cp ~/Downloads/credentials.json ~/.sunday/credentials/gmail_credentials.json

# 或通过环境变量指定任意路径
# .env 中添加：GOOGLE_CREDENTIALS_FILE=/path/to/credentials.json
```

5. 首次运行邮件/日历命令时，会自动打开浏览器完成授权，token 自动保存

```bash
uv run sunday run "查看今天未读邮件"
# 浏览器弹出授权页面 → 完成授权
```

---

## 定时任务

### 每日简报

手动触发：

```bash
uv run python scripts/task_runner.py daily_brief
uv run python scripts/task_runner.py --list   # 查看所有任务
```

配置 cron 自动触发（Linux/macOS）：

```bash
crontab -e

# 每天工作日早上 9:00 运行简报
0 9 * * 1-5 cd /path/to/sunday && uv run python scripts/task_runner.py daily_brief

# 每天凌晨 4:00 整合记忆
0 4 * * * cd /path/to/sunday && uv run python scripts/memory_consolidate.py
```

---

## 架构概览

```
用户
  │
  ├── TUI（Textual 终端界面）
  │     └── WebSocket ──────────────┐
  │                                 ▼
  └── CLI（Click 命令行）     Gateway（本地守护进程）
                                    │
                              Session Manager
                              （JSONL 持久化）
                                    │
                              Agent Loop（每会话独立）
                              ┌─────┴──────────┐
                              │                │
                           Planner         Executor（ReAct）
                        （THINK + PLAN）   （EXECUTE）
                              │                │
                           Verifier       Tool Registry
                           （VERIFY）     ├── CLI / File Tools
                              │           ├── Web Search
                           Memory         ├── Email (Gmail)
                           Manager        ├── Calendar
                                          └── MCP Servers
```

**关键设计原则**：
- `agent/` 不依赖 `gateway/`，通过注入的 `emit` 回调通信
- `is_dangerous=True` 的工具执行前必须等待用户确认
- 所有写操作通过 `.tmp` 文件原子替换，不产生半写状态
- 记忆文件使用文件系统，无外部数据库依赖

---

## 目录结构

```
sunday/
├── .env.example               # 环境变量模板
├── pyproject.toml             # 项目依赖（uv）
├── configs/
│   ├── agent.yaml             # 主配置：模型、记忆、工具、任务
│   ├── mcp_servers.yaml       # MCP 服务器定义
│   └── prompts/               # 系统提示文件
├── skills/                    # 技能包
│   ├── files/                 # 文件操作
│   ├── web_search/            # 网络搜索
│   ├── code/                  # 代码执行
│   ├── email/                 # Gmail
│   └── calendar/              # Google 日历
├── workspace/                 # 开发用工作区（生产在 ~/.sunday/workspace/）
│   ├── SOUL.md                # 身份与人格（用户可自定义）
│   ├── AGENTS.md              # 操作规则
│   ├── MEMORY.md              # 长期记忆模板
│   ├── USER.md                # 用户画像模板
│   └── TOOLS.md               # 工具使用约定
├── scripts/
│   ├── task_runner.py         # 定时任务运行器
│   └── memory_consolidate.py  # 记忆整合脚本
├── src/sunday/
│   ├── config.py              # 配置加载（Pydantic）
│   ├── cli.py                 # CLI 入口（Click）
│   ├── agent/                 # Agent 核心
│   │   ├── loop.py            # 主循环
│   │   ├── planner.py         # 规划器
│   │   ├── executor.py        # 执行器（ReAct）
│   │   ├── verifier.py        # 验证器
│   │   └── models.py          # 数据模型
│   ├── memory/                # 记忆管理
│   │   ├── manager.py
│   │   ├── context.py
│   │   └── janitor.py
│   ├── tools/                 # 工具系统
│   │   ├── registry.py
│   │   ├── guard.py
│   │   └── cli_tool.py
│   ├── skills/
│   │   └── loader.py
│   ├── gateway/               # WebSocket 守护进程
│   │   ├── server.py
│   │   ├── session.py
│   │   └── protocol.py
│   └── tui/                   # 终端界面
│       ├── app.py
│       ├── commands.py
│       └── widgets/
└── tests/
    ├── unit/
    └── integration/
```

---

## 开发指南

### 运行测试

```bash
# 全量测试
uv run pytest tests/ -v

# 仅单元测试（快，无网络）
uv run pytest tests/unit/ -v

# 仅集成测试
uv run pytest tests/integration/ -v
```

### 代码检查

```bash
uv run ruff check src/ tests/ skills/
uv run ruff check src/ tests/ skills/ --fix
```

### 添加依赖

```bash
uv add some-package
uv add some-dev-package --dev
```

---

## License

MIT
