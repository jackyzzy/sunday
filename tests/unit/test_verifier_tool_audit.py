"""Verifier 接入 ToolUsageAuditChecker 的集成测试。"""
from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import yaml

from sunday.agent.models import (
    AgentState,
    ReactIteration,
    Step,
    StepResult,
)
from sunday.agent.tool_usage_audit import ToolAuditResult
from sunday.agent.verifier import Verifier


def _make_settings(tmp_path, quality: dict | None = None):
    from sunday.config import Settings
    payload = {"model": {"provider": "anthropic", "id": "claude-test", "max_tokens": 4096}}
    if quality is not None:
        payload["quality"] = quality
    (tmp_path / "agent.yaml").write_text(yaml.dump(payload))
    with patch.dict(os.environ, {
        "ANTHROPIC_API_KEY": "sk-ant-fake",
        "SUNDAY_CONFIGS_DIR": str(tmp_path),
    }):
        s = Settings()
        _ = s.sunday
        return s


def _verify_ok_resp() -> MagicMock:
    """mock 一个 LLM 响应，返回 verify passed=true。"""
    body = {"passed": True, "reason": "看起来 OK", "should_replan": False}
    m = MagicMock()
    m.json.return_value = {"content": [{"type": "text", "text": json.dumps(body)}],
                            "stop_reason": "end_turn"}
    m.raise_for_status.return_value = None
    m.is_success = True
    return m


# 关闭 subject_consistency 与 audit 的实际调用，测试聚焦 audit 闸门
async def test_audit_failure_flips_passed_to_false(tmp_path):
    """basic verify 通过 + audit 失败 → 整体 passed=False, should_replan=True。"""
    settings = _make_settings(tmp_path)

    fake_subject = MagicMock()
    fake_subject.check = AsyncMock(return_value=MagicMock(consistent=True))

    fake_audit = MagicMock()
    fake_audit.check = AsyncMock(return_value=ToolAuditResult(
        passed=False, reason="该步骤要求实时数据但未成功调联网工具",
    ))
    verifier = Verifier(
        settings.sunday, subject_checker=fake_subject, tool_usage_auditor=fake_audit,
    )

    step = Step(id="s", intent="调研", requires_realtime_data=True,
                success_criteria="包含核心信息")
    output = "摩尔线程上市不确定" * 30  # 200+ chars
    result = StepResult(step_id="s", output=output)
    state = AgentState(session_id="sess", task="调研摩尔线程")

    mc = AsyncMock()
    mc.post = AsyncMock(return_value=_verify_ok_resp())

    with patch("sunday.agent.llm_client._get_http_client", return_value=mc):
        vr = await verifier.check(step, result, state)

    assert vr.passed is False
    assert "工具使用审计失败" in vr.reason
    assert vr.should_replan is True
    fake_audit.check.assert_awaited_once()


async def test_audit_pass_keeps_basic_passed(tmp_path):
    """basic verify 通过 + audit 通过 → 整体 passed=True。"""
    settings = _make_settings(tmp_path)

    fake_subject = MagicMock()
    fake_subject.check = AsyncMock(return_value=MagicMock(consistent=True))

    fake_audit = MagicMock()
    fake_audit.check = AsyncMock(return_value=ToolAuditResult(
        passed=True, reason="联网工具已成功调用",
    ))
    verifier = Verifier(
        settings.sunday, subject_checker=fake_subject, tool_usage_auditor=fake_audit,
    )

    step = Step(id="s", intent="调研", requires_realtime_data=True,
                success_criteria="包含核心信息")
    iters = [ReactIteration(iteration=0, tool_name="web_search",
                             tool_input={}, observation="结果")]
    result = StepResult(step_id="s", output="联网获取的内容" * 20,
                        react_iterations=iters)
    state = AgentState(session_id="sess", task="调研")

    mc = AsyncMock()
    mc.post = AsyncMock(return_value=_verify_ok_resp())

    with patch("sunday.agent.llm_client._get_http_client", return_value=mc):
        vr = await verifier.check(step, result, state)

    assert vr.passed is True


async def test_audit_skipped_when_basic_already_failed(tmp_path):
    """若 basic verify 已经判 failed，audit 不应被调用。"""
    settings = _make_settings(tmp_path)

    fake_subject = MagicMock()
    fake_audit = MagicMock()
    fake_audit.check = AsyncMock()
    verifier = Verifier(
        settings.sunday, subject_checker=fake_subject, tool_usage_auditor=fake_audit,
    )

    step = Step(id="s", intent="x", success_criteria="必须包含 X")

    fail_body = {"passed": False, "reason": "缺少 X", "should_replan": False}
    fail_resp = MagicMock()
    fail_resp.json.return_value = {
        "content": [{"type": "text", "text": json.dumps(fail_body)}],
        "stop_reason": "end_turn",
    }
    fail_resp.raise_for_status.return_value = None
    fail_resp.is_success = True
    mc = AsyncMock()
    mc.post = AsyncMock(return_value=fail_resp)

    state = AgentState(session_id="sess", task="t")
    result = StepResult(step_id="s", output="缺少 X 的输出")

    with patch("sunday.agent.llm_client._get_http_client", return_value=mc):
        vr = await verifier.check(step, result, state)

    assert vr.passed is False
    fake_audit.check.assert_not_awaited()


async def test_no_success_criteria_skips_full_pipeline(tmp_path):
    """空 success_criteria 时连基础 verify 都跳过，更别说 audit。"""
    settings = _make_settings(tmp_path)

    fake_audit = MagicMock()
    fake_audit.check = AsyncMock()
    verifier = Verifier(settings.sunday, tool_usage_auditor=fake_audit)

    step = Step(id="s", intent="x", success_criteria="")
    state = AgentState(session_id="sess", task="t")
    result = StepResult(step_id="s", output="任意")

    vr = await verifier.check(step, result, state)
    assert vr.passed is True
    fake_audit.check.assert_not_awaited()


async def test_default_factory_used_when_no_auditor_injected(tmp_path):
    """未注入 auditor 时使用 build_tool_usage_auditor，realtime + 无标签 → 失败。"""
    settings = _make_settings(tmp_path)

    fake_subject = MagicMock()
    fake_subject.check = AsyncMock(return_value=MagicMock(consistent=True))
    verifier = Verifier(settings.sunday, subject_checker=fake_subject)

    step = Step(id="s", intent="调研", requires_realtime_data=True,
                success_criteria="包含核心信息")
    result = StepResult(step_id="s", output="x" * 250)  # >200 chars, 无联网, 无标签
    state = AgentState(session_id="sess", task="t")

    mc = AsyncMock()
    mc.post = AsyncMock(return_value=_verify_ok_resp())

    with patch("sunday.agent.llm_client._get_http_client", return_value=mc):
        vr = await verifier.check(step, result, state)

    assert vr.passed is False
    assert "工具使用审计失败" in vr.reason
