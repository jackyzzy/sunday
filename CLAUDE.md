# Sunday — Claude Code 协作规则

本文件定义了 Claude Code 在本项目中的工作规范。

---

## 项目概述

Sunday 是一个本地优先的个人边端 AI 智能体，运行在用户个人电脑上，通过终端 TUI 交互。技术栈：Python 3.12+、Agno 框架、Textual TUI、文件系统记忆、MCP 协议工具。

核心规格详见 `specs.md`。

---

## 架构原则

### 配置 / 运行数据 / 代码 三向分离（强制）

| 类型 | 位置 | 内容 | 谁维护 |
|------|------|------|--------|
| **代码** | `src/sunday/` | 行为/算法本身 | 开发者 |
| **配置** | `.env`、`configs/agent.yaml` | API key、模型 ID、`enabled` 开关、checker 选择 | 用户初次配 |
| **角色 / 提示** | `configs/prompts/*.md` | 系统提示、计划模板 | 开发者 + 用户 |
| **任务模板** | `configs/agent.yaml` 的 `tasks` 节 | 预置任务流 | 用户 |
| **技能** | `skills/*/SKILL.md` + `*.py` | 工具实现（不得内嵌在代码里）| 用户/开发者 |
| **运行数据** | `workspace/RUNTIME_RULES.md` (L0)、`memory/MEMORY.md` (L1) | 关键词清单、阈值、用户偏好 | 用户手编 + AI 学习 |

- 不得硬编码 API key、模型名、关键词清单等可变数据
- 配置通过 `src/sunday/config.py`（Pydantic Settings）统一加载
- 关键词、阈值这类"可演化的内容"放在 [workspace/RUNTIME_RULES.md](workspace/RUNTIME_RULES.md) 而非 `agent.yaml`，由 [src/sunday/memory/runtime_rules.py](src/sunday/memory/runtime_rules.py) 解析；不同 sunday 实例可有自己的规则集

### 记忆系统（文件优先，四层分级）

不引入任何新的存储组件（Redis、向量数据库等），所有记忆使用文件系统。

**记忆四层架构**：

| 层级 | 路径 | 内容 | 维护者 |
|------|------|------|--------|
| **L0 永久层** | `~/.sunday/workspace/SOUL.md`、`AGENTS.md`、`TOOLS.md`、`RUNTIME_RULES.md` | Agent 身份、规则、工具约定、运行规则（关键词/阈值）| 用户手动 + AI 追加 |
| **L1 长期层** | `~/.sunday/memory/MEMORY.md`、`USER.md` | 跨会话事实摘要、用户画像 | AI 写入 |
| **L2 每日层** | `~/.sunday/memory/daily/YYYY-MM-DD.md` | 每日摘要（30天 TTL）| AI 写入 |
| **L3 会话层** | `~/.sunday/sessions/{id}/` | 完整对话（meta/stream/turns）| 自动记录 |

**开发工作区**：项目根目录 `workspace/` 存放模板文件（SOUL.md 等），实际运行数据在 `~/.sunday/`

**上下文注入顺序**：`L0 → L1 → L2（today/yesterday）→ L3（当前 session 历史轮次）→ task`

**Session 目录结构**（每 session 一个子目录）：
```
~/.sunday/sessions/{id}/
├── meta.json           # 会话元数据（turn_count、turns 索引）
├── stream.jsonl        # 原始事件流（含 turn_id，append-only）
└── turns/{turn_id}.json   # 每轮：user_input + plan + execution + output
```

**Memory 服务边界**：Session = prompt 编排层（agent 侧）；Memory = 存储接口（存储侧，未来可服务化为独立 MemoryStore Protocol）

### Agent 执行循环（两层 Team 架构，不可破坏的顺序）

**外层 AgentLoop（编排层）：**
THINK → REALTIME_HINTS → (opt-in FACT_CHECK) → PLAN（每步带 `requires_realtime_data`）→ 按依赖顺序驱动各 Team → EVALUATE（汇总评估）→ 记忆更新

**内层 Team（执行层，每个顶层 Step 独立一个 Team）：**
SUB-PLAN（1~3 个子步骤，继承父 step 的 realtime 标记）→ EXECUTE（ReAct；realtime 步骤强制联网或打"⚠ 未联网"标签）→ VERIFY（基础 verify + 主题一致性 + 工具使用审计）

**Plan 阶段保持纯粹（默认）**：
- THINK 是 LLM-only（识别不确定断言），不调外部工具
- REALTIME_HINTS 是纯函数信号聚合（关键词读自 `workspace/RUNTIME_RULES.md`）
- FACT_CHECK 默认关（`config.quality.fact_check.enabled=false`），仅当用户对 L1/L2 跨会话记忆污染敏感时手动开
- 实时数据获取统一在 Execution 阶段做，由 `Step.requires_realtime_data` 显式驱动

规则：
- 不得跳过 VERIFY 步骤（内外两层均适用）
- 默认 PLAN 阶段不调外部工具；opt-in 后 FACT_CHECK 才调白名单工具（默认 `web_search` / `read_file`），预算 `max_tool_calls=2` + `timeout_seconds=10`
- Team 共享顶层 ToolRegistry，不重复创建
- 每个 Step 有 `requires_realtime_data: bool`：Planner 决策（三信号 — 关键词 + think 实体 + plan LLM 自判）→ Executor 遵守（system prompt 注入约束 + 代码兜底打标）→ Verifier 审计（未联网且无标签 → failed + replan）
- 顶层评估用 `Verifier.evaluate()`，子步骤验证用 `Verifier.check()`；`Verifier.check()` 三层闸门：基础 verify → 主题一致性（`subject_consistency`，可关）→ 工具使用审计（`tool_usage_audit`，可关）
- "最终汇总/整合"类步骤的 Executor system prompt 自动追加主题锚定段（`config.quality.final_step_anchor`，可关）

### 工具安全原则
- 所有不可逆操作（删除文件、发送邮件、git push）必须向用户确认后执行
- CLI 工具调用必须经过 `tools/cli_tool.py` 封装，不得直接 `subprocess.run`
- 工具结果必须经过 Tool Result Guard 验证再返回给模型

### 工具注册规范（CLI 和 TUI 两种模式均须遵守）

所有工具按以下顺序注册，后加载可覆盖前加载（用户工具优先级最高）：

1. `register_cli_tools(registry)`             ← 内置工具（最低优先级）
2. `load_skill_tools(skills_dir, registry)`   ← 技能工具
3. `load_user_tools(workspace_dir, registry)` ← 用户自定义（最高优先级）

**两条注册路径，必须同步维护：**
- CLI 模式：`src/sunday/cli.py` → `_run_task()`
- TUI/Gateway 模式：`src/sunday/gateway/server.py` → `_build_agent_loop()`

技能工具声明方式（`skills/*/` 目录下任意 `.py` 文件末尾，文件名不限）：
```python
from sunday.tools.registry import ToolMeta
TOOLS = [
    (ToolMeta(name="...", description="...", input_schema={...}), fn),
]
```
Shell 工具：`.sh` 文件头部加 YAML frontmatter 注释（`# ---` 包裹 name/description/args）可自动注册。

---

## 验证优先原则

**每个 Task 的开发流程：**

1. **先写验证方案**（不写实现代码）：明确测试文件名、函数名、安全约束
2. **方案经用户确认**后才开始写实现代码
3. **实现完成后运行验证**：全绿才算 Task done
4. **验证通过后更新 task.md**：勾选对应 checkbox

**安全约束（所有测试必须遵守）：**
- 文件隔离：使用 `pytest` 的 `tmp_path`，不操作 `~/.sunday/`
- 网络隔离：用 `unittest.mock.AsyncMock` mock httpx，不调用真实 API
- 密钥隔离：用 `patch.dict(os.environ)` 注入假 key，不读取真实 `.env`
- CLI 隔离：用 `click.testing.CliRunner`，不触发真实 stdin/stdout

**验证方案模板（写在 task.md 每个 Task 内）：**
```
验证方案：
- 测试文件：tests/unit/test_XXX.py
- 主要用例：test_foo_bar、test_baz_qux（列出函数名）
- 安全约束：tmp_path / mock httpx / fake env key
```

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

## 调试与日志

每次 AgentLoop 执行（CLI 或 TUI 模式）自动在 `~/.sunday/logs/{session_id}.jsonl` 生成结构化日志，无需任何额外配置。

**日志格式（每行一个 JSON）：**
```json
{"ts": "ISO8601", "session_id": "12位hex", "event": "...", "data": {...}}
```

**事件类型：**

| 事件 | 关键字段 | 说明 |
|------|---------|------|
| `session_start` | task, thinking_level, mode | 任务开始 |
| `plan_realtime_hints` | phase, task_keywords, claim_entities | Planner 聚合的实时性提示信号（仅有信号时 emit）|
| `plan_fact_check` | phase, claims/facts | FACT_CHECK 子阶段（默认关；opt-in 后才会出现）|
| `plan` | goal, steps[]{id,intent,criteria,requires_realtime_data} | 顶层计划，每步含意图与实时性标注 |
| `step_start` | step_id, intent, node_type | 步骤开始，含执行节点类型(team/simple) |
| `sub_step_result` | parent_step_id, sub_step_id, verified, verify_reason | Team 内每个子步骤结果 |
| `step_result` | step_id, status, verified, verify_reason, duration_ms | 顶层步骤完成 |
| `replan` | step_id, replan_count, max_replans, failure_reason | 触发重规划，含原因 |
| `team_error` | step_id, phase(sub_planning/sub_replanning), error | Team 内规划/重规划异常 |
| `error` | message | 顶层未捕获异常 |
| `session_end` | outcome, steps_total, steps_passed, duration_seconds | 任务结束 |

**常用 jq 查询：**
```bash
# 查看某 session 执行结果（最后一行即 session_end）
tail -1 ~/.sunday/logs/<session_id>.jsonl | jq .

# 查看所有失败步骤及 Verifier 判定原因
jq 'select(.event == "step_result" and .data.verified == false) | {step: .data.step_id, reason: .data.verify_reason}' ~/.sunday/logs/<session_id>.jsonl

# 查看重规划链（触发了哪些 step，第几次，失败原因）
jq 'select(.event == "replan") | .data' ~/.sunday/logs/<session_id>.jsonl

# 查看 Team 内部异常（子规划/子重规划失败原因）
jq 'select(.event == "team_error") | .data' ~/.sunday/logs/<session_id>.jsonl

# 查看子步骤粒度结果（定位 Team 内哪个子步骤失败）
jq 'select(.event == "sub_step_result") | [.data.parent_step_id, .data.sub_step_id, .data.verified, .data.verify_reason]' ~/.sunday/logs/<session_id>.jsonl

# 最近 10 个 session 健康概览（session_id / outcome / 耗时）
for f in $(ls -t ~/.sunday/logs/*.jsonl | head -10); do
  tail -1 "$f" | jq -r '[.session_id, .data.outcome, .data.duration_seconds] | @tsv'
done
```

**实现位置：**
- `src/sunday/agent/session_log.py` — `SessionLog` 类（事件路由与写入）
- `src/sunday/agent/react_agent.py` — `run()` 内的 `logged_emit` 包装；`_create_node()` 将 `logged_emit` 传入 Team/SimpleNode

---

## 目录约定

| 路径 | 用途 |
|------|------|
| `src/sunday/` | 所有实现代码 |
| `configs/` | 所有配置（可提交 git） |
| `skills/` | 技能包（SKILL.md 描述 + 任意 *.py 工具文件 + 可选 *.sh 工具） |
| `workspace/` | 开发用工作区（SOUL.md 等可提交，memory/ 不提交） |
| `~/.sunday/logs/` | Session 结构化日志（JSONL，按 session_id 命名，不提交 git） |
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
