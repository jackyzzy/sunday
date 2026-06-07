"""Bug #3 + 端到端编排：ReactAgent verify_reason 选取逻辑 + replan 后无连环 SKIPPED。

- verify_reason 测试：用真实的 react_agent.execute() 但 mock Team.run 返回构造的 TeamResult，
  验证 emit step_result 事件中的 verify_reason 字段语义正确。
- 集成：构造"先失败后 replan 成功"场景，断言不出现 status=skipped & duration_ms=0 的连环条目。
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from sunday.agent.models import (
    AgentState, Plan, Step, StepResult, StepStatus, TeamResult,
)
from sunday.agent.planner import Planner
from sunday.agent.react_agent import ReactAgent
from sunday.agent.verifier import Verifier


# ── 通用工厂（基于 tests/integration/test_agent_loop.py 的 pattern） ─────────────

def _make_settings(tmp_path):
    from sunday.config import Settings
    config_file = tmp_path / "agent.yaml"
    config_file.write_text(yaml.dump({
        "model": {"provider": "anthropic", "id": "claude-test", "max_tokens": 4096},
        "reasoning": {"max_steps": 5, "max_replans": 3},
    }))
    with patch.dict(os.environ, {
        "ANTHROPIC_API_KEY": "sk-ant-fake",
        "SUNDAY_CONFIGS_DIR": str(tmp_path),
    }):
        return Settings()


def _mock_memory_client() -> MagicMock:
    client = MagicMock()
    client.workspace.read_runtime_rules = AsyncMock(return_value=None)
    client.logs.emit = AsyncMock(return_value=None)
    return client


def _make_mock_agent(plan: Plan, evaluate_return: str = "done"):
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
    agent.config.reasoning.max_replans = 3
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


def _team_result_with_sub_steps(
    step_id: str,
    passed: bool,
    sub_steps: list[tuple[str, bool, str]],
) -> TeamResult:
    """sub_steps: [(sub_id, verified, verify_reason), ...]"""
    sub_results = [
        StepResult(
            step_id=sid,
            status=StepStatus.DONE if verified else StepStatus.FAILED,
            output=f"{sid} output",
            verified=verified,
            verify_reason=reason,
        )
        for sid, verified, reason in sub_steps
    ]
    return TeamResult(step_id=step_id, passed=passed, output=f"{step_id} done", sub_steps=sub_results)


# ── Bug #3: verify_reason 语义 ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_verify_reason_clean_on_success_with_prior_failure(tmp_path):
    """sub_steps=[failed, passed], team_result.passed=True
    → 顶层 step_result.verify_reason 不应保留失败 sub-step 的 reason。"""
    _make_settings(tmp_path)
    plan = Plan(goal="g", steps=[Step(id="step_1", intent="一", success_criteria="ok")])
    agent, _, _ = _make_mock_agent(plan)

    emitted: list[tuple[str, dict]] = []

    async def capture(session_id, event_type, data):
        emitted.append((event_type, data))

    agent.emit = capture

    team_result = _team_result_with_sub_steps(
        "step_1", passed=True,
        sub_steps=[
            ("step_1.1", False, "首次失败：来源缺失"),
            ("step_1.R1", True, "已修复，所有来源标注完整"),
        ],
    )
    with patch("sunday.agent.react_agent.Team") as MockTeam:
        MockTeam.return_value.run = AsyncMock(return_value=team_result)
        state = AgentState(session_id="s", task="t")
        await agent.run(state)

    step_results = [d for ev, d in emitted if ev == "step_result"]
    assert len(step_results) == 1
    sr = step_results[0]
    assert sr["verified"] is True
    assert "首次失败" not in (sr["verify_reason"] or ""), (
        f"成功步骤不应携带失败 sub-step 的 reason，实际：{sr['verify_reason']!r}"
    )


@pytest.mark.asyncio
async def test_verify_reason_uses_last_failed_on_failure(tmp_path):
    """sub_steps=[failed, failed], team_result.passed=False
    → verify_reason 取最后失败 sub-step 的 reason。"""
    _make_settings(tmp_path)
    plan = Plan(goal="g", steps=[Step(id="step_1", intent="一", success_criteria="ok")])
    agent, _, _ = _make_mock_agent(plan)
    # 阻止 replan 接管：第一次失败后让 replan 返回空 → 流程继续 emit step_result
    agent.planner.replan = AsyncMock(return_value=[])

    emitted: list[tuple[str, dict]] = []

    async def capture(session_id, event_type, data):
        emitted.append((event_type, data))

    agent.emit = capture

    team_result = _team_result_with_sub_steps(
        "step_1", passed=False,
        sub_steps=[
            ("step_1.1", False, "首次失败原因 A"),
            ("step_1.R1", False, "重规划仍失败原因 B"),
        ],
    )
    # team_result.should_replan=True 默认；这里设 False 避免触发 replan
    team_result.should_replan = False

    with patch("sunday.agent.react_agent.Team") as MockTeam:
        MockTeam.return_value.run = AsyncMock(return_value=team_result)
        state = AgentState(session_id="s", task="t")
        await agent.run(state)

    step_results = [d for ev, d in emitted if ev == "step_result"]
    assert len(step_results) == 1
    sr = step_results[0]
    assert sr["verified"] is False
    assert "重规划仍失败原因 B" in (sr["verify_reason"] or ""), (
        f"失败步骤应取最后失败 sub-step 的 reason，实际：{sr['verify_reason']!r}"
    )


# ── 集成：replan 后无连环 SKIPPED ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_chain_skipped_after_replan(tmp_path):
    """replan 返回的新步骤必须实际执行，不应连环 SKIPPED + duration_ms=0。

    场景：plan=[step_1, step_2]，step_1 通过、step_2 失败 → replan 返回 step_2_new（依赖 step_1）。
    断言：emit 的 step_result 中不存在 status=skipped & duration_ms=0。
    """
    _make_settings(tmp_path)
    plan = Plan(
        goal="g",
        steps=[
            Step(id="step_1", intent="一", success_criteria="ok"),
            Step(id="step_2", intent="二", success_criteria="ok"),
        ],
    )
    agent, planner, _ = _make_mock_agent(plan)
    # replan 返回合法依赖的新步骤
    planner.replan = AsyncMock(return_value=[
        Step(id="step_2_new", intent="新方法", success_criteria="ok", depends_on=["step_1"]),
    ])

    emitted: list[tuple[str, dict]] = []

    async def capture(session_id, event_type, data):
        emitted.append((event_type, data))

    agent.emit = capture

    # Team.run 序列：step_1 pass, step_2 fail (触发 replan), step_2_new pass
    results = [
        TeamResult(step_id="step_1", passed=True, output="ok"),
        TeamResult(step_id="step_2", passed=False, output="fail", should_replan=True),
        TeamResult(step_id="step_2_new", passed=True, output="ok"),
    ]
    with patch("sunday.agent.react_agent.Team") as MockTeam:
        MockTeam.return_value.run = AsyncMock(side_effect=results)
        state = AgentState(session_id="s", task="t")
        await agent.run(state)

    step_results = [d for ev, d in emitted if ev == "step_result"]
    skipped_with_zero = [
        sr for sr in step_results
        if sr.get("status") == "skipped" and sr.get("duration_ms") == 0
    ]
    assert not skipped_with_zero, (
        f"replan 后不应出现连环 SKIPPED+duration_ms=0，实际：{skipped_with_zero}"
    )
    # step_2_new 应该真实执行（duration_ms 不应为 0，因为 mock Team.run 即使瞬间返回也有微小延迟，
    # 但更稳的断言是：它出现且 status=done）
    new_step_results = [sr for sr in step_results if sr["step_id"] == "step_2_new"]
    assert len(new_step_results) == 1
    assert new_step_results[0]["status"] == "done"
    assert new_step_results[0]["verified"] is True


@pytest.mark.asyncio
async def test_replan_with_invalid_deps_still_executes(tmp_path):
    """即使 replan 返回的新步骤依赖了一个已完成 step ID，应正常执行（验收 _deps_satisfied 走通）。"""
    _make_settings(tmp_path)
    plan = Plan(
        goal="g",
        steps=[
            Step(id="step_1", intent="一", success_criteria="ok"),
            Step(id="step_2", intent="二", success_criteria="ok"),
        ],
    )
    agent, planner, _ = _make_mock_agent(plan)
    # 模拟"planner 已通过严格校验保证返回 dep-safe 的步骤"
    planner.replan = AsyncMock(return_value=[
        Step(id="step_2_alt", intent="替代", success_criteria="ok", depends_on=["step_1"]),
        Step(id="step_3_alt", intent="后续", success_criteria="ok", depends_on=["step_2_alt"]),
    ])

    emitted: list[tuple[str, dict]] = []

    async def capture(session_id, event_type, data):
        emitted.append((event_type, data))

    agent.emit = capture

    results = [
        TeamResult(step_id="step_1", passed=True, output="ok"),
        TeamResult(step_id="step_2", passed=False, output="fail", should_replan=True),
        TeamResult(step_id="step_2_alt", passed=True, output="ok"),
        TeamResult(step_id="step_3_alt", passed=True, output="ok"),
    ]
    with patch("sunday.agent.react_agent.Team") as MockTeam:
        MockTeam.return_value.run = AsyncMock(side_effect=results)
        state = AgentState(session_id="s", task="t")
        await agent.run(state)

    step_results = [d for ev, d in emitted if ev == "step_result"]
    executed_ids = [sr["step_id"] for sr in step_results if sr["status"] != "skipped"]
    assert "step_2_alt" in executed_ids, f"step_2_alt 应被执行，emit 列表：{step_results}"
    assert "step_3_alt" in executed_ids, f"step_3_alt 应被执行，emit 列表：{step_results}"
