"""Bug #1 + #2: planner.replan() 严格校验/重试/降级 + synthesis 补注入。

验证三段式 dep-safe 流程：严格校验 → 重试 LLM 1 次 → 降级宽容清理。
所有 LLM 调用 mock，无网络/真实 API。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import yaml

from sunday.agent.models import AgentState, Plan, Step, StepResult, StepStatus, TeamResult
from sunday.agent.planner import Planner
from sunday.templates.loader import TemplateLoader


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_settings(tmp_path):
    from sunday.config import Settings
    config_file = tmp_path / "agent.yaml"
    config_file.write_text(yaml.dump({
        "model": {"provider": "openai", "id": "test-model", "api_key_env": "FAKE_KEY"},
        "reasoning": {"max_steps": 10},
    }))
    with patch.dict(os.environ, {
        "FAKE_KEY": "sk-fake",
        "SUNDAY_CONFIGS_DIR": str(tmp_path),
    }):
        s = Settings()
        _ = s.sunday
        return s


def _load_builtin_templates() -> TemplateLoader:
    project_root = Path(__file__).parent.parent.parent
    loader = TemplateLoader(builtin_dir=project_root / "configs" / "templates")
    loader.discover()
    return loader


def _state_with_completed(passed_step_ids: list[str], plan_steps: list[Step]) -> AgentState:
    state = AgentState(session_id="sid-test", task="test task")
    state.plan = Plan(goal="测试目标", steps=plan_steps, task_type=None)
    state.team_results = [
        TeamResult(step_id=sid, passed=True, output=f"{sid} ok")
        for sid in passed_step_ids
    ]
    return state


def _steps_json(steps: list[dict]) -> str:
    return json.dumps({"steps": steps}, ensure_ascii=False)


# ── Bug #1: 严格校验 → 重试 → 降级 三段式 ────────────────────────────────────

@pytest.mark.asyncio
async def test_replan_happy_path_clean_deps(tmp_path):
    """LLM 首次返回合法 depends_on，不应触发重试。"""
    settings = _make_settings(tmp_path)
    planner = Planner(settings.sunday)

    failed_step = Step(id="step_2", intent="原始 step_2", depends_on=["step_1"])
    plan_steps = [Step(id="step_1", intent="完成"), failed_step]
    state = _state_with_completed(["step_1"], plan_steps)

    good_response = _steps_json([
        {"id": "step_2_v2", "intent": "新方案 v2", "depends_on": ["step_1"]},
        {"id": "step_3_v2", "intent": "新方案 v3", "depends_on": ["step_2_v2"]},
    ])

    mock_call = AsyncMock(return_value=good_response)
    with patch("sunday.agent.planner.LLMClient.call_text", mock_call):
        new_steps = await planner.replan(failed_step, "失败原因", state)

    assert mock_call.await_count == 1, "合法依赖时不应重试 LLM"
    assert len(new_steps) == 2
    assert new_steps[0].id == "step_2_v2"
    assert new_steps[0].depends_on == ["step_1"]
    assert new_steps[1].depends_on == ["step_2_v2"]


@pytest.mark.asyncio
async def test_replan_retry_on_invalid_deps(tmp_path):
    """LLM 首次输出引用已被替换的旧 ID（step_2），第二次返回合法 → 重试成功。"""
    settings = _make_settings(tmp_path)
    planner = Planner(settings.sunday)

    failed_step = Step(id="step_2", intent="原始 step_2", depends_on=["step_1"])
    plan_steps = [Step(id="step_1", intent="完成"), failed_step]
    state = _state_with_completed(["step_1"], plan_steps)

    bad_response = _steps_json([
        {"id": "step_2_alt", "intent": "alt", "depends_on": ["step_2"]},  # 引用已替换的旧 ID
    ])
    good_response = _steps_json([
        {"id": "step_2_alt", "intent": "alt fixed", "depends_on": ["step_1"]},
    ])

    mock_call = AsyncMock(side_effect=[bad_response, good_response])
    with patch("sunday.agent.planner.LLMClient.call_text", mock_call):
        new_steps = await planner.replan(failed_step, "失败原因", state)

    assert mock_call.await_count == 2, "首次非法应触发 1 次重试"
    assert len(new_steps) == 1
    assert new_steps[0].depends_on == ["step_1"]


@pytest.mark.asyncio
async def test_replan_fallback_sanitize_after_retry_fail(tmp_path):
    """LLM 两次均返回非法 deps → 降级清理：非法 deps 被剔除，第一步若空则保留空。"""
    settings = _make_settings(tmp_path)
    planner = Planner(settings.sunday)

    failed_step = Step(id="step_2", intent="原始", depends_on=["step_1"])
    plan_steps = [Step(id="step_1", intent="完成"), failed_step]
    state = _state_with_completed(["step_1"], plan_steps)

    bad1 = _steps_json([
        {"id": "step_2_alt", "intent": "alt", "depends_on": ["ghost_id"]},
        {"id": "step_3", "intent": "next", "depends_on": ["step_2_alt", "another_ghost"]},
    ])
    bad2 = _steps_json([
        {"id": "step_2_alt", "intent": "alt", "depends_on": ["still_bad"]},
        {"id": "step_3", "intent": "next", "depends_on": ["step_2_alt", "more_bad"]},
    ])

    mock_call = AsyncMock(side_effect=[bad1, bad2])
    with patch("sunday.agent.planner.LLMClient.call_text", mock_call):
        new_steps = await planner.replan(failed_step, "失败原因", state)

    assert mock_call.await_count == 2, "重试 1 次后不应再调 LLM"
    # 非法 deps 被剔除，但合法的内部链 step_2_alt 保留
    step_alt = next(s for s in new_steps if s.id == "step_2_alt")
    step_3 = next(s for s in new_steps if s.id == "step_3")
    assert "still_bad" not in step_alt.depends_on
    assert "more_bad" not in step_3.depends_on
    assert "step_2_alt" in step_3.depends_on, "合法内部链应保留"


@pytest.mark.asyncio
async def test_replan_fallback_first_step_remapped_when_orphan(tmp_path):
    """降级后若第一个新步骤 depends_on 清空（且没有任何合法依赖），
    强制重映射到最后一个已完成步骤 ID，保证可执行。"""
    settings = _make_settings(tmp_path)
    planner = Planner(settings.sunday)

    failed_step = Step(id="step_3", intent="原始", depends_on=["step_2"])
    plan_steps = [
        Step(id="step_1", intent="一"),
        Step(id="step_2", intent="二"),
        failed_step,
    ]
    state = _state_with_completed(["step_1", "step_2"], plan_steps)

    bad_response = _steps_json([
        {"id": "step_3_alt", "intent": "孤儿步骤", "depends_on": ["ghost_only"]},
    ])

    mock_call = AsyncMock(return_value=bad_response)
    with patch("sunday.agent.planner.LLMClient.call_text", mock_call):
        new_steps = await planner.replan(failed_step, "失败原因", state)

    assert len(new_steps) == 1
    # ghost_only 被剔除后 deps 空，重映射到最后一个 completed
    assert new_steps[0].depends_on == ["step_2"], (
        f"孤儿首步应被重映射到 completed_ids[-1]，实际：{new_steps[0].depends_on}"
    )


# ── Bug #1: prompt 注入 completed_step_ids ─────────────────────────────────────

@pytest.mark.asyncio
async def test_replan_completed_step_ids_in_prompt(tmp_path):
    """prompt 应包含已完成步骤 ID 清单，让 LLM 能感知合法依赖范围。"""
    settings = _make_settings(tmp_path)
    planner = Planner(settings.sunday)

    failed_step = Step(id="step_2", intent="原始", depends_on=["step_1"])
    plan_steps = [Step(id="step_1", intent="完成"), failed_step]
    state = _state_with_completed(["step_1"], plan_steps)

    good_response = _steps_json([
        {"id": "step_2_v2", "intent": "v2", "depends_on": ["step_1"]},
    ])

    captured_prompts: list[str] = []

    async def fake_call(model_cfg, prompt, **kwargs):
        captured_prompts.append(prompt)
        return good_response

    with patch("sunday.agent.planner.LLMClient.call_text", side_effect=fake_call):
        await planner.replan(failed_step, "失败原因", state)

    assert captured_prompts, "应至少调一次 LLM"
    assert "step_1" in captured_prompts[0], (
        "prompt 应包含已完成步骤 ID 'step_1'，让 LLM 知道合法依赖范围"
    )


# ── Bug #2: synthesis 步骤补注入 ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_replan_preserves_synthesis_when_originally_present(tmp_path):
    """原 plan 末尾有 step_final_synthesis 时，replan 后 new_steps 必须包含 step_type='synthesis' 步骤。"""
    settings = _make_settings(tmp_path)
    planner = Planner(settings.sunday, templates=_load_builtin_templates())

    failed_step = Step(id="step_2", intent="原始", depends_on=["step_1"])
    synthesis = Step(
        id="step_final_synthesis",
        step_type="synthesis",
        intent="综合",
        depends_on=["step_1", "step_2"],
    )
    plan_steps = [Step(id="step_1", intent="一"), failed_step, synthesis]
    state = _state_with_completed(["step_1"], plan_steps)
    # synthesis 注入需要 task_type 与 document_name
    state.plan.task_type = "research"
    state.plan.synthesis_document_name = "测试报告.md"

    # LLM 返回不含 synthesis 的步骤列表
    llm_response = _steps_json([
        {"id": "step_2_v2", "intent": "v2", "depends_on": ["step_1"]},
        {"id": "step_3_v2", "intent": "v3", "depends_on": ["step_2_v2"]},
    ])

    with patch("sunday.agent.planner.LLMClient.call_text", AsyncMock(return_value=llm_response)):
        new_steps = await planner.replan(failed_step, "失败原因", state)

    assert any(s.step_type == "synthesis" for s in new_steps), (
        f"原 plan 含 synthesis 步骤但 replan 后丢失：{[(s.id, s.step_type) for s in new_steps]}"
    )


@pytest.mark.asyncio
async def test_replan_no_synthesis_when_originally_absent(tmp_path):
    """原 plan 没有 step_final_synthesis（如 task_type=None）→ replan 不补注入。"""
    settings = _make_settings(tmp_path)
    planner = Planner(settings.sunday, templates=_load_builtin_templates())

    failed_step = Step(id="step_2", intent="原始", depends_on=["step_1"])
    plan_steps = [Step(id="step_1", intent="一"), failed_step]
    state = _state_with_completed(["step_1"], plan_steps)
    # 没有 synthesis 步骤、没有 task_type

    llm_response = _steps_json([
        {"id": "step_2_v2", "intent": "v2", "depends_on": ["step_1"]},
    ])

    with patch("sunday.agent.planner.LLMClient.call_text", AsyncMock(return_value=llm_response)):
        new_steps = await planner.replan(failed_step, "失败原因", state)

    assert not any(s.step_type == "synthesis" for s in new_steps), (
        "原 plan 无 synthesis 时不应补注入"
    )
