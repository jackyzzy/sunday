"""Team 内层重规划单元测试（mock Executor/Verifier/Planner，无真实 API）"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from sunday.agent.models import AgentState, Plan, Step, StepResult, StepStatus, TeamResult
from sunday.agent.verifier import VerifyResult


def _make_config(tmp_path, max_sub_replans: int = 1):
    """创建最小化 SundayConfig，注入 max_sub_replans 参数。"""
    from sunday.config import Settings

    config_file = tmp_path / "agent.yaml"
    config_file.write_text(yaml.dump({
        "model": {"provider": "openai", "id": "test-model", "api_key_env": "FAKE_KEY"},
        "reasoning": {"max_steps": 5, "max_sub_replans": max_sub_replans},
    }))
    with patch.dict(os.environ, {"FAKE_KEY": "sk-fake", "SUNDAY_CONFIGS_DIR": str(tmp_path)}):
        s = Settings()
        _ = s.sunday  # 触发 cached_property
        return s.sunday


def _make_step(step_id: str, intent: str = "") -> Step:
    return Step(id=step_id, intent=intent or step_id, success_criteria="")


def _make_state(session_id: str = "test-session") -> AgentState:
    return AgentState(session_id=session_id, task="test task")


def _passed_result(step_id: str, output: str = "ok") -> StepResult:
    return StepResult(step_id=step_id, status=StepStatus.DONE, output=output, verified=True)


def _failed_result(step_id: str, output: str = "fail") -> StepResult:
    return StepResult(step_id=step_id, status=StepStatus.FAILED, output=output, verified=False)


async def _mock_emit(*args, **kwargs):
    pass


@pytest.mark.asyncio
async def test_team_inner_replan_success(tmp_path):
    """子步骤首次失败 → 触发内层重规划 → 新子步骤通过 → TeamResult.passed=True"""
    from sunday.agent.team import Team

    cfg = _make_config(tmp_path, max_sub_replans=1)
    parent_step = _make_step("s1", "完成某个任务")
    parent_state = _make_state()

    sub1 = _make_step("sub1", "先做子任务1")
    sub2_orig = _make_step("sub2_orig", "原始子任务2")  # 会失败
    sub2_new = _make_step("sub2_new", "重规划后的子任务2")  # 会通过

    # Planner：think_and_plan → [sub1, sub2_orig]；replan → [sub2_new]
    mock_planner = MagicMock()
    mock_planner.config = cfg
    mock_planner.think_and_plan = AsyncMock(
        return_value=Plan(goal="完成某个任务", steps=[sub1, sub2_orig])
    )
    mock_planner.replan = AsyncMock(return_value=[sub2_new])

    # Executor：sub1 通过，sub2_orig 失败，sub2_new 通过
    async def mock_executor_run(step, state):
        if step.id in ("sub1", "sub2_new"):
            return _passed_result(step.id)
        return _failed_result(step.id)

    mock_executor = MagicMock()
    mock_executor.run = mock_executor_run

    # Verifier：result.verified 直接反映 Executor 输出；失败时 should_replan=True
    async def mock_verifier_check(step, result, state):
        return VerifyResult(passed=result.verified, reason="mock",
                            should_replan=not result.verified)

    mock_verifier = MagicMock()
    mock_verifier.check = mock_verifier_check

    team = Team.__new__(Team)
    team.planner = mock_planner
    team.executor = mock_executor
    team.verifier = mock_verifier
    team.emit = _mock_emit

    result = await team.run(parent_step, parent_state)

    assert result.passed is True
    assert result.step_id == "s1"
    # replan 被调用一次（sub2_orig 失败时）
    mock_planner.replan.assert_awaited_once()
    # 执行了 sub1, sub2_orig(失败), sub2_new(通过)
    executed_ids = [r.step_id for r in result.sub_steps]
    assert "sub1" in executed_ids
    assert "sub2_orig" in executed_ids
    assert "sub2_new" in executed_ids


@pytest.mark.asyncio
async def test_team_inner_replan_exhausted(tmp_path):
    """子步骤失败次数超出 max_sub_replans=1 → 上报失败，不再重规划"""
    from sunday.agent.team import Team

    cfg = _make_config(tmp_path, max_sub_replans=1)
    parent_step = _make_step("s1")
    parent_state = _make_state()

    sub1 = _make_step("sub1")          # 通过
    sub2 = _make_step("sub2")          # 失败
    sub2_r = _make_step("sub2_retry")  # 重规划后仍失败

    mock_planner = MagicMock()
    mock_planner.config = cfg
    mock_planner.think_and_plan = AsyncMock(
        return_value=Plan(goal="g", steps=[sub1, sub2])
    )
    mock_planner.replan = AsyncMock(return_value=[sub2_r])

    async def mock_executor_run(step, state):
        if step.id == "sub1":
            return _passed_result(step.id)
        return _failed_result(step.id)  # sub2 和 sub2_retry 都失败

    async def mock_verifier_check(step, result, state):
        return VerifyResult(passed=result.verified, reason="mock",
                            should_replan=not result.verified)

    mock_executor = MagicMock()
    mock_executor.run = mock_executor_run
    mock_verifier = MagicMock()
    mock_verifier.check = mock_verifier_check

    team = Team.__new__(Team)
    team.planner = mock_planner
    team.executor = mock_executor
    team.verifier = mock_verifier
    team.emit = _mock_emit

    result = await team.run(parent_step, parent_state)

    assert result.passed is False
    # replan 调用一次（sub2 失败时），但 sub2_retry 也失败，预算耗尽不再重规划
    mock_planner.replan.assert_awaited_once()
    executed_ids = [r.step_id for r in result.sub_steps]
    assert "sub2" in executed_ids
    assert "sub2_retry" in executed_ids


@pytest.mark.asyncio
async def test_team_inner_replan_returns_empty(tmp_path):
    """replan 返回空列表 → 立即停止，不挂起"""
    from sunday.agent.team import Team

    cfg = _make_config(tmp_path, max_sub_replans=1)
    parent_step = _make_step("s1")
    parent_state = _make_state()

    sub1 = _make_step("sub1")

    mock_planner = MagicMock()
    mock_planner.config = cfg
    mock_planner.think_and_plan = AsyncMock(
        return_value=Plan(goal="g", steps=[sub1])
    )
    mock_planner.replan = AsyncMock(return_value=[])  # 空列表

    async def mock_executor_run(step, state):
        return _failed_result(step.id)

    async def mock_verifier_check(step, result, state):
        return VerifyResult(passed=False, reason="mock", should_replan=True)

    mock_executor = MagicMock()
    mock_executor.run = mock_executor_run
    mock_verifier = MagicMock()
    mock_verifier.check = mock_verifier_check

    team = Team.__new__(Team)
    team.planner = mock_planner
    team.executor = mock_executor
    team.verifier = mock_verifier
    team.emit = _mock_emit

    result = await team.run(parent_step, parent_state)

    assert result.passed is False
    mock_planner.replan.assert_awaited_once()
    # 只执行了 sub1，没有额外的步骤
    assert len(result.sub_steps) == 1
    assert result.sub_steps[0].step_id == "sub1"
