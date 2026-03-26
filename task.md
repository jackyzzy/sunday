# Sunday — 开发工作计划

**版本**：v0.1
**日期**：2026-03-26
**依据**：specs.md v0.1 / design.md v0.2

---

## 总体节奏

```
Phase 1：基础骨架        → 可跑通最简单的命令行对话
Phase 2：Agent 核心      → 完整的 think→plan→execute→verify 循环
Phase 3：记忆系统        → 跨会话持久记忆与上下文注入
Phase 4：工具与技能      → CLI 工具、MCP、技能包
Phase 5：Gateway + TUI   → 守护进程 + 终端界面
Phase 6：实用技能        → 日常办公自动化技能落地
```

每个 Phase 完成后应可独立运行和测试，不依赖后续 Phase。

---

## Phase 1：基础骨架

**目标**：项目可以运行，配置系统可用，能完成一次最简单的 LLM 对话。

### T1-1 项目初始化
- [ ] 创建 `pyproject.toml`，配置项目元信息、依赖（agno、pydantic-settings、pyyaml、click、python-dotenv）
- [ ] 创建 `.gitignore`（排除 `.env`、`__pycache__`、`.venv`、`workspace/memory/`、`~/.sunday/`）
- [ ] 创建 `.env.example`（列出所有必填环境变量及说明）
- [ ] 创建 `Makefile`（目标：`run`、`test`、`lint`、`install`、`consolidate`）
- [ ] 创建 `src/sunday/__init__.py`，定义包版本
- [ ] 验证：`uv sync` 成功，`uv run python -c "import sunday"` 无报错

**验证方案：**
- 测试文件：`tests/unit/test_project_structure.py`
- 主要用例：`test_pyproject_has_required_deps`、`test_pyproject_has_entry_point`、`test_pyproject_has_dev_extras`、`test_gitignore_protects_env`、`test_gitignore_protects_venv`、`test_gitignore_protects_workspace_memory`、`test_package_importable`、`test_env_example_has_required_vars`
- 安全约束：纯静态检查，无网络，无文件写入

### T1-2 配置系统
- [ ] 实现 `src/sunday/config.py`
  - 定义 `ModelConfig`、`ReasoningConfig`、`MemoryConfig`、`ToolsConfig`、`MCPConfig`、`SkillsConfig`、`AgentConfig`、`TaskConfig`、`SundayConfig`
  - 实现 `Settings`（BaseSettings），`sunday` 属性用 `@cached_property` 加载 YAML
  - 实现 `Settings.get_api_key(provider)` 方法
  - `workspace_dir` / `sessions_dir` 仅在 `AgentConfig` 中定义
- [ ] 创建 `configs/agent.yaml`（含所有配置项的默认值和注释）
- [ ] 创建 `configs/mcp_servers.yaml`（空列表，含格式注释）
- [ ] 创建 `configs/prompts/system.md`（基础系统提示模板，含占位符）
- [ ] 创建 `configs/prompts/verifier.md`（验证阶段提示）
- [ ] 单元测试：`tests/unit/test_config.py`（加载默认值、YAML 覆盖、缺失 key 抛异常）

**验证方案：**
- 测试文件：`tests/unit/test_config.py`
- 主要用例：`test_sunday_config_defaults`、`test_get_api_key_success`、`test_get_api_key_missing`、`test_workspace_dir_in_agent_config`、`test_all_config_models_have_defaults`、`test_yaml_missing_fields_use_defaults`、`test_yaml_type_error_raises`、`test_workspace_dir_tilde_expanded`、`test_settings_sunday_cached`、`test_api_key_not_in_error_message`、`test_openai_api_key`、`test_unknown_provider_raises`、`test_tools_config_deny_list_defaults`、`test_mcp_servers_empty_by_default`
- 安全约束：`tmp_path` 写临时 YAML、`patch.dict(os.environ)` 注入假 key，无真实 `.env`

### T1-3 工作区初始化
- [ ] 创建 `workspace/SOUL.md`（Sunday 的默认身份与人格定义）
- [ ] 创建 `workspace/AGENTS.md`（默认操作规则，含记忆使用方式）
- [ ] 创建 `workspace/MEMORY.md`（空模板，含格式说明）
- [ ] 创建 `workspace/USER.md`（空模板，含格式说明）
- [ ] 创建 `workspace/TOOLS.md`（默认工具使用约定）

**验证方案：**
- 测试文件：`tests/unit/test_workspace.py`
- 主要用例：`test_workspace_files_exist`、`test_workspace_files_nonempty`、`test_workspace_files_have_title`、`test_soul_md_has_sections`、`test_workspace_memory_in_gitignore`、`test_configs_prompts_exist`、`test_mcp_servers_yaml_parseable`
- 安全约束：只读现有文件，无写入，无 mock

### T1-4 CLI 入口（最小版）
- [ ] 实现 `src/sunday/cli.py`（Click）
  - `sunday` / `sunday tui` → 占位，打印"TUI 尚未实现"
  - `sunday run "<任务>"` → 直接调用 LLM，打印结果（暂不走 Agent Loop）
  - `sunday gateway start/stop/status` → 占位
- [ ] 在 `pyproject.toml` 注册 `sunday = "sunday.cli:main"` 入口点
- [ ] 验证：`uv run sunday run "你好"` 能收到 LLM 回复

**验证方案：**
- 测试文件：`tests/unit/test_cli.py`、`tests/unit/test_simple_agent.py`
- 主要用例（CLI）：`test_help`、`test_version`、`test_tui_placeholder`、`test_gateway_start_placeholder`、`test_gateway_stop_placeholder`、`test_gateway_status_placeholder`、`test_skills_list_placeholder`、`test_run_no_api_key_exits_1`、`test_run_thinking_valid_values`、`test_run_model_override`、`test_memory_show_file_exists`、`test_memory_show_file_missing`、`test_memory_search_found`、`test_memory_search_not_found`
- 主要用例（Agent）：`test_anthropic_success`、`test_thinking_block_filtered`、`test_openai_success`、`test_model_override_with_provider`、`test_model_override_id_only`、`test_thinking_budget_off`、`test_thinking_budget_high`、`test_unknown_provider_raises`、`test_http_error_propagates`、`test_system_prompt_includes_soul`
- 安全约束：`CliRunner` 测试 CLI、`AsyncMock` mock httpx、`tmp_path` 创建 workspace、`patch.dict(os.environ)` 注入假 key

**Phase 1 完成标准（可量化）：**
- `uv run pytest tests/unit/ -v` 全绿（≥50 个用例）
- `uv run ruff check src/ tests/` 零警告
- `uv run sunday --version` 输出 `0.1.0`
- 手动确认：`uv run sunday run "你好"` 能返回 LLM 回复（需真实 API key）

---

## Phase 2：Agent 执行循环

**目标**：实现完整的 think→plan→decompose→execute→verify 循环，能处理多步任务。

> **Phase 2 开始前**：为每个 Task 填写验证方案（测试文件、函数名、安全约束），经确认后再开始实现。

### T2-1 核心数据模型
- [ ] 实现 `src/sunday/agent/models.py`
  - `ThinkingLevel`（Enum）
  - `StepStatus`（Enum）
  - `Step`、`Plan`、`ToolCall`、`ReactIteration`、`StepResult`、`AgentState`
  - 所有含 `datetime` 的字段使用 `Field(default_factory=datetime.now)`
  - `AgentState.history` 字段存储本会话历史对话
- [ ] 单元测试：模型序列化/反序列化（`model_dump` / `model_validate`）

**验证方案：** 待 Phase 2 开始前填写

### T2-2 Planner
- [ ] 实现 `src/sunday/agent/planner.py`
  - `Planner.__init__(model_client, context_builder, config)`
  - `think_and_plan(state) → Plan`：注入上下文 → 扩展思考 → 结构化输出 Plan
  - `replan(failed_step, result, state) → list[Step]`：局部重规划
  - `THINKING_BUDGET` 映射表（off=0 / minimal=512 / low=1024 / medium=4096 / high=8192）
  - 规划阶段 temperature=0.3，禁止调用外部工具
- [ ] 单元测试：`tests/unit/test_planner.py`（VCR 录制/回放 LLM 调用）

**验证方案：** 待 Phase 2 开始前填写

### T2-3 Executor（ReAct 循环）
- [ ] 实现 `src/sunday/agent/executor.py`
  - `Executor.__init__(model_client, tool_registry, config)`
  - `run(step, state) → StepResult`：ReAct 循环，最多 `max_steps` 次
  - `MaxStepsError`、`RepetitionError` 异常定义
  - 重复检测：连续相同 tool_name + arguments 触发 `RepetitionError`
  - 上下文溢出检测：token 估算超过 80% 时压缩历史消息
  - 执行阶段 temperature=0
- [ ] 单元测试：`tests/unit/test_executor.py`

**验证方案：** 待 Phase 2 开始前填写

### T2-4 Verifier
- [ ] 实现 `src/sunday/agent/verifier.py`
  - `VerifyResult`（passed, reason, should_replan）
  - `Verifier.check(step, result, state) → VerifyResult`
  - `Verifier.summarize(state) → str`：生成最终结果摘要
  - temperature=0
- [ ] 单元测试：`tests/unit/test_verifier.py`

**验证方案：** 待 Phase 2 开始前填写

### T2-5 AgentLoop 控制器
- [ ] 实现 `src/sunday/agent/loop.py`
  - `AgentLoop.__init__(..., emit: Callable)`：注入 emit 回调，不 import gateway
  - `run(state) → str`：完整循环控制逻辑
  - 依赖满足检查 `_deps_satisfied`
  - 验证失败时局部重规划（替换剩余步骤）
  - 全局异常边界（`MaxStepsError`、`CancelledError`、通用 Exception）
  - `finally` 块保证日志写入
- [ ] 集成测试：`tests/integration/test_agent_loop.py`（用简单任务端到端验证循环）

**验证方案：** 待 Phase 2 开始前填写

### T2-6 接入 CLI
- [ ] 更新 `sunday run "<任务>"` 命令，接入 AgentLoop
- [ ] 实现临时的 `emit` 回调（打印到 stdout）
- [ ] 实现临时的空 `ToolRegistry`（无工具，验证 Agent 能纯对话完成任务）
- [ ] 验证：`uv run sunday run "给我写一首关于秋天的五言诗"` 走完完整循环

**验证方案：** 待 Phase 2 开始前填写

**Phase 2 完成标准**：`sunday run` 能展示 think→plan→execute→verify 的完整过程，打印每步进度和最终结果。

---

## Phase 3：记忆系统

**目标**：实现文件系统记忆，跨会话持久化，上下文注入。

> **Phase 3 开始前**：为每个 Task 填写验证方案（测试文件、函数名、安全约束），经确认后再开始实现。

### T3-1 MemoryManager
- [ ] 实现 `src/sunday/memory/manager.py`
  - `MemoryManager.__init__(workspace_dir: Path)`：创建目录结构
  - `append_daily_log(content)` → 原子追加写入今日日志
  - `update_memory(section, key, value, priority)` → 原子 upsert MEMORY.md
  - `update_user_profile(key, value)` → 原子 upsert USER.md
  - `consolidate_session(state)` → 同步写日志 + 异步触发 AI 整合
  - `_ai_consolidate(state)` → LLM 提炼 → 结构化输出 → 调用 update_memory/update_user_profile
  - `_atomic_write(path, content)` → .tmp rename 原子写
  - `asyncio.Lock` 串行化所有写操作
- [ ] 单元测试：`tests/unit/test_memory_manager.py`（真实文件系统，不 mock）

### T3-2 ContextBuilder
- [ ] 实现 `src/sunday/memory/context.py`
  - `Context`（system_prompt, token_estimate）
  - `ContextBuilder.__init__(workspace_dir, skill_loader, l0_max_lines)`
  - `build(session_id) → Context`：按 L0 顺序组装系统提示
    - SOUL.md → AGENTS.md → MEMORY.md（末尾 l0_max_lines 行）→ USER.md → 昨日+今日日志 → 技能摘要 → TOOLS.md → 当前日期
  - 文件不存在时静默跳过

### T3-3 MemoryJanitor
- [ ] 实现 `src/sunday/memory/janitor.py`
  - `MemoryJanitor.run() → dict`：清理超过 `retention_days` 的每日日志，返回统计
- [ ] 实现 `scripts/memory_consolidate.py`：调用 Janitor + AI 整合，供 cron 调用
- [ ] 在 `Makefile` 添加 `consolidate` 目标

### T3-4 接入 AgentLoop
- [ ] 在 `AgentLoop` 中接入 `ContextBuilder`（Planner 使用）
- [ ] 在 `AgentLoop.run()` 末尾调用 `MemoryManager.consolidate_session`
- [ ] 验证：两次运行 `sunday run`，第二次能感知第一次的结果

**Phase 3 完成标准**：运行两次任务，第二次系统提示中包含第一次的关键记忆；每日日志文件正确生成。

---

## Phase 4：工具系统与技能

**目标**：实现工具调用管道，接入 CLI 工具和 MCP，实现技能懒加载。

> **Phase 4 开始前**：为每个 Task 填写验证方案（测试文件、函数名、安全约束），经确认后再开始实现。

### T4-1 ToolRegistry 与 Guard
- [ ] 实现 `src/sunday/tools/guard.py`
  - `ToolResultGuard.validate(result, meta) → str`
  - 截断保护（> max_output_chars）
  - 敏感信息过滤（API key 正则替换为 `[REDACTED]`）
  - 非字符串转字符串
- [ ] 实现 `src/sunday/tools/registry.py`
  - `ToolMeta`（name, description, input_schema, is_dangerous, timeout）
  - `ToolRegistry.__init__(config, confirmation_handler)`
  - `register(meta, fn)` → allow/deny list 过滤
  - `get_schemas() → list[dict]`：LLM 工具调用格式
  - `execute(tool_call, session_id) → str`：完整调用管道（见 design.md §6.1）

### T4-2 内置 Python 工具
- [ ] 实现 `src/sunday/tools/cli_tool.py`
  - `run_shell(command, timeout)` → `asyncio.create_subprocess_shell`
  - `register_cli_tools(registry)` → 注册到 ToolRegistry
- [ ] 实现内置文件工具（read_file, write_file, list_dir, search_files）
- [ ] 实现内置文本工具（text_search、substring_replace）

### T4-3 MCP 客户端
- [ ] 实现 `src/sunday/tools/mcp_client.py`
  - `MCPClientManager.initialize(servers)` → 启动 MCP 子进程
  - `MCPClientManager.get_tools(server_name)` → 返回工具列表
  - `MCPClientManager.close()` → 优雅关闭
  - 连接失败记录 warning，不阻塞启动

### T4-4 技能系统
- [ ] 实现 `src/sunday/skills/loader.py`
  - `SkillMeta`（name, description, path, requires）
  - `SkillLoader.discover() → list[SkillMeta]`：优先级搜索
  - `SkillLoader.get_summary_list() → str`：技能摘要（L0 注入）
  - `SkillLoader.load_full(name) → str`：懒加载 + 缓存
- [ ] 将 `load_skill(name)` 注册为 Agent 可调用的工具

### T4-5 内置技能包
- [ ] 创建 `skills/files/SKILL.md` + `skills/files/tools.py`（文件操作技能）
- [ ] 创建 `skills/web_search/SKILL.md` + `skills/web_search/tools.py`（网络搜索技能）
- [ ] 创建 `skills/code/SKILL.md` + `skills/code/tools.py`（代码辅助技能）

### T4-6 确认机制占位实现
- [ ] 在 CLI 模式下实现简单的 `confirmation_handler`：打印提示，stdin 读取 y/n
- [ ] 确保不可逆工具（`is_dangerous=True`）在 `sunday run` 中正确触发确认

**Phase 4 完成标准**：`sunday run "列出当前目录的所有 Python 文件"` 能调用 `list_dir` 工具并返回结果；`skills/files` 技能能被发现和懒加载。

---

## Phase 5：Gateway + TUI

**目标**：实现本地守护进程和终端交互界面，完成完整的 edge agent 体验。

> **Phase 5 开始前**：为每个 Task 填写验证方案（测试文件、函数名、安全约束），经确认后再开始实现。

### T5-1 Gateway 通信协议
- [ ] 实现 `src/sunday/gateway/protocol.py`
  - `EventType`（枚举，含客户端→服务端和服务端→客户端全部类型）
  - `Message`（type, session_id, data, ts）
  - 协议文档与 design.md §8.3 保持一致

### T5-2 SessionManager
- [ ] 实现 `src/sunday/gateway/session.py`
  - `SessionManager.__init__(sessions_dir)`
  - `new_session() → str`
  - `append(session_id, event_type, data)` → asyncio.Lock 保护
  - `load_history(session_id, max_events) → list[dict]`
  - `list_sessions() → list[dict]`：按 last_active 倒序
  - index.json 原子写

### T5-3 Gateway Server
- [ ] 实现 `src/sunday/gateway/server.py`
  - `Gateway.__init__`：按 design.md §12.1 顺序初始化所有组件
  - `start()` → WebSocket 服务，永久运行
  - `_handle(ws, path)` → 消息路由
  - `_handle_send`：检查串行、创建 Task、注册 done_callback
  - `_handle_abort`：task.cancel()
  - `_handle_slash`：会话控制（不走 Agent Loop）
  - `_handle_confirm`：resolve pending Future
  - `emit(session_id, event_type, data)` → 推送事件到客户端
  - `_build_state(session_id, content) → AgentState`
  - `_build_agent_loop(session_id) → AgentLoop`（注入 emit 回调）
  - `_pending_confirms`：不可逆操作 Future 字典
  - 启动时按序：配置校验 → 工作区初始化 → MCP 连接 → 工具注册 → 技能扫描 → 监听

### T5-4 CLI Gateway 命令
- [ ] 实现 `sunday gateway start`：后台启动（nohup 或 subprocess），写入 PID 文件
- [ ] 实现 `sunday gateway stop`：读 PID 文件，发送 SIGTERM
- [ ] 实现 `sunday gateway status`：检查 PID + WebSocket 心跳
- [ ] 实现 `sunday memory show/search` 命令
- [ ] 实现 `sunday skills list/install` 命令

### T5-5 TUI 基础框架
- [ ] 实现 `src/sunday/tui/app.py`
  - `SundayApp`（Textual App）5 区布局（Header / ChatLog / StatusBar / InfoBar / InputBar）
  - WebSocket 客户端（连接 Gateway，断线重连）
  - 键盘绑定（Ctrl+P/L/T/O/Esc）
  - Gateway 未运行时自动启动

### T5-6 TUI 核心组件
- [ ] 实现 `widgets/chat_log.py`：消息渲染、流式 token 追加、自动滚动
- [ ] 实现 `widgets/tool_card.py`：折叠卡片，输入/输出实时更新
- [ ] 实现 `widgets/status_bar.py`：状态指示（● 思考中 / ● 执行中 step N/M / ● 就绪）
- [ ] 实现 `widgets/input_bar.py`：输入框，`/` 补全，`!` 本地命令
- [ ] 创建 `src/sunday/tui/style.tcss`（布局样式）

### T5-7 Slash 命令
- [ ] 实现 `src/sunday/tui/commands.py`
  - `SlashCommandHandler` 分发所有 slash 命令
  - 实现所有命令：/think /model /session /sessions /new /reset /abort /memory /skills /help

### T5-8 TUI 与 Gateway 事件绑定
- [ ] 将 Gateway 全部事件类型映射到 TUI 对应行为（参考 design.md §9.4）
- [ ] 确认请求在 TUI 中以醒目方式呈现（高亮提示，y/n 快捷键确认）

**Phase 5 完成标准**：`sunday tui` 启动正常，能完成多轮对话，工具调用卡片实时显示，Slash 命令全部可用。

---

## Phase 6：实用技能

**目标**：落地日常办公自动化，验证边端 agent 的实际价值。

> **Phase 6 开始前**：为每个 Task 填写验证方案（测试文件、函数名、安全约束），经确认后再开始实现。

### T6-1 文件管理技能（增强）
- [ ] 完善 `skills/files/` 技能：支持文件搜索、内容检索、批量重命名
- [ ] 确认 MCP `mcp-server-filesystem` 集成正常

### T6-2 网络搜索技能（增强）
- [ ] 接入可用的搜索 API（Tavily 或 Brave Search）
- [ ] 实现 `fetch_url` 工具：抓取页面内容并提取正文
- [ ] 完善 `skills/web_search/SKILL.md`

### T6-3 邮件技能
- [ ] 创建 `skills/email/SKILL.md`（使用说明）
- [ ] 创建 `skills/email/tools.py`（Gmail API 工具：list、read、send、reply）
- [ ] 在 `configs/mcp_servers.yaml` 添加 Gmail MCP 服务器配置（或直接 API 实现）
- [ ] 测试：`sunday run "汇总今天未读邮件"` 能正常执行

### T6-4 日历技能
- [ ] 创建 `skills/calendar/SKILL.md`
- [ ] 创建 `skills/calendar/tools.py`（Google Calendar：list_events、create_event、update_event）
- [ ] 测试：`sunday run "明天上午有什么安排"` 能正常执行

### T6-5 每日简报定时任务
- [ ] 在 `configs/agent.yaml` 的 `tasks` 节定义 `daily_brief` 任务（工作日 9:00）
- [ ] 实现 `scripts/task_runner.py`：读取任务配置，调用 AgentLoop 执行
- [ ] 测试：手动触发 `uv run scripts/task_runner.py daily_brief` 能生成简报

### T6-6 记忆整合定时任务
- [ ] 配置 cron（或 launchd/systemd）每天凌晨 4 点运行 `scripts/memory_consolidate.py`
- [ ] 在 `Makefile` 添加 `make setup-cron` 目标（一键配置定时任务）
- [ ] 测试：手动运行整合脚本，验证过期日志清理和 MEMORY.md 更新

**Phase 6 完成标准**：能完成"整理今日邮件并生成摘要"、"查看明天日程"两个实际任务；每日简报能自动生成。

---

## 横向任务（贯穿各 Phase）

### TX-1 测试基础设施
- [ ] 配置 `pytest.ini`（asyncio_mode=auto）
- [ ] 配置 `vcrpy`（LLM 调用录制/回放，测试不消耗真实 API）
- [ ] 集成测试原则：文件操作不 mock；LLM 调用用 VCR 录制

### TX-2 日志规范
- [ ] 统一使用 Python 标准 `logging`，结构化格式（JSON 或 key=value）
- [ ] 日志文件：`~/.sunday/logs/YYYY-MM-DD.log`
- [ ] 日志级别通过环境变量 `SUNDAY_LOG_LEVEL` 控制（默认 INFO）

### TX-3 代码质量
- [ ] 配置 `ruff`（lint + format，集成到 `Makefile lint` 目标）
- [ ] 所有 public 接口有 docstring
- [ ] 遵循 `CLAUDE.md` 代码规范

### TX-4 文档同步
- [ ] 每个 Phase 结束后更新 `README.md` 的"快速开始"章节
- [ ] 配置变更同步到 `.env.example`
- [ ] 重大设计变更同步到 `design.md`

---

## 里程碑

| 里程碑 | 完成条件 | 对应 Phase |
|--------|----------|------------|
| **M1 能跑** | `sunday run "你好"` 返回 LLM 回复 | Phase 1 完成 |
| **M2 能推理** | `sunday run` 展示完整 think→plan→execute→verify | Phase 2 完成 |
| **M3 能记忆** | 第二次运行能感知第一次的结果 | Phase 3 完成 |
| **M4 能用工具** | 能调用 shell 命令和内置技能 | Phase 4 完成 |
| **M5 有界面** | `sunday tui` 完整可用 | Phase 5 完成 |
| **M6 能办公** | 能处理真实邮件和日程任务 | Phase 6 完成 |

---

## 任务依赖关系

```
T1-1 → T1-2 → T1-3 → T1-4
                         │
              T2-1 → T2-2 ┤
                     T2-3 ├→ T2-5 → T2-6
                     T2-4 ┘
                              │
              T3-1 → T3-2 ───┤
              T3-3            │
              T3-4 ───────────┘
                              │
              T4-1 ───────────┤
              T4-2            │
              T4-3 ───────────┤
              T4-4 → T4-5    ┘
                              │
              T5-1 → T5-2 ───┤
              T5-3 ───────────┤
              T5-4            │
              T5-5 → T5-6 ───┤
              T5-7 → T5-8 ───┘
                              │
              T6-1 ～ T6-6  ──┘
```

---

## 开发原则备忘

> 详见 `CLAUDE.md`，这里列出最容易遗忘的几条：

- **配置与实现分离**：任何 API key、模型名称、路径不得硬编码
- **记忆文件原子写**：所有写操作通过 `_atomic_write`（.tmp rename）
- **emit 注入，不反向依赖**：`agent/` 不得 import `gateway/`
- **VERIFY 不可跳过**：每步 execute 后必须经过 verify
- **不可逆操作必须确认**：`is_dangerous=True` 的工具在执行前等待用户确认
- **测试用真实文件系统**：不 mock 文件操作；LLM 调用用 VCR
