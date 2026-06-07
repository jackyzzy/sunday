"""ReactAgent 集成测试（mock LLM + mock MemoryClient，无真实 API/IO）

ReactAgent 通过 Team 执行每个顶层 Step，不直接调用 executor/verifier。
测试通过 patch('sunday.agent.react_agent.Team') 来隔离 Team 行为。
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import yaml

from sunday.agent.models import AgentState, Plan, Step, TeamResult
from sunday.agent.planner import Planner
from sunday.agent.react_agent import ReactAgent
from sunday.agent.verifier import Verifier


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


def _mock_memory_client() -> MagicMock:
    """构造满足 ReactAgent 调用需求的 MemoryClient mock。"""
    client = MagicMock()
    client.workspace.read_runtime_rules = AsyncMock(return_value=None)
    client.logs.emit = AsyncMock(return_value=None)
    return client


def _make_mock_agent(plan, evaluate_return="任务完成！"):
    """通过 __new__ 绕过构造函数，手动注入 mock 组件。"""
    planner = MagicMock(spec=Planner)
    planner.think_and_plan = AsyncMock(return_value=plan)
    planner.system_prompt = ""
    planner._runtime_rules = None
    planner.replan = AsyncMock(return_value=[])

    verifier = MagicMock(spec=Verifier)
    verifier.evaluate = AsyncMock(return_value=evaluate_return)

    context_builder = MagicMock()
    context_builder.build = AsyncMock(return_value=MagicMock(
        system_prompt="", token_estimate=0,
    ))

    consolidator = MagicMock()
    consolidator.consolidate = AsyncMock(return_value=None)

    agent = ReactAgent.__new__(ReactAgent)
    agent.config = MagicMock()
    agent.config.reasoning.max_steps = 10
    agent.config.reasoning.max_replans = 5
    agent.config.agent.log_dir = None
    agent.config.nodes = {}
    agent.emit = AsyncMock()
    agent.mode = "test"
    agent.planner = planner
    agent.verifier = verifier
    agent.context_builder = context_builder
    agent.consolidator = consolidator
    agent.memory = _mock_memory_client()
    agent.tool_registry = MagicMock()
    agent.tool_registry.probe_all = AsyncMock()
    agent.tool_registry.connect_mcp = AsyncMock()
    agent.tool_registry.close_mcp = AsyncMock()
    agent.tool_registry.clone = MagicMock(return_value=MagicMock())
    agent.tool_registry.set_report_dir = MagicMock()
    agent.tool_registry.agent_written_files = []
    agent.session_report_dir = None
    return agent, planner, verifier


# ── 基本流程 ──────────────────────────────────────────────────────────────────

async def test_loop_completes_simple_task(tmp_path):
    _make_settings(tmp_path)

    plan = _make_plan(2)
    agent, planner, _ = _make_mock_agent(plan, evaluate_return="任务完成！")
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
    _make_settings(tmp_path)

    plan = _make_plan(1)
    agent, _, _ = _make_mock_agent(plan, evaluate_return="done")

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
    _make_settings(tmp_path)

    plan = _make_plan(1)
    agent, _, _ = _make_mock_agent(plan, evaluate_return="done")
    state = AgentState(session_id="sess", task="test")

    with patch("sunday.agent.react_agent.Team") as MockTeam:
        instance = MockTeam.return_value
        instance.run = AsyncMock(return_value=_make_team_result("step_1", passed=True))
        await agent.run(state)

    assert state.team_results[0].passed is True
    assert state.team_results[0].step_id == "step_1"


# ── verify 失败触发重规划 ──────────────────────────────────────────────────────

async def test_team_failure_triggers_replan(tmp_path):
    _make_settings(tmp_path)

    plan = _make_plan(2)
    new_steps = [Step(id="step_2_new", intent="新方法", success_criteria="完成")]

    agent, planner, _ = _make_mock_agent(plan, evaluate_return="重规划后完成")
    planner.replan = AsyncMock(return_value=new_steps)

    state = AgentState(session_id="sess", task="test")

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
    _make_settings(tmp_path)

    plan = _make_plan(2)
    agent, planner, _ = _make_mock_agent(plan, evaluate_return="部分完成")
    planner.replan = AsyncMock(return_value=[])

    state = AgentState(session_id="sess", task="test")

    with patch("sunday.agent.react_agent.Team") as MockTeam:
        instance = MockTeam.return_value
        instance.run = AsyncMock(return_value=_make_team_result("step_1", passed=False))
        result = await agent.run(state)

    assert result == "部分完成"


# ── 依赖满足检查 ──────────────────────────────────────────────────────────────

async def test_deps_satisfied_skips_unmet(tmp_path):
    _make_settings(tmp_path)

    step1 = Step(id="step_1", intent="步骤1", success_criteria="")
    step2 = Step(id="step_2", intent="步骤2", depends_on=["step_1"], success_criteria="")
    plan = Plan(goal="测试", steps=[step1, step2])

    agent, planner, _ = _make_mock_agent(plan, evaluate_return="done")
    planner.replan = AsyncMock(return_value=[])

    state = AgentState(session_id="sess", task="test")

    with patch("sunday.agent.react_agent.Team") as MockTeam:
        instance = MockTeam.return_value
        # step_1 失败 → step_2 因依赖未满足被跳过
        instance.run = AsyncMock(return_value=_make_team_result("step_1", passed=False))
        await agent.run(state)

    assert instance.run.call_count == 1


# ── ContextBuilder + Consolidator 接入 ────────────────────────────────────

async def test_loop_injects_context_into_planner(tmp_path):
    """context_builder.build() 被调用，planner.system_prompt 被设置。"""
    plan = _make_plan(1)
    agent, planner, _ = _make_mock_agent(plan, evaluate_return="done")
    agent.context_builder.build = AsyncMock(return_value=MagicMock(
        system_prompt="# Soul\n你是 Sunday。", token_estimate=10,
    ))

    state = AgentState(session_id="s1", task="测试注入")

    with patch("sunday.agent.react_agent.Team") as MockTeam:
        instance = MockTeam.return_value
        instance.run = AsyncMock(return_value=_make_team_result("step_1"))
        await agent.run(state)

    assert "你是 Sunday" in planner.system_prompt


async def test_loop_calls_consolidate(tmp_path):
    """consolidator.consolidate 在循环结束后被调用。"""
    plan = _make_plan(1)
    agent, _, _ = _make_mock_agent(plan, evaluate_return="done")

    state = AgentState(session_id="s2", task="记忆整合测试")

    with patch("sunday.agent.react_agent.Team") as MockTeam:
        instance = MockTeam.return_value
        instance.run = AsyncMock(return_value=_make_team_result("step_1"))
        await agent.run(state)

    agent.consolidator.consolidate.assert_called_once_with(state)


async def test_loop_logs_session_lifecycle(tmp_path):
    """session_start + session_end 通过 client.logs.emit 写入。"""
    plan = _make_plan(1)
    agent, _, _ = _make_mock_agent(plan, evaluate_return="done")

    state = AgentState(session_id="s3", task="日志测试")

    with patch("sunday.agent.react_agent.Team") as MockTeam:
        instance = MockTeam.return_value
        instance.run = AsyncMock(return_value=_make_team_result("step_1"))
        await agent.run(state)

    events = [call.args[1].event for call in agent.memory.logs.emit.call_args_list]
    assert "session_start" in events
    assert "session_end" in events
