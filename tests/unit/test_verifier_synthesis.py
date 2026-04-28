"""Verifier 对 synthesis 步骤使用 verify_synthesis.md prompt"""
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import yaml

from sunday.agent.models import AgentState, Step, StepResult, StepStatus
from sunday.agent.verifier import Verifier


def _make_settings(tmp_path):
    from sunday.config import Settings
    config_file = tmp_path / "agent.yaml"
    config_file.write_text(yaml.dump({
        "model": {"provider": "anthropic", "id": "claude-test"},
    }))
    with patch.dict(os.environ, {
        "ANTHROPIC_API_KEY": "sk-ant-fake",
        "SUNDAY_CONFIGS_DIR": str(tmp_path),
    }):
        s = Settings()
        _ = s.sunday  # 触发 cached_property
        return s


def _mock_response(text: str):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
    }
    mock_resp.raise_for_status.return_value = None
    mc = AsyncMock()
    mc.__aenter__ = AsyncMock(return_value=mc)
    mc.__aexit__ = AsyncMock(return_value=False)
    mc.post = AsyncMock(return_value=mock_resp)
    return mc


async def test_verifier_uses_synthesis_prompt_for_synthesis_step(tmp_path):
    """synthesis step_type 触发 verify_synthesis.md 加载，质量不合格触发 replan"""
    settings = _make_settings(tmp_path)
    verifier = Verifier(settings.sunday)

    # 关闭额外的主题/工具审计闸门，以便单独测试 verify_synthesis 路径
    settings.sunday.quality.subject_consistency.enabled = False
    settings.sunday.quality.tool_usage_audit.enabled = False

    step = Step(
        id="step_final_synthesis",
        step_type="synthesis",
        intent="生成综合报告",
        success_criteria="包含对比表 + 推荐理由 + 详细计划",
    )
    result = StepResult(step_id=step.id, status=StepStatus.DONE, output="...输出内容...")
    state = AgentState(session_id="s1", task="规划旅行")

    response = json.dumps({
        "passed": False, "reason": "缺少对比表", "should_replan": True
    })
    mc = _mock_response(response)

    with (
        patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-fake"}),
        patch("sunday.agent.llm_client._get_http_client", return_value=mc),
    ):
        vr = await verifier.check(step, result, state)

    assert vr.passed is False
    assert vr.should_replan is True
    assert "对比表" in vr.reason


async def test_verifier_falls_back_to_default_for_unknown_step_type(tmp_path):
    """未知 step_type 时 fallback 到 verify.md"""
    settings = _make_settings(tmp_path)
    verifier = Verifier(settings.sunday)

    # 关闭额外闸门
    settings.sunday.quality.subject_consistency.enabled = False
    settings.sunday.quality.tool_usage_audit.enabled = False

    step = Step(
        id="step_1",
        step_type="unknown_xyz",
        intent="某步骤",
        success_criteria="完成",
    )
    result = StepResult(step_id=step.id, status=StepStatus.DONE, output="OK")
    state = AgentState(session_id="s1", task="test")

    response = json.dumps({"passed": True, "reason": "ok", "should_replan": False})
    mc = _mock_response(response)

    with (
        patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-fake"}),
        patch("sunday.agent.llm_client._get_http_client", return_value=mc),
    ):
        vr = await verifier.check(step, result, state)

    assert vr.passed is True


async def test_verifier_uses_default_when_step_type_is_none(tmp_path):
    """step_type=None 时使用默认 verify.md"""
    settings = _make_settings(tmp_path)
    verifier = Verifier(settings.sunday)

    settings.sunday.quality.subject_consistency.enabled = False
    settings.sunday.quality.tool_usage_audit.enabled = False

    step = Step(id="step_1", intent="某步骤", success_criteria="完成")  # step_type=None
    result = StepResult(step_id=step.id, status=StepStatus.DONE, output="OK")
    state = AgentState(session_id="s1", task="test")

    response = json.dumps({"passed": True, "reason": "ok", "should_replan": False})
    mc = _mock_response(response)

    with (
        patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-fake"}),
        patch("sunday.agent.llm_client._get_http_client", return_value=mc),
    ):
        vr = await verifier.check(step, result, state)

    assert vr.passed is True
