"""相同失败原因早退机制测试

验证 Team 层在检测到相邻两次 sub-step 失败原因高度相似时，
跳过剩余 sub-replan，避免浪费时间在结构性失败上重复尝试。
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sunday.agent.team import _is_same_failure


# ── 纯函数测试：_is_same_failure ─────────────────────────────────────────────

def test_is_same_failure_high_overlap():
    """词袋重叠率 ≥ 0.6 → True"""
    prev = "来源标注缺失 未提供有效URL 无法满足来源要求 数据缺失"
    curr = "来源标注不合格 未提供有效URL 无法满足来源要求 数据不足"
    assert _is_same_failure(prev, curr) is True


def test_is_same_failure_low_overlap():
    """词袋重叠率 < 0.6 → False"""
    prev = "来源URL缺失 无法追溯数据"
    curr = "目标对齐失败 厂商数量不足 仅覆盖3家 要求10家"
    assert _is_same_failure(prev, curr) is False


def test_is_same_failure_too_short():
    """reason 词数 < 5 → False（不做相似度判断，避免误判）"""
    prev = "失败"
    curr = "失败"
    assert _is_same_failure(prev, curr) is False


def test_is_same_failure_empty_prev():
    """prev 为空 → False"""
    assert _is_same_failure("", "来源标注缺失 未提供有效URL 数据不足 无法验证") is False


def test_is_same_failure_empty_curr():
    """curr 为空 → False"""
    assert _is_same_failure("来源标注缺失 未提供有效URL 数据不足 无法验证", "") is False


def test_is_same_failure_identical():
    """完全相同的 reason → True"""
    reason = "来源标注仅给机构名称 未提供具体网页标题或URL 无法满足每条事实标注来源的要求"
    assert _is_same_failure(reason, reason) is True


def test_is_same_failure_custom_threshold():
    """threshold=0.9 时较低相似度返回 False"""
    prev = "来源标注缺失 未提供有效URL 数据不足 无法验证来源"
    curr = "来源标注缺失 未提供有效URL 数据不足 缺少时间节点"
    assert _is_same_failure(prev, curr, threshold=0.9) is False
    assert _is_same_failure(prev, curr, threshold=0.5) is True


# ── 集成测试：Team sub-replan 早退 ──────────────────────────────────────────

def _make_team(config=None):
    """构造一个最小 Team，工具注册表为空。"""
    from sunday.agent.team import Team

    if config is None:
        from sunday.config import SundayConfig
        config = SundayConfig()

    tool_registry = MagicMock()
    tool_registry.get_schemas.return_value = []
    tool_registry.execute = AsyncMock(return_value="观察结果")
    return Team(config=config, tool_registry=tool_registry)


def _make_state(session_id="test-session"):
    from sunday.agent.models import AgentState
    return AgentState(session_id=session_id, task="测试任务")


@pytest.mark.asyncio
async def test_same_failure_reason_skips_subreplan():
    """第2次失败原因与第1次高度相似 → 不触发第2次 sub-replan，直接终止。"""
    from sunday.agent.models import Step, StepResult, StepStatus
    from sunday.agent.verifier import VerifyResult

    similar_reason = "来源标注缺失 未提供有效URL 无法满足来源要求 数据不足 不合格"

    step = Step(
        id="step_1",
        intent="调研厂商",
        step_type="research",
        expected_output="厂商报告",
        success_criteria="10家厂商完整数据",
    )
    state = _make_state()

    team = _make_team()

    # sub_plan: 1个子步骤
    from sunday.agent.models import Plan
    sub_plan = Plan(goal="调研厂商", task_type="research", steps=[
        Step(id="step_1.1", intent="搜索厂商", step_type="research",
             expected_output="", success_criteria=""),
    ])

    # executor 总是返回 FAILED
    team.executor.run = AsyncMock(return_value=StepResult(
        step_id="step_1.1", status=StepStatus.FAILED, output="搜索结果不完整"
    ))

    # verifier 两次都失败，reason 高度相似
    verify_call_count = 0

    async def mock_check(sub_step, result, sub_state):
        nonlocal verify_call_count
        verify_call_count += 1
        return VerifyResult(passed=False, reason=similar_reason, should_replan=True)

    team.verifier.check = mock_check

    # planner: sub_plan 正常；sub_replan 记录调用次数
    sub_replan_call_count = 0

    async def mock_sub_replan(parent_step, failed_sub_step, result_output, sub_state):
        nonlocal sub_replan_call_count
        sub_replan_call_count += 1
        return [Step(id="step_1.R1", intent="重试搜索", step_type="research",
                     expected_output="", success_criteria="")]

    team.planner.think_and_plan = AsyncMock(return_value=sub_plan)
    team.planner.sub_replan = mock_sub_replan

    result = await team.run(step, state)

    # 第1次失败触发 sub-replan（计数 1），第2次失败检测相似 → 不再触发
    assert sub_replan_call_count == 1, (
        f"相同原因时应只触发1次 sub-replan，实际触发 {sub_replan_call_count} 次"
    )
    assert result.passed is False


@pytest.mark.asyncio
async def test_different_failure_reason_still_replans():
    """两次失败原因不同 → 仍触发第2次 sub-replan（不提前终止）。"""
    from sunday.agent.models import Plan, Step, StepResult, StepStatus
    from sunday.agent.verifier import VerifyResult

    reasons = [
        "来源标注缺失 未提供有效URL 无法满足来源要求 数据不足",  # 第1次：来源问题
        "目标对齐失败 仅覆盖3家厂商 要求覆盖10家 未达标准",      # 第2次：完全不同
    ]
    reason_idx = 0

    step = Step(
        id="step_2",
        intent="调研厂商",
        step_type="research",
        expected_output="",
        success_criteria="",
    )
    state = _make_state()
    team = _make_team()

    sub_plan = Plan(goal="调研", task_type="research", steps=[
        Step(id="step_2.1", intent="搜索", step_type="research",
             expected_output="", success_criteria=""),
    ])
    replacement = [Step(id="step_2.R1", intent="再搜索", step_type="research",
                        expected_output="", success_criteria="")]

    team.executor.run = AsyncMock(return_value=StepResult(
        step_id="step_2.1", status=StepStatus.FAILED, output="不完整"
    ))

    sub_replan_call_count = 0

    async def mock_check(sub_step, result, sub_state):
        nonlocal reason_idx
        r = reasons[min(reason_idx, len(reasons) - 1)]
        reason_idx += 1
        return VerifyResult(passed=False, reason=r, should_replan=True)

    async def mock_sub_replan(parent_step, failed_sub_step, result_output, sub_state):
        nonlocal sub_replan_call_count
        sub_replan_call_count += 1
        return replacement if sub_replan_call_count <= 1 else []

    team.verifier.check = mock_check
    team.planner.think_and_plan = AsyncMock(return_value=sub_plan)
    team.planner.sub_replan = mock_sub_replan

    result = await team.run(step, state)

    assert sub_replan_call_count >= 1, "不同原因时应至少触发1次 sub-replan"
