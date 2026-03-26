# Sunday

> 你的个人边端智能体，运行在本地电脑，帮你完成日常办公的 AI 自动化任务。

Sunday 是一个本地优先（local-first）的个人 AI 智能体，灵感来自 OpenClaw。它以终端为主要交互界面，通过 ReAct + Reasoning 混合推理模式，完成思考 → 计划 → 拆分 → 执行 → 验证的完整任务循环，帮你处理日常工作中的重复性、复杂性任务。

---

## 特性

- **本地优先** — 运行在你的个人电脑，数据不离开本机，支持本地模型（Ollama）
- **边端智能体** — 长驻后台守护进程 + 轻量 TUI 客户端，随时 attach/detach
- **混合推理** — ReAct + 扩展思考（Extended Thinking），思考→计划→拆分→执行→验证循环
- **持久记忆** — 基于 Markdown 文件的 Workspace 记忆系统（SOUL / MEMORY / 每日日志）
- **技能扩展** — SKILL.md 指令包，按需懒加载，不污染上下文
- **工具调用** — Python 原生工具、CLI 命令封装、MCP 协议服务器
- **配置分离** — 模型 key、角色定义、任务配置与实现代码完全分离
- **模型无关** — 支持 Anthropic Claude、OpenAI、Google Gemini、本地 Ollama

---

## 快速开始

### 安装依赖

```bash
# 推荐使用 uv 管理依赖
pip install uv
uv sync
```

### 配置

```bash
cp .env.example .env
# 编辑 .env，填入你的 API Key
```

### 启动

```bash
# 交互式 TUI（主要使用方式）
uv run sunday tui

# 单次任务
uv run sunday run "帮我整理今天的邮件，生成摘要"

# 后台守护进程
uv run sunday gateway start
```

---

## 架构概览

```
┌─────────────────────────────────────────────────────────────┐
│                        Sunday Agent                         │
│                                                             │
│  TUI / CLI  ──────▶  Gateway (本地进程)  ──────▶  Agent Loop │
│                           │                        │        │
│                    Session Store                   │        │
│                    (JSONL 转录)              ┌──────┴──────┐ │
│                                             │  THINK      │ │
│  ~/.sunday/workspace/                       │  PLAN       │ │
│  ├── SOUL.md          (身份/人格)            │  DECOMPOSE  │ │
│  ├── AGENTS.md        (操作规则)             │  EXECUTE    │ │
│  ├── MEMORY.md        (长期记忆)             │  VERIFY     │ │
│  ├── USER.md          (用户画像)             └──────┬──────┘ │
│  ├── TOOLS.md         (工具约定)                    │        │
│  └── memory/                                       │        │
│      └── YYYY-MM-DD.md (每日日志)                   │        │
│                                              Tools Layer    │
│  skills/                                    ├── Python Tools│
│  ├── email/SKILL.md                         ├── CLI Tools   │
│  ├── calendar/SKILL.md                      └── MCP Servers │
│  └── files/SKILL.md                                        │
└─────────────────────────────────────────────────────────────┘
```

---

## 目录结构

```
sunday/
├── .env.example               # 环境变量模板
├── pyproject.toml             # 项目依赖（uv）
├── CLAUDE.md                  # Claude Code 协作规则
├── specs.md                   # 需求规格说明
│
├── configs/                   # 配置文件（可版本控制）
│   ├── agent.yaml             # 主配置：模型、记忆、工具、角色
│   ├── mcp_servers.yaml       # MCP 服务器定义
│   └── prompts/               # 系统提示文件
│
├── skills/                    # 技能包
│   ├── email/SKILL.md
│   ├── calendar/SKILL.md
│   └── files/SKILL.md
│
├── workspace/                 # 智能体工作区（记忆）
│   ├── SOUL.md
│   ├── AGENTS.md
│   ├── MEMORY.md
│   ├── USER.md
│   └── memory/
│
├── src/sunday/                # 核心实现
│   ├── config.py              # 配置加载（Pydantic）
│   ├── agent/                 # 智能体核心
│   │   ├── loop.py            # 主循环（think→plan→execute→verify）
│   │   ├── planner.py         # 思考与计划
│   │   ├── executor.py        # ReAct 执行
│   │   └── verifier.py        # 结果验证
│   ├── memory/                # 记忆管理
│   │   ├── manager.py
│   │   └── janitor.py
│   ├── tools/                 # 工具系统
│   │   ├── registry.py
│   │   ├── cli.py
│   │   └── mcp.py
│   ├── skills/                # 技能加载器
│   │   └── loader.py
│   ├── gateway/               # 本地守护进程
│   │   └── server.py
│   └── tui/                   # 终端界面
│       └── app.py
│
└── tests/
```

---

## 技术栈

| 层级 | 选型 |
|------|------|
| 语言 | Python 3.12+ |
| Agent 框架 | Agno |
| 配置管理 | pydantic-settings + YAML |
| 记忆存储 | 文件系统（Markdown + JSONL） |
| TUI | Textual |
| 依赖管理 | uv |
| MCP 客户端 | agno 内置 |

---

## License

MIT
