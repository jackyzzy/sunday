"""Verifier 主题一致性集成单元测试：验证 Verifier.check 会把 SubjectConsistencyChecker
织入流程，并在主题漂移时把 passed=True 的基础判定翻转为 passed=False。
"""
from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import yaml

from sunday.agent.models import AgentState, Plan, SessionThread, Step, StepResult
from sunday.agent.subject_consistency import SubjectCheckResult
from sunday.agent.verifier import Verifier


def _make_settings(tmp_path, quality: dict | None = None):
    from sunday.config import Settings
    payload = {
        "model": {"provider": "anthropic", "id": "claude-test", "max_tokens": 4096},
    }
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


def _verify_pass_client():
    """返回一个 mock httpx client，让基础 verify LLM 始终判 passed=True。"""
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "content": [{"type": "text", "text": json.dumps(
            {"passed": True, "reason": "结构满足", "should_replan": False}
        )}],
        "stop_reason": "end_turn",
    }
    mock_resp.raise_for_status.return_value = None
    mock_resp.is_success = True
    mc = AsyncMock()
    mc.post = AsyncMock(return_value=mock_resp)
    return mc


async def test_check_long_output_subject_drift_flips_to_failed(tmp_path):
    """输出 ≥200 字且主题检查返回不一致时，整体 passed 翻转为 False。"""
    settings = _make_settings(tmp_path)

    drift = SubjectCheckResult(consistent=False, reason="输出转向云厂商大赛")
    fake_checker = MagicMock()
    fake_checker.check = AsyncMock(return_value=drift)
    verifier = Verifier(settings.sunday, subject_checker=fake_checker)

    step = Step(id="s1", intent="输出最终建议", success_criteria="建议可执行")
    # output 长度 > 200，触发主题检查
    result = StepResult(step_id="s1", output="a" * 600)
    state = AgentState(
        session_id="sess", task="帮我分析一下，自变量这家公司怎么样，是否值得进入？"
    )
    state.plan = Plan(goal="围绕自变量的公司分析", steps=[])

    with patch("sunday.agent.llm_client._get_http_client", return_value=_verify_pass_client()):
        vr = await verifier.check(step, result, state)

    assert vr.passed is False
    assert "主题不一致" in vr.reason
    assert "云厂商" in vr.reason
    assert vr.should_replan is True
    fake_checker.check.assert_awaited_once()
    # 检查传入的 subjects 包含 task 与 goal
    args, _ = fake_checker.check.call_args
    output_arg, subjects_arg = args
    assert any("自变量" in s for s in subjects_arg)


async def test_check_long_output_subject_consistent_passes(tmp_path):
    """输出 ≥200 字且主题检查一致时，整体保持 passed=True。"""
    settings = _make_settings(tmp_path)

    ok = SubjectCheckResult(consistent=True, reason="主题匹配")
    fake_checker = MagicMock()
    fake_checker.check = AsyncMock(return_value=ok)
    verifier = Verifier(settings.sunday, subject_checker=fake_checker)

    step = Step(id="s1", intent="输出最终建议", success_criteria="建议可执行")
    result = StepResult(step_id="s1", output="围绕自变量的分析..." * 30)
    state = AgentState(session_id="sess", task="分析自变量")

    with patch("sunday.agent.llm_client._get_http_client", return_value=_verify_pass_client()):
        vr = await verifier.check(step, result, state)

    assert vr.passed is True


async def test_check_short_output_skips_subject_check(tmp_path):
    """输出 <200 字时不触发主题检查，避免对简短中间输出做无谓调用。"""
    settings = _make_settings(tmp_path)

    fake_checker = MagicMock()
    fake_checker.check = AsyncMock(
        return_value=SubjectCheckResult(consistent=False, reason="不该被调用")
    )
    verifier = Verifier(settings.sunday, subject_checker=fake_checker)

    step = Step(id="s1", intent="写首诗", success_criteria="4 行")
    result = StepResult(step_id="s1", output="短内容")  # 极短，绕过主题检查
    state = AgentState(session_id="sess", task="写诗")

    with patch("sunday.agent.llm_client._get_http_client", return_value=_verify_pass_client()):
        vr = await verifier.check(step, result, state)

    assert vr.passed is True
    fake_checker.check.assert_not_called()


async def test_check_failed_base_verify_skips_subject_check(tmp_path):
    """基础 verify 已判 failed 时，不再调用主题检查（避免冗余）。"""
    settings = _make_settings(tmp_path)

    fake_checker = MagicMock()
    fake_checker.check = AsyncMock()
    verifier = Verifier(settings.sunday, subject_checker=fake_checker)

    # verify LLM 返回 passed=False
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "content": [{"type": "text", "text": json.dumps(
            {"passed": False, "reason": "不满足", "should_replan": True}
        )}],
        "stop_reason": "end_turn",
    }
    mock_resp.raise_for_status.return_value = None
    mock_resp.is_success = True
    mc = AsyncMock()
    mc.post = AsyncMock(return_value=mock_resp)

    step = Step(id="s1", intent="任务", success_criteria="标准")
    result = StepResult(step_id="s1", output="x" * 500)
    state = AgentState(session_id="sess", task="测试")

    with patch("sunday.agent.llm_client._get_http_client", return_value=mc):
        vr = await verifier.check(step, result, state)

    assert vr.passed is False
    fake_checker.check.assert_not_called()


async def test_check_no_subjects_skips_subject_check(tmp_path):
    """无法从 state 提取主题（task 为空）时跳过主题检查。"""
    settings = _make_settings(tmp_path)

    fake_checker = MagicMock()
    fake_checker.check = AsyncMock()
    verifier = Verifier(settings.sunday, subject_checker=fake_checker)

    step = Step(id="s1", intent="任务", success_criteria="标准")
    result = StepResult(step_id="s1", output="x" * 500)
    state = AgentState(session_id="sess", task="")  # task 为空

    with patch("sunday.agent.llm_client._get_http_client", return_value=_verify_pass_client()):
        vr = await verifier.check(step, result, state)

    assert vr.passed is True
    fake_checker.check.assert_not_called()


async def test_check_subjects_include_thread_entities(tmp_path):
    """session_thread.key_entities 被纳入主题列表。"""
    settings = _make_settings(tmp_path)

    ok = SubjectCheckResult(consistent=True, reason="ok")
    fake_checker = MagicMock()
    fake_checker.check = AsyncMock(return_value=ok)
    verifier = Verifier(settings.sunday, subject_checker=fake_checker)

    step = Step(id="s1", intent="整合输出", success_criteria="xxx")
    result = StepResult(step_id="s1", output="y" * 400)
    state = AgentState(session_id="sess", task="分析某主题")
    state.session_thread = SessionThread(
        summary="关于自变量的分析",
        key_entities=["自变量", "具身智能"],
    )

    with patch("sunday.agent.llm_client._get_http_client", return_value=_verify_pass_client()):
        await verifier.check(step, result, state)

    args, _ = fake_checker.check.call_args
    _, subjects_arg = args
    assert "自变量" in subjects_arg
    assert "具身智能" in subjects_arg


async def test_verifier_default_checker_from_factory(tmp_path):
    """未显式注入 checker 时，构造函数应通过工厂根据 config 生成一个。"""
    settings = _make_settings(tmp_path, quality={
        "subject_consistency": {"enabled": False, "checker": "llm"}
    })
    verifier = Verifier(settings.sunday)  # 不注入

    from sunday.agent.subject_consistency import _AlwaysConsistentChecker
    assert isinstance(verifier._subject_checker, _AlwaysConsistentChecker)
