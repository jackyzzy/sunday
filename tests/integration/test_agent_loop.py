"""T2-5 验证：ReactAgent 集成测试（mock LLM，无真实 API）

架构说明：ReactAgent 通过 Team 执行每个顶层 Step，不直接调用 executor/verifier。
测试通过 patch('sunday.agent.react_agent.Team') 来隔离 Team 行为。
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import yaml

from sunday.agent.executor import Executor, MaxStepsError
from sunday.agent.loop import AgentLoop  # 向后兼容别名，等价于 ReactAgent
from sunday.agent.models import AgentState, Plan, Step, StepResult, StepStatus, TeamResult
from sunday.agent.planner import Planner
from sunday.agent.react_agent import ReactAgent
from sunday.agent.verifier import Verifier, VerifyResult


def _make_settings(tmp_path, provider="anthropic"):
    from sunday.config import Settings
    config_file = tmp_path / "agent.yaml"
    config_file.write_text(yaml.dump({
        "model": {"provider": provider, "id": "claude-test", "max_tokens": 4096},
        "reasoning": {"max_steps": 5},
    }))
    with patch.dict(os.environ, {
        "ANTHROPIC_API_KEY": "sk-ant-fake",
        "SUNDAY_CONFIGS_DIR": str(tmp_path),
    }):
        return Settings()


def _make_plan(n_steps=2) -> Plan:
    return Plan(
        goal="完成测试任务",
        steps=[Step(id=f"step_{i+1}", intent=f"步骤{i+1}", success_criteria="完成")
               for i in range(n_steps)],
    )


def _make_team_result(step_id: str, passed: bool = True) -> TeamResult:
    return TeamResult(step_id=step_id, passed=passed, output=f"{step_id} 输出")


def _make_mock_agent(plan, evaluate_return="任务完成！"):
    """构造一个测试用 ReactAgent，使用 __new__ 绕过构造函数，手动注入 mock 组件。"""
    planner = MagicMock(spec=Planner)
    planner.think_and_plan = AsyncMock(return_value=plan)
    planner.system_prompt = ""

    verifier = MagicMock(spec=Verifier)
    verifier.evaluate = AsyncMock(return_value=evaluate_return)

    agent = ReactAgent.__new__(ReactAgent)
    agent.config = MagicMock()
    agent.config.reasoning.max_steps = 10
    agent.config.reasoning.max_replans = 5
    agent.config.agent.log_dir = None
    agent.config.agent.report_dir = None
    agent.config.nodes = {}
    agent.emit = AsyncMock()
    agent.mode = "test"
    agent.planner = planner
    agent.verifier = verifier
    agent.tool_registry = MagicMock()
    agent.tool_registry.clone = MagicMock(return_value=MagicMock())
    agent.tool_registry.set_report_dir = MagicMock()
    agent.context_builder = None
    agent.memory_manager = None
    return agent, planner, verifier


# ── 基本流程 ──────────────────────────────────────────────────────────────────

async def test_loop_completes_simple_task(tmp_path):
    """完整循环：plan → Team × 2 → evaluate → 返回摘要"""
    _make_settings(tmp_path)

    plan = _make_plan(2)
    agent, planner, verifier = _make_mock_agent(plan, evaluate_return="任务完成！")
    state = AgentState(session_id="sess", task="测试任务")

    team_results = [_make_team_result("step_1"), _make_team_result("step_2")]
    with patch("sunday.agent.react_agent.Team") as MockTeam:
        instance = MockTeam.return_value
        instance.run = AsyncMock(side_effect=team_results)
        result = await agent.run(state)

    assert result == "任务完成！"
    assert planner.think_and_plan.called
    assert instance.run.call_count == 2
    assert len(state.team_results) == 2
    assert state.plan is plan


async def test_loop_emit_called(tmp_path):
    """emit 回调在关键节点被调用"""
    _make_settings(tmp_path)

    plan = _make_plan(1)
    agent, planner, verifier = _make_mock_agent(plan, evaluate_return="done")

    emitted = []

    async def capture_emit(session_id, event_type, data):
        emitted.append((event_type, data))

    agent.emit = capture_emit
    state = AgentState(session_id="sess", task="test")

    with patch("sunday.agent.react_agent.Team") as MockTeam:
        instance = MockTeam.return_value
        instance.run = AsyncMock(return_value=_make_team_result("step_1"))
        await agent.run(state)

    event_types = [e[0] for e in emitted]
    assert "status" in event_types
    assert "plan" in event_types

    status_events = [e[1]["status"] for e in emitted if e[0] == "status" and "status" in e[1]]
    assert "thinking" in status_events
    assert "idle" in status_events


async def test_loop_step_result_verified_in_team_result(tmp_path):
    """team_results 的 passed 字段来自 Team.run 的返回"""
    _make_settings(tmp_path)

    plan = _make_plan(1)
    agent, planner, verifier = _make_mock_agent(plan, evaluate_return="done")
    state = AgentState(session_id="sess", task="test")

    with patch("sunday.agent.react_agent.Team") as MockTeam:
        instance = MockTeam.return_value
        instance.run = AsyncMock(return_value=_make_team_result("step_1", passed=True))
        await agent.run(state)

    assert state.team_results[0].passed is True
    assert state.team_results[0].step_id == "step_1"


# ── verify 失败触发重规划 ──────────────────────────────────────────────────────

async def test_team_failure_triggers_replan(tmp_path):
    """Team 执行失败时触发顶层重规划"""
    _make_settings(tmp_path)

    plan = _make_plan(2)
    new_steps = [Step(id="step_2_new", intent="新方法", success_criteria="完成")]

    planner = MagicMock(spec=Planner)
    planner.think_and_plan = AsyncMock(return_value=plan)
    planner.system_prompt = ""
    planner.replan = AsyncMock(return_value=new_steps)

    verifier = MagicMock(spec=Verifier)
    verifier.evaluate = AsyncMock(return_value="重规划后完成")

    agent = ReactAgent.__new__(ReactAgent)
    agent.config = MagicMock()
    agent.config.reasoning.max_steps = 10
    agent.config.reasoning.max_replans = 5
    agent.config.agent.log_dir = None
    agent.config.agent.report_dir = None
    agent.config.nodes = {}
    agent.emit = AsyncMock()
    agent.mode = "test"
    agent.planner = planner
    agent.verifier = verifier
    agent.tool_registry = MagicMock()
    agent.tool_registry.clone = MagicMock(return_value=MagicMock())
    agent.tool_registry.set_report_dir = MagicMock()
    agent.context_builder = None
    agent.memory_manager = None

    state = AgentState(session_id="sess", task="test")

    # step_1 通过，step_2 失败触发 replan，step_2_new 通过
    team_results_seq = [
        _make_team_result("step_1", passed=True),
        _make_team_result("step_2", passed=False),
        _make_team_result("step_2_new", passed=True),
    ]
    with patch("sunday.agent.react_agent.Team") as MockTeam:
        instance = MockTeam.return_value
        instance.run = AsyncMock(side_effect=team_results_seq)
        result = await agent.run(state)

    assert result == "重规划后完成"
    assert planner.replan.called
    step_ids = [s.id for s in state.plan.steps]
    assert "step_2_new" in step_ids


async def test_team_failure_no_replan_continues(tmp_path):
    """Team 失败但重规划返回空时，继续后续步骤"""
    _make_settings(tmp_path)

    plan = _make_plan(2)
    planner = MagicMock(spec=Planner)
    planner.think_and_plan = AsyncMock(return_value=plan)
    planner.system_prompt = ""
    planner.replan = AsyncMock(return_value=[])

    verifier = MagicMock(spec=Verifier)
    verifier.evaluate = AsyncMock(return_value="部分完成")

    agent = ReactAgent.__new__(ReactAgent)
    agent.config = MagicMock()
    agent.config.reasoning.max_steps = 10
    agent.config.reasoning.max_replans = 5
    agent.config.agent.log_dir = None
    agent.config.agent.report_dir = None
    agent.config.nodes = {}
    agent.emit = AsyncMock()
    agent.mode = "test"
    agent.planner = planner
    agent.verifier = verifier
    agent.tool_registry = MagicMock()
    agent.tool_registry.clone = MagicMock(return_value=MagicMock())
    agent.tool_registry.set_report_dir = MagicMock()
    agent.context_builder = None
    agent.memory_manager = None

    state = AgentState(session_id="sess", task="test")

    with patch("sunday.agent.react_agent.Team") as MockTeam:
        instance = MockTeam.return_value
        instance.run = AsyncMock(return_value=_make_team_result("step_1", passed=False))
        result = await agent.run(state)

    assert result == "部分完成"


# ── 依赖满足检查 ──────────────────────────────────────────────────────────────

async def test_deps_satisfied_skips_unmet(tmp_path):
    """依赖未满足的步骤被跳过"""
    _make_settings(tmp_path)

    step1 = Step(id="step_1", intent="步骤1", success_criteria="")
    step2 = Step(id="step_2", intent="步骤2", depends_on=["step_1"], success_criteria="")
    plan = Plan(goal="测试", steps=[step1, step2])

    planner = MagicMock(spec=Planner)
    planner.think_and_plan = AsyncMock(return_value=plan)
    planner.system_prompt = ""
    planner.replan = AsyncMock(return_value=[])

    verifier = MagicMock(spec=Verifier)
    verifier.evaluate = AsyncMock(return_value="done")

    agent = ReactAgent.__new__(ReactAgent)
    agent.config = MagicMock()
    agent.config.reasoning.max_steps = 10
    agent.config.reasoning.max_replans = 5
    agent.config.agent.log_dir = None
    agent.config.agent.report_dir = None
    agent.config.nodes = {}
    agent.emit = AsyncMock()
    agent.mode = "test"
    agent.planner = planner
    agent.verifier = verifier
    agent.tool_registry = MagicMock()
    agent.tool_registry.clone = MagicMock(return_value=MagicMock())
    agent.tool_registry.set_report_dir = MagicMock()
    agent.context_builder = None
    agent.memory_manager = None

    state = AgentState(session_id="sess", task="test")

    with patch("sunday.agent.react_agent.Team") as MockTeam:
        instance = MockTeam.return_value
        # step_1 执行并返回 passed=False，step_2 依赖 step_1 passed → 被跳过
        instance.run = AsyncMock(return_value=_make_team_result("step_1", passed=False))
        await agent.run(state)

    # step_2 依赖未满足，Team.run 只被调用了 1 次
    assert instance.run.call_count == 1


# ── T3-4：ContextBuilder + MemoryManager 接入 ReactAgent ──────────────────────

async def test_loop_injects_context_into_planner(tmp_path):
    """context_builder.build() 被调用，planner.system_prompt 被设置"""
    from sunday.memory.context import ContextBuilder

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "memory").mkdir()
    (workspace / "SOUL.md").write_text("# Soul\n你是 Sunday。")

    plan = _make_plan(1)
    planner = MagicMock(spec=Planner)
    planner.think_and_plan = AsyncMock(return_value=plan)
    planner.system_prompt = ""

    verifier = MagicMock(spec=Verifier)
    verifier.evaluate = AsyncMock(return_value="done")

    context_builder = ContextBuilder(workspace)

    agent = ReactAgent.__new__(ReactAgent)
    agent.config = MagicMock()
    agent.config.reasoning.max_steps = 10
    agent.config.reasoning.max_replans = 5
    agent.config.agent.log_dir = None
    agent.config.agent.report_dir = None
    agent.config.nodes = {}
    agent.emit = AsyncMock()
    agent.mode = "test"
    agent.planner = planner
    agent.verifier = verifier
    agent.tool_registry = MagicMock()
    agent.tool_registry.clone = MagicMock(return_value=MagicMock())
    agent.tool_registry.set_report_dir = MagicMock()
    agent.context_builder = context_builder
    agent.memory_manager = None

    state = AgentState(session_id="s1", task="测试注入")

    with patch("sunday.agent.react_agent.Team") as MockTeam:
        instance = MockTeam.return_value
        instance.run = AsyncMock(return_value=_make_team_result("step_1"))
        await agent.run(state)

    assert "你是 Sunday" in planner.system_prompt


async def test_loop_calls_memory_consolidate(tmp_path):
    """memory_manager.consolidate_session 在循环结束后被调用"""
    from unittest.mock import AsyncMock as AM

    plan = _make_plan(1)
    planner = MagicMock(spec=Planner)
    planner.think_and_plan = AsyncMock(return_value=plan)
    planner.system_prompt = ""

    verifier = MagicMock(spec=Verifier)
    verifier.evaluate = AM(return_value="done")

    memory_manager = MagicMock()
    memory_manager.consolidate_session = AM(return_value=None)

    agent = ReactAgent.__new__(ReactAgent)
    agent.config = MagicMock()
    agent.config.reasoning.max_steps = 10
    agent.config.reasoning.max_replans = 5
    agent.config.agent.log_dir = None
    agent.config.agent.report_dir = None
    agent.config.nodes = {}
    agent.emit = AsyncMock()
    agent.mode = "test"
    agent.planner = planner
    agent.verifier = verifier
    agent.tool_registry = MagicMock()
    agent.tool_registry.clone = MagicMock(return_value=MagicMock())
    agent.tool_registry.set_report_dir = MagicMock()
    agent.context_builder = None
    agent.memory_manager = memory_manager

    state = AgentState(session_id="s2", task="记忆整合测试")

    with patch("sunday.agent.react_agent.Team") as MockTeam:
        instance = MockTeam.return_value
        instance.run = AsyncMock(return_value=_make_team_result("step_1"))
        await agent.run(state)

    memory_manager.consolidate_session.assert_called_once_with(state)


async def test_loop_no_context_builder_runs_fine(tmp_path):
    """不传 context_builder 时循环正常运行（向后兼容）"""
    plan = _make_plan(1)
    agent, planner, verifier = _make_mock_agent(plan, evaluate_return="done")
    agent.context_builder = None
    agent.memory_manager = None
    state = AgentState(session_id="s3", task="向后兼容测试")

    with patch("sunday.agent.react_agent.Team") as MockTeam:
        instance = MockTeam.return_value
        instance.run = AsyncMock(return_value=_make_team_result("step_1"))
        result = await agent.run(state)

    assert result == "done"
