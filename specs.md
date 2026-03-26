# Sunday — 需求规格说明书

**版本**：v0.1
**日期**：2026-03-26
**状态**：草稿

---

## 1. 产品定位

Sunday 是一个**本地优先（local-first）的个人边端 AI 智能体**，运行在用户的个人笔记本或 PC 上。它是用户日常办公的 AI 自动化助手，以终端为主要交互界面，帮助用户完成重复性、复杂性的工作任务。

**核心价值**：
- 数据本地化 — 对话、记忆、任务历史不离开本机
- 无缝融入终端工作流 — 不需要切换到浏览器或 App
- 持续学习用户习惯 — 跨会话持久记忆，越用越懂你
- 可扩展 — 通过技能包和 MCP 服务器扩展能力边界

**参考系统**：OpenClaw（本地边端智能体）、Claude Code（终端 AI 助手）

---

## 2. 系统架构

### 2.1 整体架构

```
用户
  │
  ▼
TUI / CLI（交互入口）
  │ WebSocket / Unix Socket
  ▼
Gateway（本地守护进程）
  ├── Session 管理
  ├── 配置加载
  └── Agent 调度
        │
        ▼
  Agent Loop（每会话独立）
  ├── Planner（THINK + PLAN + DECOMPOSE）
  ├── Executor（ReAct 循环）
  └── Verifier（结果验证 + 重规划）
        │
        ├── Tool Layer
        │   ├── Python Native Tools
        │   ├── CLI Tools（shell 封装）
        │   └── MCP Tools（外部服务）
        │
        └── Memory Layer
            ├── Workspace 文件（SOUL / MEMORY / 每日日志）
            └── Session 存储（JSONL 转录）
```

### 2.2 核心组件

| 组件 | 描述 |
|------|------|
| Gateway | 本地 WebSocket 服务，管理会话状态和 Agent 生命周期 |
| TUI | 基于 Textual 的终端界面，主要交互入口 |
| Agent Loop | think→plan→decompose→execute→verify 的完整任务循环 |
| Memory Manager | 工作区文件读写，上下文注入，记忆整合 |
| Tool Registry | 工具注册、发现、调用、结果验证 |
| Skill Loader | 技能包发现与懒加载 |

---

## 3. 功能需求

### 3.1 交互模式

#### F-01：TUI 交互（主要模式）
- 提供基于 Textual 的终端 UI
- 布局：头部（上下文状态）/ 聊天日志（带工具调用卡片）/ 状态行 / 底栏（模型/Token）/ 输入区
- 支持 Slash 命令（`/think`、`/session`、`/model`、`/new`、`/reset`、`/abort`）
- 支持 `!command` 前缀直接执行本地 shell 命令（一次性授权）
- 工具调用实时流式显示，以可折叠卡片呈现
- 支持键盘快捷键：Ctrl+P（会话切换）、Ctrl+G（Agent 切换）、Ctrl+L（模型切换）、Esc（中止）

#### F-02：CLI 单次调用
- `sunday run "<任务描述>"` — 非交互式执行单个任务
- `sunday gateway start/stop/status` — 管理后台守护进程
- `sunday memory show/search "<关键词>"` — 查看/搜索记忆
- `sunday skills list/install/remove` — 技能管理

#### F-03：后台守护进程
- `sunday gateway start` 启动本地 WebSocket 服务（默认端口 7899）
- 守护进程崩溃后自动重启
- TUI 和 CLI 作为轻量客户端 attach/detach，不影响运行中的 Agent

### 3.2 Agent 执行循环

#### F-04：混合推理（THINK）
- 每个任务开始时，先进行扩展思考（Extended Thinking / Chain-of-Thought）
- 思考阶段：分析任务意图、加载相关记忆上下文、评估所需工具和技能
- 思考深度可配置（off / minimal / low / medium / high），通过 `/think <level>` 切换
- 思考内容在 TUI 中可展开/折叠

#### F-05：结构化规划（PLAN）
- 基于思考结果生成结构化任务计划
- 计划格式：步骤列表，每步包含：意图描述、预期输入/输出、成功标准
- 规划阶段不调用任何外部工具
- 规划结果记录到会话日志

#### F-06：步骤拆分（DECOMPOSE）
- 将每个计划步骤拆分为可原子执行的工具调用单元
- 识别步骤间依赖关系，确定串行/并行执行顺序

#### F-07：ReAct 执行（EXECUTE）
- 每步执行遵循 Thought→Action→Observation 循环
- 硬性限制：单步最多 10 次 ReAct 迭代（可配置）
- 重复检测：连续两次相同的 Action 触发跳出或重规划
- 上下文溢出保护：接近 token 限制时自动摘要早期步骤

#### F-08：结果验证（VERIFY）
- 每步执行完成后，根据计划中的成功标准验证结果
- 验证失败时触发：重试当前步骤 / 局部重规划 / 向用户报告
- 所有步骤完成后生成最终结果摘要

#### F-09：不可逆操作确认
- 以下操作必须在执行前向用户确认：
  - 文件删除、目录清空
  - 邮件发送、消息发送
  - git push、部署操作
  - 超出沙箱范围的 shell 命令
- 确认请求在 TUI 中以醒目方式呈现，支持 y/n 键盘快速确认

### 3.3 记忆系统

#### F-10：工作区记忆（持久，文件存储）
工作区路径：`~/.sunday/workspace/`

| 文件 | 内容 | 生命周期 |
|------|------|----------|
| `SOUL.md` | 智能体身份、人格、价值观、行为边界 | 永久，用户手动编辑 |
| `AGENTS.md` | 操作规则、记忆使用方式、工具使用约定 | 永久，用户手动编辑 |
| `MEMORY.md` | 智能体主动整合的长期事实和偏好 | 长期，智能体写入 |
| `USER.md` | 用户画像：称谓、角色、偏好、工作习惯 | 长期，智能体写入 |
| `TOOLS.md` | 本地工具使用约定和注意事项 | 长期，用户/智能体写入 |
| `memory/YYYY-MM-DD.md` | 每日运行日志（事件、决策、任务结果）| 短期（30天 TTL）|

#### F-11：会话存储（临时，JSONL）
- 路径：`~/.sunday/sessions/<sessionId>.jsonl`
- 格式：每行一个 JSON 对象（用户消息、助手消息、工具调用、系统事件）
- 追加写入，保证原子性
- 会话列表元数据：`~/.sunday/sessions/index.json`

#### F-12：上下文注入（分层加载）
系统提示组装顺序（L0 始终注入，L1/L2 按需）：
- **L0（始终）**：`SOUL.md` + `AGENTS.md` + `MEMORY.md`（最近 100 行）+ `USER.md`
- **L0（始终）**：今天和昨天的 `memory/YYYY-MM-DD.md`
- **L0（始终）**：技能列表摘要（名称 + 描述，不含完整内容）
- **L1（按需）**：完整的 `SKILL.md` 内容（Agent 主动请求时加载）
- **L2（按需）**：历史 `memory/YYYY-MM-DD.md` 文件

#### F-13：记忆整合（自动）
- 每次会话结束后，Agent 主动将重要信息写入 `MEMORY.md` 和当日日志
- 上下文压缩前触发一次记忆整合提示（防止信息丢失）
- 每日凌晨 4 点（可配置）运行 `memory_consolidate` 脚本清理过期日志

### 3.4 工具系统

#### F-14：Python 原生工具
- 通过 `@tool` 装饰器（Agno）定义
- 每个工具有严格的 Pydantic 输入 schema 验证
- 工具调用结果经过 Tool Result Guard 验证后才返回模型
- 内置工具：文件读写、目录操作、文本处理

#### F-15：CLI 工具（Shell 封装）
- 通过 `tools/cli.py` 统一封装 `subprocess` 调用
- 默认超时 30 秒（可按工具配置）
- 输出截断保护（超过 4096 字符时摘要）
- 支持流式输出到 TUI

#### F-16：MCP 工具（协议扩展）
- 通过 Agno 内置 MCP 客户端连接外部 MCP 服务器
- MCP 服务器定义在 `configs/mcp_servers.yaml`
- 支持的 MCP 服务器（初期）：
  - `mcp-server-filesystem`：文件系统访问
  - `mcp-server-github`：GitHub 操作
  - `mcp-server-playwright`：浏览器自动化
  - `mcp-server-fetch`：HTTP 请求

#### F-17：工具安全策略
- 沙箱模式（默认）：工具在受限环境中执行
- 白名单/黑名单：在 `configs/agent.yaml` 中配置允许/禁止的命令
- 每个工具调用记录到会话 JSONL（输入、输出、时间戳）

### 3.5 技能系统

#### F-18：技能包格式（SKILL.md）
```markdown
---
name: email_automation
description: 邮件撰写、发送、分类和摘要自动化
requires: [gmail_mcp]
---

# 邮件自动化技能

## 能力
...（使用说明，不含 Python 代码）
```

#### F-19：技能发现与加载
- 发现路径（优先级从高到低）：
  1. `<workspace>/skills/`（用户安装的技能）
  2. `./skills/`（项目内置技能）
- 技能以摘要列表（名称 + 描述）注入系统提示
- Agent 通过 `load_skill("<name>")` 工具按需加载完整 `SKILL.md`
- 不满足 `requires` 条件的技能自动跳过

#### F-20：内置技能（初期）
- `email`：邮件处理（Gmail、Outlook）
- `calendar`：日程管理（Google Calendar）
- `files`：文件和目录操作
- `web_search`：网络搜索和信息提取
- `code`：代码阅读、编写、执行

### 3.6 配置系统

#### F-21：配置分层
```
.env                    # 密钥（不提交 git）
configs/agent.yaml      # 主配置（提交 git）
configs/mcp_servers.yaml # MCP 服务器（提交 git）
configs/prompts/*.md    # 系统提示文件（提交 git）
workspace/SOUL.md       # 智能体身份（用户编辑）
```

#### F-22：主配置结构（`configs/agent.yaml`）
```yaml
agent:
  name: Sunday
  workspace: ~/.sunday/workspace
  sessions_dir: ~/.sunday/sessions

model:
  provider: anthropic       # anthropic | openai | google | ollama
  id: claude-opus-4-5
  temperature: 0.2
  max_tokens: 8192

reasoning:
  max_steps: 10
  thinking_level: medium    # off|minimal|low|medium|high
  thinking_budget_tokens: 4096

memory:
  consolidation_cron: "0 4 * * *"
  log_retention_days: 30

tools:
  default_timeout: 30
  max_output_chars: 4096
  sandbox_mode: true
  allow_list: []            # 空=全部允许
  deny_list: []

mcp:
  servers: []               # 引用 mcp_servers.yaml

skills:
  extra_dirs: []

tasks: {}                   # 定时任务定义
```

#### F-23：模型提供商切换
- 通过修改 `configs/agent.yaml` 的 `model.provider` 和 `model.id` 切换
- 无需修改任何代码
- 运行时通过 `/model` Slash 命令切换（临时）

---

## 4. 非功能需求

### 4.1 性能
- TUI 响应延迟 < 100ms（不含 LLM 推理时间）
- Gateway 启动时间 < 2 秒
- 记忆文件读取 < 500ms（含 L0 全部文件）
- 单会话 JSONL 文件最大 50MB，超出后自动归档

### 4.2 可靠性
- Agent Loop 崩溃不影响 Gateway 和其他会话
- 文件写入保证原子性（先写临时文件，再重命名）
- 工具调用失败记录错误，不中断整体流程（除非步骤依赖失败）

### 4.3 安全性
- API key 仅存储在 `.env`，不写入任何日志或记忆文件
- `MEMORY.md` 中的信息不包含密码、Token 等敏感数据（智能体规则约束）
- 默认沙箱模式，用户主动授权才能执行宿主机命令

### 4.4 可维护性
- 配置文件变更无需重启（热加载，可选）
- 所有工具调用有完整日志，便于调试
- 技能包可独立安装/卸载，不影响核心运行

### 4.5 可扩展性
- 新增工具：在 `skills/<name>/tools.py` 中实现，无需修改核心
- 新增 MCP 服务器：在 `configs/mcp_servers.yaml` 中配置
- 新增模型提供商：实现 `ModelProvider` 接口，注册到 Agno

---

## 5. 数据模型

### 5.1 会话 JSONL 格式
```jsonl
{"type": "session_start", "session_id": "abc123", "ts": "2026-03-26T09:00:00Z", "model": "claude-opus-4-5"}
{"type": "user", "content": "帮我整理今天的邮件", "ts": "2026-03-26T09:00:01Z"}
{"type": "think", "content": "用户想要...", "ts": "2026-03-26T09:00:02Z"}
{"type": "plan", "steps": [...], "ts": "2026-03-26T09:00:05Z"}
{"type": "tool_call", "tool": "list_emails", "input": {...}, "output": {...}, "ts": "2026-03-26T09:00:10Z"}
{"type": "assistant", "content": "今天共有 12 封邮件...", "ts": "2026-03-26T09:00:30Z"}
{"type": "session_end", "tokens": {"input": 1200, "output": 350}, "ts": "2026-03-26T09:00:31Z"}
```

### 5.2 MEMORY.md 格式
```markdown
# Sunday 记忆

## 用户偏好
- [P0] 邮件回复风格：简洁专业，不使用过多礼貌用语
- [P1][2026-03-26] 当前重点项目：Sunday 智能体开发

## 工具约定
- Gmail 邮件列表每次最多获取 20 封

## 任务历史摘要
- 2026-03-26：整理了当日邮件，生成了每日简报模板
```

---

## 6. 开发阶段规划

### Phase 1：基础骨架（MVP）
- [ ] 项目结构搭建（pyproject.toml、目录）
- [ ] 配置系统（Pydantic Settings + YAML）
- [ ] 记忆系统（文件读写、上下文注入）
- [ ] Agent Loop（think→plan→execute→verify）
- [ ] 工具系统（Python 工具 + CLI 封装）
- [ ] CLI 入口（`sunday run`）

### Phase 2：TUI 与会话管理
- [ ] Textual TUI（5 区布局、工具卡片、流式输出）
- [ ] Slash 命令
- [ ] Gateway 守护进程（WebSocket）
- [ ] 会话存储（JSONL）
- [ ] 记忆整合（会话结束写入 MEMORY.md）

### Phase 3：工具与技能扩展
- [ ] MCP 客户端集成
- [ ] 技能包系统（SKILL.md 发现与懒加载）
- [ ] 内置技能：files、web_search、code
- [ ] 工具安全策略（白名单/沙箱）

### Phase 4：实用技能
- [ ] 技能：email（Gmail MCP）
- [ ] 技能：calendar（Google Calendar MCP）
- [ ] 定时任务（cron + 每日简报）
- [ ] 记忆整合定时任务

---

## 7. 技术选型说明

### 为什么选 Agno 而不是 LangGraph？
- Agno 原生支持 MCP、记忆、多智能体，API 更简洁
- LangGraph 的图模型在个人助手场景下过重（适合复杂企业级工作流）
- Agno 的 Agent-as-Config 模式与本项目的配置分离原则契合
- 未来如需复杂审批工作流，可针对特定子流程引入 LangGraph

### 为什么选 Textual 而不是 Rich/Prompt-toolkit？
- Textual 提供完整的 TUI 框架（布局、事件、组件），而非仅渲染库
- 内置异步支持，适合流式 LLM 输出
- 社区活跃，组件丰富（与 OpenClaw 的 pi-tui 功能对标）

### 为什么用文件系统而不是 SQLite/向量数据库？
- 零依赖，无需安装额外服务
- Markdown 文件人类可读、可直接编辑
- Git 可追踪（记忆历史可版本控制）
- 对于个人用户的记忆规模（数百条），文件系统性能完全足够

---

## 附录：关键参考

- [OpenClaw 架构文档](https://deepwiki.com/wiki/openclaw/openclaw)
- [Agno 官方文档](https://docs.agno.com)
- [Model Context Protocol](https://modelcontextprotocol.io)
- [ReAct 论文](https://arxiv.org/abs/2210.03629)（Yao et al., 2023）
- [Textual 文档](https://textual.textualize.io)
