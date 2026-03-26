# Sunday — 技术设计文档

**版本**：v0.2
**日期**：2026-03-26
**依据**：specs.md v0.1

---

## 目录

1. [系统总体设计](#1-系统总体设计)
2. [目录结构](#2-目录结构)
3. [配置系统设计](#3-配置系统设计)
4. [Agent 执行循环设计](#4-agent-执行循环设计)
5. [记忆系统设计](#5-记忆系统设计)
6. [工具系统设计](#6-工具系统设计)
7. [技能系统设计](#7-技能系统设计)
8. [Gateway 设计](#8-gateway-设计)
9. [TUI 设计](#9-tui-设计)
10. [并发与异步模型](#10-并发与异步模型)
11. [错误处理策略](#11-错误处理策略)
12. [启动流程](#12-启动流程)
13. [依赖清单](#13-依赖清单)

---

## 1. 系统总体设计

### 1.1 架构全景图

```
┌──────────────────────────────────────────────────────────────────────┐
│                          用户个人电脑                                  │
│                                                                      │
│   ┌─────────────┐    WebSocket     ┌───────────────────────────────┐ │
│   │   TUI/CLI   │ ◀──────────────▶ │         Gateway               │ │
│   │  (客户端)    │                  │    (本地守护进程 :7899)         │ │
│   └─────────────┘                  │                               │ │
│                                    │  ┌─────────┐  ┌────────────┐  │ │
│                                    │  │ Session │  │   Config   │  │ │
│                                    │  │ Manager │  │   Loader   │  │ │
│                                    │  └─────────┘  └────────────┘  │ │
│                                    └──────────┬────────────────────┘ │
│                                               │ 调度                  │
│                                    ┌──────────▼────────────────────┐ │
│                                    │        Agent Loop             │ │
│                                    │  THINK→PLAN→EXECUTE→VERIFY    │ │
│                                    │                               │ │
│                                    │  ┌─────────┐  ┌───────────┐  │ │
│                                    │  │ Planner │  │ Executor  │  │ │
│                                    │  └─────────┘  └───────────┘  │ │
│                                    │       ┌────────────────────┐  │ │
│                                    │       │     Verifier       │  │ │
│                                    │       └────────────────────┘  │ │
│                                    └──────────┬────────────────────┘ │
│                                               │                      │
│                    ┌──────────────────────────┼───────────────┐      │
│                    ▼                          ▼               ▼      │
│           ┌─────────────────┐    ┌──────────────────┐  ┌──────────┐ │
│           │   Tool Layer    │    │   Memory Layer   │  │  Skills  │ │
│           │                 │    │                  │  │  Loader  │ │
│           │ ┌─────────────┐ │    │ ~/.sunday/       │  └──────────┘ │
│           │ │Python Tools │ │    │  workspace/      │               │
│           │ ├─────────────┤ │    │  ├── SOUL.md     │               │
│           │ │  CLI Tools  │ │    │  ├── MEMORY.md   │               │
│           │ ├─────────────┤ │    │  ├── USER.md     │               │
│           │ │  MCP Tools  │ │    │  └── memory/     │               │
│           │ └─────────────┘ │    │                  │               │
│           └─────────────────┘    │  sessions/       │               │
│                                  │  └── *.jsonl     │               │
│                                  └──────────────────┘               │
│                                                                      │
│   外部服务: Anthropic API / OpenAI API / Ollama(本地)                 │
└──────────────────────────────────────────────────────────────────────┘
```

### 1.2 模块依赖关系

设计原则：**依赖方向单向向下，禁止反向依赖**。

```
         cli.py
            │
            ▼
        gateway/               ← 顶层，组装所有依赖
         ├── server.py
         ├── session.py
         └── protocol.py
            │
            ▼
         agent/                ← 核心循环，不依赖 gateway
         ├── loop.py           (通过注入的 emit 回调与 gateway 通信)
         ├── planner.py
         ├── executor.py
         └── verifier.py
            │
       ┌────┴────┐
       ▼         ▼
    memory/    tools/          ← 基础层，不互相依赖
    ├── manager.py   ├── registry.py
    ├── context.py   ├── guard.py
    └── janitor.py   ├── cli_tool.py
                     └── mcp_client.py
                          │
                      skills/loader.py   ← 最底层，无内部依赖

    config.py          ← 横切关注点，所有层均可依赖
```

**关键约束**：
- `agent/` 不得 `import` 任何 `gateway/` 模块
- `AgentLoop` 通过构造时注入的 `emit: Callable` 回调向客户端推送事件，而非直接调用 gateway
- `memory/` 和 `tools/` 不得互相依赖

### 1.3 关键设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| Agent 框架 | Agno | 原生 MCP、记忆、结构化输出，API 简洁 |
| 通信方式 | WebSocket（本地） | TUI 可 attach/detach，支持双向流式推送 |
| 记忆存储 | 文件系统（Markdown） | 零依赖，人类可读，Git 可追踪 |
| 并发模型 | asyncio 单事件循环 | 无线程竞争，会话内串行保证一致性 |
| 配置格式 | YAML + .env | YAML 可读性好；密钥单独隔离 |
| TUI 框架 | Textual | 原生 async，组件化，适合流式渲染 |

---

## 2. 目录结构

```
sunday/
├── .env                        # 密钥（不提交 git）
├── .env.example                # 密钥模板
├── .gitignore
├── pyproject.toml
├── Makefile                    # 常用命令：run、test、lint、consolidate
│
├── configs/                    # 所有结构化配置（提交 git）
│   ├── agent.yaml              # 主配置：模型、记忆、工具、角色、定时任务
│   ├── mcp_servers.yaml        # MCP 服务器列表
│   └── prompts/                # 系统提示模板（Markdown）
│       ├── system.md           # 基础系统提示（含占位符）
│       └── verifier.md         # 验证阶段提示
│
├── skills/                     # 内置技能包
│   ├── files/
│   │   ├── SKILL.md            # 技能说明（纯 Markdown 指令）
│   │   └── tools.py            # 技能配套 Python 工具实现
│   ├── web_search/
│   │   ├── SKILL.md
│   │   └── tools.py
│   └── code/
│       ├── SKILL.md
│       └── tools.py
│
├── workspace/                  # 开发用工作区（生产在 ~/.sunday/workspace/）
│   ├── SOUL.md                 # 智能体身份（提交 git，用户编辑）
│   ├── AGENTS.md               # 操作规则（提交 git，用户编辑）
│   ├── MEMORY.md               # 长期记忆（提交 git，智能体写入）
│   ├── USER.md                 # 用户画像（提交 git，智能体写入）
│   ├── TOOLS.md                # 工具约定（提交 git）
│   └── memory/                 # 每日日志（.gitignore）
│
├── src/
│   └── sunday/
│       ├── __init__.py
│       ├── cli.py              # CLI 入口（Click）
│       ├── config.py           # 配置加载（Pydantic Settings）
│       │
│       ├── agent/
│       │   ├── models.py       # 共享数据模型（Plan、Step、StepResult 等）
│       │   ├── loop.py         # AgentLoop 主控制器
│       │   ├── planner.py      # Planner（THINK + PLAN + DECOMPOSE）
│       │   ├── executor.py     # Executor（ReAct 循环）
│       │   └── verifier.py     # Verifier（结果验证 + 摘要）
│       │
│       ├── memory/
│       │   ├── manager.py      # MemoryManager（文件读写）
│       │   ├── context.py      # ContextBuilder（系统提示组装）
│       │   └── janitor.py      # MemoryJanitor（TTL 清理）
│       │
│       ├── tools/
│       │   ├── registry.py     # ToolRegistry（注册、路由、确认）
│       │   ├── guard.py        # ToolResultGuard（输出验证）
│       │   ├── cli_tool.py     # CLI 命令封装工具
│       │   └── mcp_client.py   # MCP 客户端管理
│       │
│       ├── skills/
│       │   └── loader.py       # SkillLoader（发现与懒加载）
│       │
│       ├── gateway/
│       │   ├── server.py       # Gateway（WebSocket 服务 + 组装器）
│       │   ├── session.py      # SessionManager（JSONL 存储）
│       │   └── protocol.py     # 消息协议定义（EventType、Message）
│       │
│       └── tui/
│           ├── app.py          # Textual App 主体
│           ├── style.tcss      # TUI 样式
│           ├── commands.py     # Slash 命令处理器
│           └── widgets/
│               ├── chat_log.py
│               ├── tool_card.py
│               ├── status_bar.py
│               └── input_bar.py
│
├── scripts/
│   ├── memory_consolidate.py   # 每日记忆整合（cron 调用）
│   └── task_runner.py          # 定时任务执行器
│
└── tests/
    ├── unit/
    │   ├── test_config.py
    │   ├── test_planner.py
    │   ├── test_executor.py
    │   ├── test_verifier.py
    │   └── test_memory_manager.py
    └── integration/
        └── test_agent_loop.py
```

---

## 3. 配置系统设计

### 3.1 配置分层

```
┌─────────────────────────────────────────────────────────┐
│  Layer 1: 密钥层（.env）                                  │
│  ANTHROPIC_API_KEY=...  OPENAI_API_KEY=...               │
│  不提交 git，运行时通过 pydantic-settings 注入             │
├─────────────────────────────────────────────────────────┤
│  Layer 2: 主配置层（configs/agent.yaml）                  │
│  模型参数、记忆路径、工具策略、MCP 服务器、定时任务         │
│  提交 git，结构化，Pydantic 验证                           │
├─────────────────────────────────────────────────────────┤
│  Layer 3: 工作区层（workspace/*.md）                      │
│  SOUL.md / AGENTS.md / MEMORY.md / USER.md / TOOLS.md   │
│  用户直接编辑，智能体运行时读取，部分由智能体写入           │
└─────────────────────────────────────────────────────────┘
```

### 3.2 配置数据模型

`config.py` 使用 Pydantic 定义以下模型层级：

```
Settings（BaseSettings）
│  ├── anthropic_api_key: str      ← 来自 .env
│  ├── openai_api_key: str
│  ├── google_api_key: str
│  └── sunday: SundayConfig        ← 来自 agent.yaml（@cached_property）
│
SundayConfig
├── agent: AgentConfig
│   ├── name: str
│   ├── workspace_dir: Path        ← ~/.sunday/workspace（唯一来源）
│   └── sessions_dir: Path         ← ~/.sunday/sessions（唯一来源）
│
├── model: ModelConfig
│   ├── provider: str              # anthropic | openai | google | ollama
│   ├── id: str
│   ├── temperature: float
│   ├── max_tokens: int
│   └── base_url: str | None       # 自托管模型（Ollama）
│
├── reasoning: ReasoningConfig
│   ├── max_steps: int             # ReAct 最大迭代次数，默认 10
│   ├── thinking_level: str        # off|minimal|low|medium|high
│   └── thinking_budget_tokens: int
│
├── memory: MemoryConfig
│   ├── consolidation_cron: str    # 每日整合 cron，默认 "0 4 * * *"
│   ├── log_retention_days: int    # 每日日志保留天数，默认 30
│   └── l0_max_lines: int          # MEMORY.md 注入最大行数，默认 100
│
├── tools: ToolsConfig
│   ├── default_timeout: int       # 工具默认超时秒数，默认 30
│   ├── max_output_chars: int      # 工具输出截断阈值，默认 4096
│   ├── sandbox_mode: bool
│   ├── allow_list: list[str]      # 空=全部允许
│   └── deny_list: list[str]
│
├── mcp: MCPConfig
│   └── servers: list[MCPServerConfig]
│       ├── name, command, args, env, enabled
│
├── skills: SkillsConfig
│   └── extra_dirs: list[Path]
│
└── tasks: dict[str, TaskConfig]
    └── schedule, description, prompt, enabled
```

**重要约束**：
- `workspace_dir` 和 `sessions_dir` 仅在 `AgentConfig` 中定义，其他模块通过注入获得，不重复定义
- `Settings.sunday` 使用 `@cached_property`，只在首次访问时解析 YAML，后续复用缓存
- `Settings.get_api_key(provider)` 统一获取密钥，provider 不存在时抛出明确异常

### 3.3 配置加载时机

| 时机 | 加载内容 | 负责方 |
|------|----------|--------|
| Gateway 启动时 | 完整 `SundayConfig`，校验必填项 | `Gateway.__init__` |
| Gateway 启动时 | 初始化 MCP 连接，注册所有工具 | `Gateway._setup_tools` |
| TUI 会话创建时 | `AgentConfig`（路径），`ReasoningConfig` | `Gateway._build_state` |
| 每次 Agent Loop 开始 | `ModelConfig`（支持 `/model` 热切换） | `Planner.__init__` |
| 工具注册时 | `ToolsConfig`（allow/deny/timeout） | `ToolRegistry.__init__` |

---

## 4. Agent 执行循环设计

### 4.1 核心数据模型

```
AgentState                      ← 一次任务执行的完整状态，贯穿整个循环
├── session_id: str
├── task: str                   ← 用户原始输入
├── history: list[Message]      ← 本会话历史对话（用于多轮上下文）
├── plan: Plan | None
├── step_results: list[StepResult]
├── thinking_level: ThinkingLevel
└── aborted: bool

Plan                            ← Planner 输出，描述"做什么"
├── goal: str
├── thinking: str | None        ← THINK 阶段的思考内容（可选展示）
└── steps: list[Step]

Step                            ← 一个原子执行单元
├── id: str                     ← "step_1", "step_2"...
├── intent: str                 ← 这步要达成什么
├── expected_input: str
├── expected_output: str
├── success_criteria: str       ← Verifier 的判断依据
├── depends_on: list[str]       ← 依赖的 step id
└── status: StepStatus          ← PENDING|RUNNING|DONE|FAILED|SKIPPED

StepResult                      ← Executor 的输出
├── step_id: str
├── status: StepStatus
├── output: str
├── react_iterations: list[ReactIteration]
├── verified: bool              ← Verifier 填写
└── verify_reason: str          ← Verifier 填写

ReactIteration                  ← ReAct 单次循环记录
├── thought: str
├── tool_name: str
├── tool_input: dict
├── observation: str
└── iteration: int
```

### 4.2 Agent Loop 主控制流

```
┌─────────────────────────────────────────────────────────────┐
│                      AgentLoop.run(state)                   │
│                                                             │
│  emit(STATUS="thinking")                                    │
│       │                                                     │
│       ▼                                                     │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  PLANNER.think_and_plan(state)                      │   │
│  │  ① 加载 L0 上下文（ContextBuilder.build）            │   │
│  │  ② 扩展思考（按 thinking_level 分配 token budget）   │   │
│  │  ③ 结构化输出 Plan（LLM structured output）          │   │
│  │  ④ 写入 JSONL（type=plan）                          │   │
│  └──────────────────────┬──────────────────────────────┘   │
│                         │ plan.steps                        │
│                         ▼                                   │
│  ┌─── for step in steps（串行，依赖满足才执行）────────────┐  │
│  │                                                     │  │
│  │    emit(STATUS=f"executing:{step.id}")              │  │
│  │         │                                           │  │
│  │         ▼                                           │  │
│  │    ┌─────────────────────────────────────────────┐ │  │
│  │    │  EXECUTOR.run(step, state)                  │ │  │
│  │    │  ReAct 循环（最多 max_steps 次）              │ │  │
│  │    │  Thought → ToolCall → Observation → ...     │ │  │
│  │    └──────────────────┬──────────────────────────┘ │  │
│  │                       │ StepResult                  │  │
│  │                       ▼                             │  │
│  │    ┌─────────────────────────────────────────────┐ │  │
│  │    │  VERIFIER.check(step, result)               │ │  │
│  │    │  对照 success_criteria 判断是否通过           │ │  │
│  │    └────────────┬──────────────┬─────────────────┘ │  │
│  │                 │ 通过          │ 失败               │  │
│  │                 ▼              ▼                    │  │
│  │            继续下一步      should_replan?            │  │
│  │                            ├─ 是 → Planner.replan  │  │
│  │                            │      替换剩余步骤       │  │
│  │                            └─ 否 → 标记失败，继续    │  │
│  │    写入 JSONL（type=step_result）                    │  │
│  └─────────────────────────────────────────────────────┘  │
│                         │                                   │
│                         ▼                                   │
│  MEMORY.consolidate_session(state)  ← 后台异步，不阻塞返回  │
│  emit(STATUS="idle")                                        │
│  return VERIFIER.summarize(state)                           │
└─────────────────────────────────────────────────────────────┘
```

### 4.3 ReAct 执行循环（Executor）

```
Executor.run(step, state):

  messages = [step_prompt(step)]
  last_action = None

  for i in range(max_steps):
    response = LLM.chat(messages, tools=registry.schemas, temperature=0)

    if response.finish_reason == "stop":
      return StepResult(DONE, response.content)  ← 模型判断完成

    tool_call = response.tool_calls[0]

    if is_repetition(tool_call, last_action):
      raise RepetitionError  ← 连续相同调用，跳出

    observation = registry.execute(tool_call)    ← 含超时、确认、Guard

    messages += [assistant(tool_call), tool(observation)]
    last_action = tool_call

  raise MaxStepsError  ← 超出迭代上限
```

### 4.4 Planner 局部重规划

当 Verifier 判定失败且 `should_replan=True` 时，触发局部重规划：

```
Planner.replan(failed_step, result, state):
  输入：失败步骤、失败原因、已有执行结果
  输出：替代 failed_step 之后所有未执行步骤的新步骤列表
  约束：不调用任何外部工具；temperature=0.3
```

重规划后替换 `state.plan.steps` 中剩余未执行的步骤，继续循环。

### 4.5 emit 注入机制

`AgentLoop` 不直接依赖 `gateway`，通过构造时注入 `emit` 回调解耦：

```
# Gateway 创建 AgentLoop 时注入自身的 emit 方法
loop = AgentLoop(
    planner=planner,
    executor=executor,
    verifier=verifier,
    memory=memory,
    session=session,
    emit=self.emit,          ← 注入回调，类型: Callable[[str, EventType, dict], Awaitable]
)
```

`AgentLoop` 内部调用 `await self.emit(session_id, EventType.STATUS, {...})`，对 gateway 实现无感知。

---

## 5. 记忆系统设计

### 5.1 记忆层级与文件结构

```
记忆优先级（高 → 低）：

  P0 永久层          P1 长期层          P2 短期层          P3 临时层
  ──────────         ──────────         ──────────         ──────────
  SOUL.md            MEMORY.md          memory/            sessions/
  AGENTS.md          USER.md            YYYY-MM-DD.md      <id>.jsonl
                     TOOLS.md           （每日日志）        （会话转录）
  用户手动编辑        智能体写入          智能体写入          自动追加
  永不自动修改        长期保留            30天 TTL           50MB 归档

                    ← 均存储在 ~/.sunday/ 下 →
```

### 5.2 上下文组装（ContextBuilder）

每次 Agent Loop 开始时，`ContextBuilder.build()` 按以下顺序组装系统提示：

```
┌─────────────────────────────────────────────────────────────────┐
│              L0（始终注入，每次必读）                             │
│                                                                 │
│  1. SOUL.md          — 身份与人格（全文）                        │
│  2. AGENTS.md        — 操作规则（全文）                          │
│  3. MEMORY.md        — 长期记忆（最新 l0_max_lines 行，默认100）  │
│  4. USER.md          — 用户画像（全文）                          │
│  5. 昨日日志          — memory/昨天.md（全文，若存在）            │
│  6. 今日日志          — memory/今天.md（全文，若存在）            │
│  7. 技能摘要列表      — 名称+描述（不含完整内容）                 │
│  8. TOOLS.md         — 工具使用约定（全文）                      │
│  9. 当前日期          — date.today().isoformat()                │
├─────────────────────────────────────────────────────────────────┤
│              L1（按需注入，Agent 主动请求）                       │
│                                                                 │
│  load_skill(name) 工具触发 → 完整 SKILL.md 内容                 │
├─────────────────────────────────────────────────────────────────┤
│              L2（按需注入，历史溯源）                             │
│                                                                 │
│  get_memory_log(date) 工具触发 → 指定日期的 memory/日志          │
└─────────────────────────────────────────────────────────────────┘

组装规则：
- 各部分以 "---" 分隔
- 某文件不存在时静默跳过，不报错
- MEMORY.md 超出 l0_max_lines 时取末尾（最新）N 行
- 组装完成后返回 token 粗估（len // 4）供调用方判断
```

### 5.3 MemoryManager 接口

```
MemoryManager
│
├── append_daily_log(content: str) → None
│   追加内容到今日 memory/YYYY-MM-DD.md
│   原子性：先 append 到文件（追加写入，天然原子）
│
├── update_memory(section, key, value, priority="P1") → None
│   在 MEMORY.md 的指定 section 下插入或更新一条记忆
│   格式：- [P1][2026-03-26] key：value
│   原子性：读 → 修改 → 写 .tmp → rename
│   并发：asyncio.Lock 串行化所有写操作
│
├── update_user_profile(key, value) → None
│   在 USER.md 中插入或更新 key-value 条目
│   同上，原子写
│
├── consolidate_session(state: AgentState) → None
│   会话结束时调用：
│   ① 同步：将任务摘要追加到今日日志
│   ② 异步（asyncio.create_task）：调用 LLM 提炼
│      本次会话中值得写入 MEMORY.md / USER.md 的信息
│      后台执行，不阻塞返回给用户
│
└── _atomic_write(path, content) → None [private]
    先写 path.tmp，再 rename，保证写入原子性
```

### 5.4 会话存储（SessionManager）

```
SessionManager
│
├── new_session() → session_id
│   生成 uuid hex[:12]，写入 sessions/index.json
│
├── append(session_id, event_type, data) → None
│   追加一条 JSONL 记录（asyncio.Lock 防止并发写同一文件）
│   格式：{"type": "...", "ts": "...", ...data}
│
├── load_history(session_id, max_events=200) → list[dict]
│   读取最近 N 条事件，用于恢复会话上下文
│
└── list_sessions() → list[dict]
    从 index.json 读取，按 last_active 倒序

JSONL 事件类型：
  session_start | user | think | plan | step_result |
  tool_call | assistant | memory_update | session_end
```

### 5.5 记忆整合流程（AI Consolidation）

```
触发时机：每次会话结束后，在后台异步执行

流程：
  1. 读取本次会话的完整步骤结果（state.step_results）
  2. 调用 LLM，提示词要求：
     "从以下会话中提取值得长期记忆的事实、用户偏好、工具约定"
  3. LLM 返回结构化结果：
     { memories: [{section, key, value, priority}],
       user_profile: [{key, value}] }
  4. 依次调用 update_memory / update_user_profile 写入
  5. 写入结果记录到今日日志

此流程不影响用户等待时间，失败时仅记录日志，不抛出异常。
```

---

## 6. 工具系统设计

### 6.1 工具调用管道

```
Agent 请求调用工具
        │
        ▼
   ToolRegistry.execute(tool_call, session_id)
        │
        ├─ [1] 工具存在性检查
        │       未知工具 → 返回错误字符串（不抛异常，让 ReAct 感知）
        │
        ├─ [2] allow_list / deny_list 过滤
        │       被拒绝 → 返回拒绝说明字符串
        │
        ├─ [3] is_dangerous 检查（不可逆操作）
        │       是 → 向 Gateway 请求用户确认（await Future，60s 超时）
        │       用户拒绝 / 超时 → 返回"操作已取消"字符串
        │
        ├─ [4] 执行工具函数
        │       asyncio.wait_for(fn(**args), timeout=meta.timeout)
        │       超时 → 返回超时错误字符串
        │       异常 → 返回格式化异常字符串
        │
        ├─ [5] ToolResultGuard.validate(result)
        │       截断（> max_output_chars）
        │       过滤敏感信息（API key 等）
        │       非字符串转字符串
        │
        └─ [6] 记录到 JSONL（tool_call 事件）
                返回最终字符串给 Executor
```

### 6.2 三类工具对比

| 维度 | Python 原生工具 | CLI 工具 | MCP 工具 |
|------|----------------|----------|----------|
| 定义方式 | `@tool` 装饰器，Pydantic schema | `cli_tool.py` 统一封装 | MCP 服务器声明 |
| 执行环境 | 当前 Python 进程 | `asyncio.create_subprocess_shell` | MCP 子进程（JSON-RPC） |
| 超时控制 | `asyncio.wait_for` | 同左 | MCP 协议超时 |
| 适用场景 | 文件读写、文本处理、内部逻辑 | 系统命令、脚本执行 | 外部服务（GitHub、浏览器）|
| 注册时机 | Gateway 启动时静态注册 | 同左 | Gateway 启动时连接 MCP Server |

### 6.3 工具元数据（ToolMeta）

每个工具注册时须提供：

```
ToolMeta
├── name: str              ← 唯一标识，LLM 调用时使用
├── description: str       ← 告诉 LLM 何时用、何时不用（关键！）
├── input_schema: dict     ← JSON Schema，Pydantic 自动生成
├── is_dangerous: bool     ← true 时执行前请求用户确认
└── timeout: int           ← 覆盖默认超时
```

**描述质量直接影响工具选择准确性**，描述应包含：适用场景、不适用场景、示例输入。

### 6.4 不可逆操作确认机制

```
ToolRegistry                    Gateway                     TUI
     │                              │                        │
     │── is_dangerous=True ─────────▶                        │
     │                         创建 Future                   │
     │                         存入 pending_confirms          │
     │                              │── CONFIRM_REQUEST ────▶│
     │                              │                    用户 y/n
     │◀─────── Future.result() ─────│◀─── CONFIRM ──────────│
     │                              │                        │
  继续/取消                     resolve Future               │

超时（60s）→ Future 超时 → 视为拒绝 → 返回"操作已取消"
```

---

## 7. 技能系统设计

### 7.1 SKILL.md 格式规范

```markdown
---
name: web_search            # 唯一标识符（snake_case）
description: 网络搜索与信息提取，用于查询最新信息、调研话题
version: "1.0"
requires: []                # 依赖的 MCP 服务器名称或工具名称
author: sunday
---

# 技能标题

## 适用场景
何时应该加载并使用此技能

## 可用工具
- `tool_name(param)` — 工具描述

## 使用步骤
1. 步骤一
2. 步骤二

## 注意事项
使用限制和注意点
```

### 7.2 技能发现与加载流程

```
发现阶段（SkillLoader.discover，启动时执行一次）：

  搜索路径优先级（高 → 低）：
  ① <workspace>/skills/     ← 用户安装的技能，最高优先级
  ② configs/skills.extra_dirs（可选）
  ③ ./skills/               ← 项目内置技能

  逐目录扫描 */SKILL.md，解析 frontmatter，
  同名技能取最高优先级版本（不重复加载）
  requires 不满足的技能自动跳过


注入阶段（L0 系统提示，每次 Agent Loop）：

  技能摘要格式（仅名称+描述）：
  "可用技能：
   - web_search: 网络搜索与信息提取...
   - files: 文件和目录操作...
   使用 load_skill(name) 工具加载完整说明。"


懒加载阶段（Agent 运行时按需）：

  Agent 调用 load_skill("web_search")
    → SkillLoader.load_full("web_search")
    → 读取完整 SKILL.md（带缓存，同一会话内不重复读）
    → 返回给 Agent 作为 tool 的 observation
```

---

## 8. Gateway 设计

### 8.1 Gateway 职责

Gateway 是系统的**顶层组装器和通信中枢**，负责：
1. 持有所有组件的实例（唯一组装点）
2. 管理 WebSocket 连接（session_id → 连接映射）
3. 将用户消息路由到对应的 Agent Loop
4. 维护每会话的 Task（串行调度）
5. 处理不可逆操作的用户确认

### 8.2 组件依赖关系（Gateway 视角）

```
Gateway.__init__() 按序初始化：

  settings           ← 配置（全局单例）
      │
  MCPClientManager   ← 连接 MCP 服务器
      │
  ToolRegistry       ← 注册 Python 工具 + CLI 工具 + MCP 工具
      │
  SkillLoader        ← 扫描并缓存技能元数据
      │
  MemoryManager      ← 初始化工作区目录
  ContextBuilder     ← 持有 MemoryManager + SkillLoader
      │
  SessionManager     ← 初始化会话目录

  以上所有组件，在 _build_agent_loop() 中注入 AgentLoop
```

### 8.3 通信协议

**客户端 → 服务端**：

| EventType | 数据字段 | 说明 |
|-----------|----------|------|
| `send` | `content: str` | 发送用户消息 |
| `abort` | — | 中止当前任务 |
| `slash` | `command: str, args: str` | Slash 命令 |
| `confirm` | `confirmed: bool` | 不可逆操作确认 |

**服务端 → 客户端**：

| EventType | 数据字段 | 说明 |
|-----------|----------|------|
| `status` | `state: str` | thinking/executing:step_N/idle/aborted/error |
| `stream` | `delta: str` | 模型输出 token 流 |
| `plan` | `steps: list` | 规划结果（用于 TUI 渲染进度） |
| `tool_start` | `tool: str, input: dict` | 工具调用开始 |
| `tool_end` | `tool: str, output: str` | 工具调用结束 |
| `confirm_request` | `tool: str, args: dict, message: str` | 请求用户确认 |
| `done` | `content: str` | 任务完成，附最终摘要 |
| `error` | `message: str` | 错误通知 |

所有消息格式：`{"type": "<EventType>", "session_id": "...", "data": {...}, "ts": "..."}`

### 8.4 消息处理流程

```
TUI 发送消息
     │
     ▼
Gateway._handle(ws, path)
     │
     ├── 解析 Message，提取 session_id
     ├── 注册 ws 到 _connections[session_id]
     │
     ├── type=send  → _handle_send(session_id, content)
     │                  ├── 检查是否有运行中 Task → 有则拒绝
     │                  ├── JSONL append(user)
     │                  ├── _build_state(session_id, content)
     │                  ├── _build_agent_loop(session_id)
     │                  ├── asyncio.create_task(loop.run(state))
     │                  └── done_callback → emit(DONE) + 清理 _running_tasks
     │
     ├── type=abort → _handle_abort(session_id)
     │                  └── task.cancel()
     │
     ├── type=slash → _handle_slash(session_id, data)
     │                  └── 执行会话控制命令（不走 Agent Loop）
     │
     └── type=confirm → _handle_confirm(session_id, confirmed)
                          └── pending_confirms[session_id].set_result(confirmed)
```

---

## 9. TUI 设计

### 9.1 布局结构

```
┌─────────────────────────────────────────────────────────────────┐
│  Sunday  │  会话: abc123  │  模型: claude-opus-4-5               │  ← Header（固定 1 行）
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  [用户]  帮我整理今天的邮件                          09:00:01    │
│                                                                 │
│  [思考]  ▶ 分析：用户需要邮件整理摘要...（可折叠）    09:00:02    │
│                                                                 │
│  [规划]  共 3 步：获取邮件 → 分类分析 → 生成报告      09:00:05    │
│                                                                 │
│  ┌─ 🔧 list_emails ────────────────────────────────────────┐    │
│  │  IN:  {"max_results": 20}                               │    │  ← 工具卡片（可折叠）
│  │  OUT: [共 12 封，3 封未读，2 封需回复...]               │    │
│  └─────────────────────────────────────────────────────────┘    │
│                                                                 │  ← 聊天日志（可滚动）
│  ✓ 步骤 1/3 完成                                  09:00:08    │
│                                                                 │
│  [Sunday] 今日共收到 12 封邮件...                  09:00:30    │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│  ● 执行中  步骤 2/3：分类分析                                     │  ← 运行状态栏（固定 1 行）
├─────────────────────────────────────────────────────────────────┤
│  Session: abc123  │  Think: medium  │  Tokens: 1.2k/8k          │  ← 信息栏（固定 1 行）
├─────────────────────────────────────────────────────────────────┤
│  > _                                                            │  ← 输入区（可扩展）
└─────────────────────────────────────────────────────────────────┘

键盘快捷键：
  Ctrl+P  会话切换    Ctrl+L  模型切换    Ctrl+T  思考展开/折叠
  Ctrl+O  工具卡片展开/折叠              Esc     中止当前任务
```

### 9.2 组件划分

| 组件 | 文件 | 职责 |
|------|------|------|
| `SundayApp` | `app.py` | Textual App 主体，WebSocket 连接管理，事件路由 |
| `ChatLog` | `widgets/chat_log.py` | 消息渲染，流式 token 追加，自动滚动 |
| `ToolCard` | `widgets/tool_card.py` | 工具调用折叠卡片，输入/输出实时更新 |
| `StatusBar` | `widgets/status_bar.py` | 运行状态展示（含进度指示） |
| `InputBar` | `widgets/input_bar.py` | 输入框，`/` 前缀补全，`!` 本地命令 |
| `SlashCommandHandler` | `commands.py` | Slash 命令解析与执行 |

### 9.3 Slash 命令

| 命令 | 参数 | 说明 |
|------|------|------|
| `/think <level>` | off\|minimal\|low\|medium\|high | 设置思考深度 |
| `/model <id>` | provider/model-id | 临时切换模型 |
| `/session <id>` | session_id | 切换到指定会话 |
| `/sessions` | — | 列出所有会话 |
| `/new` | — | 开始新会话 |
| `/reset` | — | 重置当前会话上下文（保留 JSONL 历史）|
| `/abort` | — | 中止当前运行任务 |
| `/memory [file]` | SOUL\|MEMORY\|USER\|TOOLS | 查看记忆文件 |
| `/skills` | — | 列出可用技能 |
| `/help` | — | 显示帮助 |

### 9.4 TUI 与 Gateway 事件处理

```
Gateway 推送事件              TUI 处理

status(thinking)     →   StatusBar 显示 "● 思考中..."
status(executing:2)  →   StatusBar 显示 "● 执行中 步骤 2/3"
plan(steps)          →   ChatLog 渲染规划摘要
stream(delta)        →   ChatLog 追加 token 到当前消息
tool_start(...)      →   ChatLog 插入新 ToolCard（展开状态）
tool_end(...)        →   ToolCard 更新输出，折叠
confirm_request(...) →   ChatLog 插入确认提示，等待 y/n
done(content)        →   ChatLog 渲染最终消息，StatusBar 恢复 idle
error(message)       →   ChatLog 渲染错误消息（红色）
status(idle)         →   StatusBar 显示 "● 就绪"
```

---

## 10. 并发与异步模型

### 10.1 整体并发模型

```
Python asyncio 单事件循环
          │
          ├── Gateway WebSocket Server（持续监听）
          │
          ├── Task[session_A] → AgentLoop（正在运行）
          │     └── 串行：同一 session 的任务排队执行
          │
          ├── Task[session_B] → AgentLoop（并发）
          │
          └── Task[background] → memory consolidation（后台）

规则：
  ① 同一 session_id：串行（已有 Task 时拒绝新任务）
  ② 不同 session_id：并发（各自独立 asyncio.Task）
  ③ I/O 工具（subprocess, HTTP）：asyncio 原生异步，不阻塞事件循环
  ④ CPU 密集工具（极少情况）：run_in_executor(ThreadPoolExecutor)
```

### 10.2 任务生命周期

```
                    Gateway._running_tasks[session_id]
                                │
  _handle_send()                │             done_callback
       │                        │                  │
       ▼                        ▼                  ▼
  create_task ──────────▶  [Task 运行中]  ──▶  清理 + emit(DONE)
                                │
                          用户发 ABORT
                                │
                          task.cancel()
                                │
                    AgentLoop 捕获 CancelledError
                                │
                    state.aborted = True
                    emit(STATUS="aborted")
```

### 10.3 上下文溢出处理

```
Executor ReAct 循环内：

  每次模型调用前，估算 messages 的 token 数
  if token_estimate > max_tokens * 0.8:
    ① 触发 ContextBuilder 生成历史摘要
    ② 将早期 messages 替换为摘要（保留最近 4 轮）
    ③ 触发 MemoryManager.append_daily_log（记录压缩事件）
    ④ 继续 ReAct 循环
```

---

## 11. 错误处理策略

### 11.1 错误分类与处置

```
错误来源                  处置方式                         用户感知

MaxStepsError           → 步骤标记 FAILED                  状态栏提示
（ReAct 超限）             Verifier 汇报失败原因             最终摘要显示未完成

RepetitionError         → 同上                             同上
（重复工具调用）

Tool timeout            → Guard 返回超时字符串              工具卡片显示超时
                          ReAct 感知后尝试其他方式

Tool crash              → Guard 捕获，返回格式化错误        工具卡片显示错误
                          不抛异常，让 ReAct 感知

LLM API error           → 指数退避重试 3 次（1s/2s/4s）    重试中提示
（网络/限速）               超出后 emit(ERROR) 并终止

Context overflow        → 摘要压缩后继续                   无感知
（token 超限）

File write error        → 记录日志，不中断 Loop             无感知
（记忆写入失败）

Gateway crash           → supervisord/systemd 自动重启      重连后继续

用户 Ctrl+C             → CancelledError → "任务已中止"     正常退出
```

### 11.2 AgentLoop 全局异常边界

```
AgentLoop.run(state):
  try:
    [主循环逻辑]

  except MaxStepsError | RepetitionError as e:
    emit(ERROR, 原因)
    return "任务未完成：{e}"

  except asyncio.CancelledError:
    state.aborted = True
    emit(STATUS, "aborted")
    raise  ← 必须重新抛出，让 asyncio.Task 知道自己被取消

  except Exception as e:
    logger.exception("Agent Loop 意外错误")
    emit(ERROR, str(e))
    return "发生意外错误，请查看日志"

  finally:
    ← 无论成功/失败/中止，都追加今日日志
    memory.append_daily_log(f"会话 {session_id}，任务：{task[:100]}")
```

---

## 12. 启动流程

### 12.1 Gateway 启动序列

```
sunday gateway start
        │
        ▼
  [1] 加载配置
      Settings() → 读取 .env + configs/agent.yaml
      校验必填项（至少一个 API key，workspace 路径可写）
        │
        ▼
  [2] 初始化工作区
      MemoryManager(workspace_dir)
        → 创建目录（若不存在）
        → 初始化默认 SOUL.md / AGENTS.md（若不存在）
        │
        ▼
  [3] 连接 MCP 服务器
      MCPClientManager.initialize(mcp.servers)
        → 逐个启动 MCP 子进程
        → 等待连接就绪（超时 10s）
        → 失败的服务器记录 warning，不阻塞启动
        │
        ▼
  [4] 注册工具
      ToolRegistry()
        → register_builtin_tools()    ← 文件读写等内置工具
        → register_cli_tools()        ← shell 命令封装
        → register_mcp_tools()        ← 来自已连接的 MCP 服务器
        │
        ▼
  [5] 扫描技能
      SkillLoader.discover()
        → 扫描 workspace/skills/ + ./skills/
        → 解析 frontmatter，过滤 requires 不满足的技能
        → 缓存技能元数据
        │
        ▼
  [6] 启动 WebSocket 服务
      websockets.serve("127.0.0.1", 7899)
      打印：Sunday Gateway 已启动，ws://127.0.0.1:7899
        │
        ▼
  [7] 进入事件循环（永久运行）
      asyncio.Future()  ← 等待 SIGTERM/SIGINT
```

### 12.2 CLI 入口命令

```
sunday                    # 等同于 sunday tui
sunday tui                # 启动 TUI，自动连接 Gateway（若未运行则先启动）
sunday run "<任务>"        # 单次非交互式执行，输出结果后退出
sunday gateway start      # 后台启动 Gateway 守护进程
sunday gateway stop       # 停止 Gateway
sunday gateway status     # 查看 Gateway 运行状态
sunday memory show [file] # 查看记忆文件（SOUL|MEMORY|USER|TOOLS）
sunday memory search <kw> # 关键词搜索记忆
sunday skills list        # 列出所有已安装技能
sunday skills install <p> # 从路径安装技能包
```

### 12.3 TUI 启动序列

```
sunday tui
    │
    ├── 检查 Gateway 是否运行（连接 ws://127.0.0.1:7899）
    │     未运行 → 在子进程中启动 Gateway，等待就绪
    │
    ├── 建立 WebSocket 连接
    │
    ├── 加载或创建会话（默认复用最近一次会话）
    │
    └── 启动 Textual App，进入交互循环
```

---

## 13. 依赖清单

### 13.1 核心依赖

| 包 | 版本 | 用途 |
|----|------|------|
| `agno` | ≥1.4 | Agent 框架，MCP 客户端，structured output |
| `pydantic` | ≥2.7 | 数据模型，配置验证 |
| `pydantic-settings` | ≥2.3 | .env + YAML 配置加载 |
| `pyyaml` | ≥6.0 | YAML 配置文件解析 |
| `textual` | ≥0.80 | TUI 框架 |
| `click` | ≥8.1 | CLI 命令定义 |
| `websockets` | ≥13.0 | Gateway WebSocket 服务端 + TUI 客户端 |
| `httpx` | ≥0.27 | HTTP 工具调用 |
| `python-dotenv` | ≥1.0 | .env 文件加载 |

### 13.2 开发依赖

| 包 | 用途 |
|----|------|
| `pytest` + `pytest-asyncio` | 单元测试 + 异步测试 |
| `vcrpy` | LLM 调用录制/回放（避免测试消耗 API） |
| `ruff` | Lint + 格式化 |

### 13.3 运行时要求

| 要求 | 版本 |
|------|------|
| Python | ≥ 3.12 |
| uv | 最新版（依赖管理） |
| Node.js | ≥ 18（MCP 服务器运行时，按需） |

### 13.4 版本约束说明

- `agno ≥ 1.4`：MCP 客户端和 async structured output 从此版本稳定
- `textual ≥ 0.80`：CSS 布局系统和 reactive 属性 API 稳定版
- `python ≥ 3.12`：`asyncio.TaskGroup`、`@override` 装饰器、类型系统改进
- `websockets ≥ 13.0`：asyncio 原生连接管理，`serve()` API 稳定
