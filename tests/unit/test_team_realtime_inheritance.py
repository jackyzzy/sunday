"""S2-F 验证：sub_step 默认继承父 step 的 requires_realtime_data。

- 父 requires_realtime_data=True → 子步骤继承（除 step_type=synthesis 外）
- step_type=synthesis 的子步骤不继承（综合类基于已搜回数据，不需重新联网）
- 父 requires_realtime_data=False → 子步骤保持 LLM 输出，不强制
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import yaml

from sunday.agent.models import AgentState, Plan, Step
from sunday.agent.team import Team


def _make_settings(tmp_path: Path):
    from sunday.config import Settings

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "SOUL.md").write_text("# Sunday\n", encoding="utf-8")
    (tmp_path / "agent.yaml").write_text(yaml.dump({
        "agent": {
            "workspace_dir": str(workspace),
            "memory_dir": str(tmp_path / "memory"),
            "sessions_dir": str(tmp_path / "sessions"),
            "log_dir": str(tmp_path / "logs"),
        },
        "model": {"provider": "anthropic", "id": "claude-test"},
    }))
    with patch.dict(os.environ, {
        "ANTHROPIC_API_KEY": "sk-ant-fake",
        "SUNDAY_CONFIGS_DIR": str(tmp_path),
    }):
        s = Settings()
        _ = s.sunday
        return s


class _StubRegistry:
    def get_schemas(self):
        return []


def _make_team(tmp_path: Path) -> Team:
    settings = _make_settings(tmp_path)
    return Team(settings.sunday, tool_registry=_StubRegistry())


def _build_run_with_subplan(team: Team, sub_steps: list[Step]):
    """让 planner 返回固定 sub_plan，仅观察 sub_steps 的 requires_realtime_data。"""
    sub_plan = Plan(goal="子目标", steps=sub_steps)
    team.planner.think_and_plan = AsyncMock(return_value=sub_plan)
    # 避免真实 executor / verifier 调用：把循环立即结束
    team.executor.run = AsyncMock(side_effect=AssertionError("不应被调用"))


@pytest.mark.asyncio
async def test_sub_steps_inherit_when_parent_realtime_true(tmp_path: Path):
    team = _make_team(tmp_path)
    parent_step = Step(
        id="step_1", intent="调研 X", requires_realtime_data=True, step_type="research",
    )
    sub_steps = [
        Step(id="step_1.1", intent="搜资料", step_type="research"),
        Step(id="step_1.2", intent="整合", step_type="synthesis"),
    ]
    _build_run_with_subplan(team, sub_steps)
    parent_state = AgentState(session_id="s", task="root")

    # 早早跳出循环：让第一个 sub_step 抛 AssertionError，stop loop
    # 改用 stub：把 sub_steps 跑空（执行循环退出）
    team.executor.run = AsyncMock(return_value=None)
    team.verifier.check = AsyncMock(side_effect=Exception("stop"))

    try:
        await team.run(parent_step, parent_state)
    except Exception:
        pass

    # 子规划已被回写到 sub_state.plan；从 sub_steps 引用直接验证
    assert sub_steps[0].requires_realtime_data is True   # research → 继承
    assert sub_steps[1].requires_realtime_data is False  # synthesis → 不继承（整合已有数据）


@pytest.mark.asyncio
async def test_sub_steps_keep_when_parent_realtime_false(tmp_path: Path):
    """父无实时性时不强制注入。"""
    team = _make_team(tmp_path)
    parent_step = Step(
        id="step_2", intent="纯写作", requires_realtime_data=False, step_type="generic",
    )
    sub_steps = [
        Step(id="step_2.1", intent="写", requires_realtime_data=False, step_type="generic"),
        Step(id="step_2.2", intent="审", requires_realtime_data=False, step_type="generic"),
    ]
    _build_run_with_subplan(team, sub_steps)
    parent_state = AgentState(session_id="s", task="root")

    team.executor.run = AsyncMock(return_value=None)
    team.verifier.check = AsyncMock(side_effect=Exception("stop"))

    try:
        await team.run(parent_step, parent_state)
    except Exception:
        pass

    # 没有继承，全部保持 False
    assert all(s.requires_realtime_data is False for s in sub_steps)


@pytest.mark.asyncio
async def test_synthesis_sub_step_does_not_inherit_realtime(tmp_path: Path):
    """父 realtime=True 时，step_type=synthesis 的子步骤明确不继承。"""
    team = _make_team(tmp_path)
    parent_step = Step(
        id="step_3", intent="调研 + 整合", requires_realtime_data=True, step_type="research",
    )
    sub_steps = [
        Step(id="step_3.1", intent="整合所有事实", step_type="synthesis"),
    ]
    _build_run_with_subplan(team, sub_steps)
    parent_state = AgentState(session_id="s", task="root")

    team.executor.run = AsyncMock(return_value=None)
    team.verifier.check = AsyncMock(side_effect=Exception("stop"))

    try:
        await team.run(parent_step, parent_state)
    except Exception:
        pass

    assert sub_steps[0].requires_realtime_data is False  # synthesis 排除项
