"""简单/复杂步骤路由单元测试（mock LLMClient/Executor/Verifier，无真实 API）"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from sunday.agent.models import AgentState, Plan, Step, StepResult, StepStatus, TeamResult
from sunday.agent.verifier import VerifyResult

# ─── 通用工厂 ────────────────────────────────────────────────────────────────

def _make_config(tmp_path):
    from sunday.config import Settings

    config_file = tmp_path / "agent.yaml"
    config_file.write_text(yaml.dump({
        "model": {"provider": "openai", "id": "test-model", "api_key_env": "FAKE_KEY"},
        "reasoning": {"max_react_iterations_per_substep": 5, "max_replans_per_step": 1,
                      "max_replans": 5},
    }))
    with patch.dict(os.environ, {"FAKE_KEY": "sk-fake", "SUNDAY_CONFIGS_DIR": str(tmp_path)}):
        s = Settings()
        _ = s.sunday
        return s.sunday


def _step(step_id: str, is_simple: bool = False) -> Step:
    return Step(id=step_id, intent=step_id, is_simple=is_simple)


def _state(task: str = "test") -> AgentState:
    return AgentState(session_id="test-session", task=task)


def _ok_result(step_id: str) -> StepResult:
    return StepResult(step_id=step_id, status=StepStatus.DONE, output="ok", verified=True)


def _fail_result(step_id: str) -> StepResult:
    return StepResult(step_id=step_id, status=StepStatus.FAILED, output="fail", verified=False)


async def _noop_emit(*args, **kwargs):
    pass


# ─── SimpleNode 测试 ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_simple_node_returns_team_result(tmp_path):
    """SimpleNode.run() 在执行通过时返回 passed=True 的标准 TeamResult。"""
    from sunday.agent.simple import SimpleNode

    cfg = _make_config(tmp_path)
    step = _step("s1", is_simple=True)
    state = _state()

    mock_executor = MagicMock()
    mock_executor.run = AsyncMock(return_value=_ok_result("s1"))

    mock_verifier = MagicMock()
    mock_verifier.check = AsyncMock(
        return_value=VerifyResult(passed=True, reason="ok")
    )

    node = SimpleNode.__new__(SimpleNode)
    node.executor = mock_executor
    node.verifier = mock_verifier
    node.emit = _noop_emit

    result = await node.run(step, state)

    assert isinstance(result, TeamResult)
    assert result.step_id == "s1"
    assert result.passed is True
    assert len(result.sub_steps) == 1
    assert result.sub_steps[0].step_id == "s1"
    mock_executor.run.assert_awaited_once()
    mock_verifier.check.assert_awaited_once()


@pytest.mark.asyncio
async def test_simple_node_returns_failed_when_verify_fails(tmp_path):
    """SimpleNode：执行成功但验证失败时 TeamResult.passed=False。"""
    from sunday.agent.simple import SimpleNode

    cfg = _make_config(tmp_path)
    step = _step("s1", is_simple=True)
    state = _state()

    mock_executor = MagicMock()
    mock_executor.run = AsyncMock(return_value=_ok_result("s1"))

    mock_verifier = MagicMock()
    mock_verifier.check = AsyncMock(
        return_value=VerifyResult(passed=False, reason="不符合预期")
    )

    node = SimpleNode.__new__(SimpleNode)
    node.executor = mock_executor
    node.verifier = mock_verifier
    node.emit = _noop_emit

    result = await node.run(step, state)

    assert result.passed is False
    assert result.sub_steps[0].verified is False


@pytest.mark.asyncio
async def test_simple_node_handles_executor_exception(tmp_path):
    """SimpleNode：Executor 抛出 MaxStepsError 时仍返回 TeamResult（passed 由 verify 决定）。"""
    from sunday.agent.executor import MaxStepsError
    from sunday.agent.simple import SimpleNode

    cfg = _make_config(tmp_path)
    step = _step("s1", is_simple=True)
    state = _state()

    mock_executor = MagicMock()
    mock_executor.run = AsyncMock(side_effect=MaxStepsError("超出步数"))

    mock_verifier = MagicMock()
    mock_verifier.check = AsyncMock(
        return_value=VerifyResult(passed=False, reason="执行异常")
    )

    node = SimpleNode.__new__(SimpleNode)
    node.executor = mock_executor
    node.verifier = mock_verifier
    node.emit = _noop_emit

    result = await node.run(step, state)

    assert isinstance(result, TeamResult)
    assert result.passed is False
    assert result.sub_steps[0].status == StepStatus.FAILED


# ─── loop.py 路由测试 ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_simple_step_routes_to_simple_node(tmp_path):
    """is_simple=True 的步骤路由到 SimpleNode，不创建 Team。"""
    from unittest.mock import patch as upatch

    from sunday.agent.models import Plan
    from sunday.agent.react_agent import ReactAgent

    cfg = _make_config(tmp_path)
    simple_step = _step("s1", is_simple=True)
    state = _state()
    state.plan = Plan(goal="test", steps=[simple_step])

    mock_simple_node = MagicMock()
    mock_simple_node.run = AsyncMock(
        return_value=TeamResult(step_id="s1", passed=True, output="done")
    )
    mock_team = MagicMock()
    mock_team.run = AsyncMock(
        return_value=TeamResult(step_id="s1", passed=True, output="done")
    )

    mock_verifier = MagicMock()
    mock_verifier.evaluate = AsyncMock(return_value="完成")

    mock_tool_registry = MagicMock()
    mock_tool_registry.clone = MagicMock(return_value=MagicMock())

    agent = ReactAgent.__new__(ReactAgent)
    agent.config = cfg
    agent.tool_registry = mock_tool_registry
    agent.verifier = mock_verifier
    agent.emit = _noop_emit

    with upatch("sunday.agent.react_agent.SimpleNode", return_value=mock_simple_node) as mock_sn_cls, \
         upatch("sunday.agent.react_agent.Team", return_value=mock_team) as mock_team_cls:
        await agent.execute(state, _noop_emit)

    # SimpleNode 被实例化，Team 未被实例化
    mock_sn_cls.assert_called_once()
    mock_team_cls.assert_not_called()
    mock_simple_node.run.assert_awaited_once()


@pytest.mark.asyncio
async def test_complex_step_routes_to_team(tmp_path):
    """is_simple=False 的步骤路由到 Team，不创建 SimpleNode。"""
    from unittest.mock import patch as upatch

    from sunday.agent.react_agent import ReactAgent

    cfg = _make_config(tmp_path)
    complex_step = _step("s1", is_simple=False)
    state = _state()
    state.plan = Plan(goal="test", steps=[complex_step])

    mock_simple_node = MagicMock()
    mock_team = MagicMock()
    mock_team.run = AsyncMock(
        return_value=TeamResult(step_id="s1", passed=True, output="done")
    )

    mock_verifier = MagicMock()
    mock_verifier.evaluate = AsyncMock(return_value="完成")

    mock_tool_registry = MagicMock()
    mock_tool_registry.clone = MagicMock(return_value=MagicMock())

    agent = ReactAgent.__new__(ReactAgent)
    agent.config = cfg
    agent.tool_registry = mock_tool_registry
    agent.verifier = mock_verifier
    agent.emit = _noop_emit

    with upatch("sunday.agent.react_agent.SimpleNode", return_value=mock_simple_node) as mock_sn_cls, \
         upatch("sunday.agent.react_agent.Team", return_value=mock_team) as mock_team_cls:
        await agent.execute(state, _noop_emit)

    # Team 被实例化，SimpleNode 未被实例化
    mock_team_cls.assert_called_once()
    mock_sn_cls.assert_not_called()
    mock_team.run.assert_awaited_once()


# ─── should_replan 路由测试 ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_should_replan_false_skips_team_replan(tmp_path):
    """verify.should_replan=False 时 Team 内层不触发重规划，直接停止。"""
    from sunday.agent.team import Team

    cfg = _make_config(tmp_path, )
    parent_step = _step("s1")
    parent_state = _state()

    sub1 = _step("sub1")

    mock_planner = MagicMock()
    mock_planner.config = cfg
    mock_planner.think_and_plan = AsyncMock(
        return_value=Plan(goal="g", steps=[sub1])
    )
    mock_planner.sub_replan = AsyncMock(return_value=[_step("sub1_retry")])

    mock_executor = MagicMock()
    mock_executor.run = AsyncMock(return_value=_fail_result("sub1"))

    # should_replan=False — 即使子步骤失败，也不应触发重规划
    mock_verifier = MagicMock()
    mock_verifier.check = AsyncMock(
        return_value=VerifyResult(passed=False, reason="失败", should_replan=False)
    )

    team = Team.__new__(Team)
    team.planner = mock_planner
    team.executor = mock_executor
    team.verifier = mock_verifier
    team.emit = _noop_emit

    result = await team.run(parent_step, parent_state)

    assert result.passed is False
    # sub_replan 未被调用
    mock_planner.sub_replan.assert_not_awaited()


@pytest.mark.asyncio
async def test_should_replan_true_triggers_team_replan(tmp_path):
    """verify.should_replan=True 时 Team 内层触发重规划。"""
    from sunday.agent.team import Team

    cfg = _make_config(tmp_path)
    parent_step = _step("s1")
    parent_state = _state()

    sub1 = _step("sub1")
    sub1_retry = _step("sub1_retry")

    mock_planner = MagicMock()
    mock_planner.config = cfg
    mock_planner.think_and_plan = AsyncMock(
        return_value=Plan(goal="g", steps=[sub1])
    )
    mock_planner.sub_replan = AsyncMock(return_value=[sub1_retry])

    async def mock_exec(step, state):
        if step.id == "sub1_retry":
            return _ok_result(step.id)
        return _fail_result(step.id)

    mock_executor = MagicMock()
    mock_executor.run = mock_exec

    async def mock_check(step, result, state):
        return VerifyResult(
            passed=result.verified,
            reason="mock",
            should_replan=not result.verified,  # 失败时建议重规划
        )

    mock_verifier = MagicMock()
    mock_verifier.check = mock_check

    team = Team.__new__(Team)
    team.planner = mock_planner
    team.executor = mock_executor
    team.verifier = mock_verifier
    team.emit = _noop_emit

    result = await team.run(parent_step, parent_state)

    assert result.passed is True
    mock_planner.sub_replan.assert_awaited_once()
