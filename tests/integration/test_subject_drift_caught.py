"""集成：主题漂移的最终输出应被 Verifier 翻转为失败，触发重规划。

复现场景：Turn fa866037 类型的串话 —— 基础 verify LLM 可能只看结构就判 passed=True，
但实际输出的主题与 task 脱钩（最终报告谈"云厂商大赛"而不是"自变量"）。
本集成用例验证 Verifier.check 会额外跑 SubjectConsistencyChecker 兜底，翻转结论。
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import yaml

from sunday.agent.models import AgentState, Plan, Step, StepResult
from sunday.agent.subject_consistency import SubjectCheckResult
from sunday.agent.verifier import Verifier


def _make_settings(tmp_path):
    from sunday.config import Settings
    (tmp_path / "agent.yaml").write_text(yaml.dump({
        "model": {"provider": "anthropic", "id": "claude-test", "max_tokens": 4096},
        "quality": {
            "subject_consistency": {"enabled": True, "checker": "llm"},
        },
    }))
    with patch.dict(os.environ, {
        "ANTHROPIC_API_KEY": "sk-ant-fake",
        "SUNDAY_CONFIGS_DIR": str(tmp_path),
    }):
        s = Settings()
        _ = s.sunday
        return s


async def test_verifier_catches_subject_drift(tmp_path):
    """模拟 fa866037 现场：task 是"自变量"分析，但输出全是"云厂商大赛"内容。"""
    import json

    settings = _make_settings(tmp_path)

    # 模拟"被 L2 污染的最终输出"：几百字全是云厂商大赛的内容
    drift_output = (
        "# 云厂商开发者竞赛分析现状总结\n"
        "根据最新调研，四大云厂商在开发者竞赛方面各有侧重...\n"
    ) * 10

    # 基础 verify LLM 只看结构与字数就判通过（常见的"假通过"情况）
    verify_pass = json.dumps({
        "passed": True,
        "reason": "输出直接回应了问题，提供了清晰的结论、要点",
        "should_replan": False,
    })
    mock_verify_resp = MagicMock()
    mock_verify_resp.json.return_value = {
        "content": [{"type": "text", "text": verify_pass}],
        "stop_reason": "end_turn",
    }
    mock_verify_resp.raise_for_status.return_value = None
    mock_verify_resp.is_success = True
    mc = AsyncMock()
    mc.post = AsyncMock(return_value=mock_verify_resp)

    # 注入一个固定返回"主题不一致"的 checker（模拟一个 LLM 会判断主题漂移）
    drift_detected = SubjectCheckResult(
        consistent=False,
        reason="实际输出聚焦在云厂商开发者竞赛，与自变量公司分析的主题不一致",
    )
    fake_checker = MagicMock()
    fake_checker.check = AsyncMock(return_value=drift_detected)
    verifier = Verifier(settings.sunday, subject_checker=fake_checker)

    step = Step(
        id="step_5",
        intent="整合所有分析，生成最终的综合建议与行动清单",
        success_criteria="建议直接回应原问题，逻辑清晰",
    )
    result = StepResult(step_id="step_5", output=drift_output)
    state = AgentState(
        session_id="sess",
        task="我最近要换工作，现在考虑的是：是否加入自变量团队，是否值得？",
    )
    state.plan = Plan(goal="围绕用户职业决策需求，评估加入自变量", steps=[])

    with patch("sunday.agent.llm_client._get_http_client", return_value=mc):
        vr = await verifier.check(step, result, state)

    # 最关键的断言：主题检查把假通过翻转为失败
    assert vr.passed is False
    assert "主题不一致" in vr.reason
    assert "云厂商" in vr.reason
    assert vr.should_replan is True

    # 证明 SubjectConsistencyChecker 确实被调用
    fake_checker.check.assert_awaited_once()
    args, _ = fake_checker.check.call_args
    output_arg, subjects_arg = args
    # 输出是漂移的
    assert "云厂商" in output_arg
    # subjects 包含任务主题锚点
    assert any("自变量" in s for s in subjects_arg)
