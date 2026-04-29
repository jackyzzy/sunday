"""S3-A 验证：Verifier.check() 闸门短路 —— 前一闸门失败时跳过后续 LLM 调用。

三层闸门顺序：基础 verify → 主题一致性 → 工具使用审计。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from sunday.agent.models import AgentState, Step, StepResult, StepStatus
from sunday.agent.subject_consistency import SubjectCheckResult
from sunday.agent.tool_usage_audit import ToolAuditResult
from sunday.agent.verifier import Verifier


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


def _build_verifier_with_mocks(settings, basic_passed: bool):
    """构造 Verifier，subject_checker / tool_usage_auditor 各自 AsyncMock，
    并 mock LLM 返回 basic_passed 决定的 verify 结果。"""
    subject_checker = MagicMock()
    subject_checker.check = AsyncMock(
        return_value=SubjectCheckResult(consistent=True, reason="ok"),
    )
    tool_auditor = MagicMock()
    tool_auditor.check = AsyncMock(
        return_value=ToolAuditResult(passed=True, reason="ok"),
    )
    v = Verifier(
        settings.sunday,
        subject_checker=subject_checker,
        tool_usage_auditor=tool_auditor,
    )
    return v, subject_checker, tool_auditor


@pytest.mark.asyncio
async def test_basic_fail_short_circuits_subsequent_gates(tmp_path: Path):
    """基础 verify fail → subject_consistency 与 tool_usage_audit 都不被调用。"""
    settings = _make_settings(tmp_path)
    verifier, subject_checker, tool_auditor = _build_verifier_with_mocks(
        settings, basic_passed=False,
    )

    step = Step(id="s1", intent="x", success_criteria="必须满足 X")
    # 输出足够长，否则主题检查本来就被长度门槛跳过；这里要确保它的"未被调用"是来自短路而非长度门槛
    result = StepResult(step_id=step.id, status=StepStatus.DONE, output="A" * 500)
    state = AgentState(session_id="s", task="测试任务")

    fake_resp = json.dumps({
        "passed": False, "reason": "missing X", "should_replan": True,
    })
    response_obj = MagicMock(text=fake_resp)
    with patch(
        "sunday.agent.llm_client.LLMClient.call",
        new=AsyncMock(return_value=response_obj),
    ):
        vr = await verifier.check(step, result, state)

    assert vr.passed is False
    assert "missing X" in vr.reason
    # 短路：后续两个闸门均未触发
    subject_checker.check.assert_not_called()
    tool_auditor.check.assert_not_called()


@pytest.mark.asyncio
async def test_subject_fail_short_circuits_audit(tmp_path: Path):
    """基础 verify pass + 主题不一致 → tool_usage_audit 不被调用。"""
    settings = _make_settings(tmp_path)
    verifier, subject_checker, tool_auditor = _build_verifier_with_mocks(
        settings, basic_passed=True,
    )
    # 主题检查返回不一致
    subject_checker.check = AsyncMock(
        return_value=SubjectCheckResult(consistent=False, reason="主题跑偏"),
    )

    step = Step(id="s2", intent="x", success_criteria="必须 X")
    result = StepResult(step_id=step.id, status=StepStatus.DONE, output="A" * 500)
    state = AgentState(session_id="s", task="测试任务")

    fake_resp = json.dumps({"passed": True, "reason": "ok", "should_replan": False})
    response_obj = MagicMock(text=fake_resp)
    with patch(
        "sunday.agent.llm_client.LLMClient.call",
        new=AsyncMock(return_value=response_obj),
    ):
        vr = await verifier.check(step, result, state)

    assert vr.passed is False
    assert "主题不一致" in vr.reason
    # 短路：tool_usage_audit 未触发
    subject_checker.check.assert_called_once()
    tool_auditor.check.assert_not_called()


@pytest.mark.asyncio
async def test_all_gates_run_when_passing(tmp_path: Path):
    """全 pass 路径上三个闸门都执行。"""
    settings = _make_settings(tmp_path)
    verifier, subject_checker, tool_auditor = _build_verifier_with_mocks(
        settings, basic_passed=True,
    )

    step = Step(id="s3", intent="x", success_criteria="X")
    result = StepResult(step_id=step.id, status=StepStatus.DONE, output="A" * 500)
    state = AgentState(session_id="s", task="测试任务")

    fake_resp = json.dumps({"passed": True, "reason": "ok", "should_replan": False})
    response_obj = MagicMock(text=fake_resp)
    with patch(
        "sunday.agent.llm_client.LLMClient.call",
        new=AsyncMock(return_value=response_obj),
    ):
        vr = await verifier.check(step, result, state)

    assert vr.passed is True
    subject_checker.check.assert_called_once()
    tool_auditor.check.assert_called_once()
