# Sunday — Claude Code 协作规则

本文件定义了 Claude Code 在本项目中的工作规范。

---

## 项目概述

Sunday 是一个本地优先的个人边端 AI 智能体，运行在用户个人电脑上，通过终端 TUI 交互。技术栈：Python 3.12+、Agno 框架、Textual TUI、文件系统记忆、MCP 协议工具。

核心规格详见 `specs.md`。

---

## 架构原则

### 配置与实现分离（强制）
- API key、模型 ID、温度等参数 → `.env` 或 `configs/agent.yaml`，**不得硬编码**
- 角色定义、系统提示 → `configs/prompts/*.md`
- 任务定义 → `configs/agent.yaml` 的 `tasks` 节
- 技能指令 → `skills/*/SKILL.md`，**不得内嵌在 Python 代码中**
- 配置通过 `src/sunday/config.py`（Pydantic Settings）统一加载

### 记忆系统（文件优先）
- 不引入任何新的存储组件（Redis、向量数据库等），所有记忆使用文件系统
- 工作区路径：`~/.sunday/workspace/`（生产）或 `./workspace/`（开发）
- 记忆层级：`SOUL.md`（永久）→ `MEMORY.md`（长期）→ `memory/YYYY-MM-DD.md`（每日）→ 会话 JSONL（临时）
- 上下文注入顺序严格按照 L0→L1→L2 优先级

### Agent 执行循环（不可破坏的顺序）
THINK → PLAN → DECOMPOSE → EXECUTE（ReAct）→ VERIFY → 记忆更新

不得跳过 VERIFY 步骤，不得在 PLAN 阶段调用外部工具。

### 工具安全原则
- 所有不可逆操作（删除文件、发送邮件、git push）必须向用户确认后执行
- CLI 工具调用必须经过 `tools/cli.py` 封装，不得直接 `subprocess.run`
- 工具结果必须经过 Tool Result Guard 验证再返回给模型

---

## 代码规范

### Python 风格
- Python 3.12+，使用 `uv` 管理依赖
- 所有配置对象使用 Pydantic BaseModel/BaseSettings
- 工具函数用 `@tool` 装饰器（Agno）定义，**不得在装饰器外暴露**
- 异步优先：agent loop、tool 调用、TUI 更新都使用 `async/await`

### 文件操作
- 不直接操作 `~/.sunday/` 下的文件，通过 `memory/manager.py` 的接口
- 记忆文件写入必须是追加或原子替换，不得部分写入

### 错误处理
- ReAct 循环 `max_steps=10`，超出后抛出 `MaxStepsError` 并通知用户
- 工具超时统一设置 `timeout=30s`，可在配置中覆盖
- 不使用空 except，不吞掉异常

### 测试
- 单元测试覆盖 planner、executor、verifier、memory manager
- 集成测试必须使用真实文件系统（不 mock 文件操作）
- 不 mock LLM 调用做集成测试，使用录制/回放（VCR）模式

---

## 目录约定

| 路径 | 用途 |
|------|------|
| `src/sunday/` | 所有实现代码 |
| `configs/` | 所有配置（可提交 git） |
| `skills/` | 技能包（SKILL.md + tools.py） |
| `workspace/` | 开发用工作区（SOUL.md 等可提交，memory/ 不提交） |
| `.env` | 密钥（不提交 git） |
| `specs.md` | 需求规格（权威文档） |

---

## 禁止行为

- 不在代码中硬编码任何 API key 或模型名称
- 不引入向量数据库、Redis、PostgreSQL 等外部组件（当前阶段）
- 不绕过 Tool Result Guard 直接将工具输出喂给模型
- 不在 PLAN 阶段执行真实工具调用
- 不修改 `workspace/SOUL.md` 的内容（这是用户的配置领域）
- 不创建不必要的抽象或工具函数（三行相似代码优于过早抽象）

---

## 参考资源

- Agno 文档：通过 `mcp__context7__resolve-library-id` 查询 `agno`
- OpenClaw 架构参考：`mcp__deepwiki__ask_question` 查询 `openclaw/openclaw`
- 项目需求：`specs.md`
