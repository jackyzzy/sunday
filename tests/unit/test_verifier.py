"""T2-4 验证：Verifier 单元测试（mock httpx，无真实 API）"""
from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import yaml

from sunday.agent.models import AgentState, Step, StepResult, StepStatus
from sunday.agent.verifier import Verifier, VerifyResult


def _make_settings(tmp_path, provider="anthropic"):
    from sunday.config import Settings
    config_file = tmp_path / "agent.yaml"
    config_file.write_text(yaml.dump({
        "model": {"provider": provider, "id": "claude-test", "max_tokens": 4096},
    }))
    with patch.dict(os.environ, {
        "ANTHROPIC_API_KEY": "sk-ant-fake",
        "OPENAI_API_KEY": "sk-openai-fake",
        "SUNDAY_CONFIG_FILE": str(config_file),
    }):
        return Settings()


def _mock_client(text: str):
    response_data = {
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
    }
    mock_resp = MagicMock()
    mock_resp.json.return_value = response_data
    mock_resp.raise_for_status.return_value = None
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_resp)
    return mock_client


# ── check ─────────────────────────────────────────────────────────────────────

async def test_check_passed(tmp_path):
    """验证通过时 VerifyResult.passed=True"""
    settings = _make_settings(tmp_path)
    verifier = Verifier(settings.sunday)

    verify_json = json.dumps({"passed": True, "reason": "满足标准", "should_replan": False})
    mock_cl = _mock_client(verify_json)

    step = Step(id="s1", intent="写一首诗", success_criteria="包含 4 行")
    result = StepResult(step_id="s1", output="床前明月光\n疑是地上霜\n举头望明月\n低头思故乡")
    state = AgentState(session_id="sess", task="写诗")

    with (
        patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-fake"}),
        patch("httpx.AsyncClient", return_value=mock_cl),
    ):
        vr = await verifier.check(step, result, state)

    assert vr.passed is True
    assert vr.reason == "满足标准"
    assert vr.should_replan is False


async def test_check_failed_should_replan(tmp_path):
    """验证失败且 should_replan=True"""
    settings = _make_settings(tmp_path)
    verifier = Verifier(settings.sunday)

    verify_json = json.dumps({"passed": False, "reason": "只有2行", "should_replan": True})
    mock_cl = _mock_client(verify_json)

    step = Step(id="s1", intent="写诗", success_criteria="包含 4 行")
    result = StepResult(step_id="s1", output="床前明月光\n疑是地上霜")
    state = AgentState(session_id="sess", task="写诗")

    with (
        patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-fake"}),
        patch("httpx.AsyncClient", return_value=mock_cl),
    ):
        vr = await verifier.check(step, result, state)

    assert vr.passed is False
    assert vr.should_replan is True
    assert "2行" in vr.reason


async def test_check_failed_no_replan(tmp_path):
    """验证失败且 should_replan=False"""
    settings = _make_settings(tmp_path)
    verifier = Verifier(settings.sunday)

    verify_json = json.dumps({"passed": False, "reason": "任务无意义", "should_replan": False})
    mock_cl = _mock_client(verify_json)

    step = Step(id="s1", intent="test", success_criteria="标准")
    result = StepResult(step_id="s1", output="结果")
    state = AgentState(session_id="sess", task="test")

    with (
        patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-fake"}),
        patch("httpx.AsyncClient", return_value=mock_cl),
    ):
        vr = await verifier.check(step, result, state)

    assert vr.passed is False
    assert vr.should_replan is False


async def test_check_no_criteria_passes_without_llm(tmp_path):
    """无成功标准时无需调用 LLM，直接返回通过"""
    settings = _make_settings(tmp_path)
    verifier = Verifier(settings.sunday)

    step = Step(id="s1", intent="test", success_criteria="")
    result = StepResult(step_id="s1", output="任何结果")
    state = AgentState(session_id="sess", task="test")

    # 不 patch httpx，如果调用了就会报错
    vr = await verifier.check(step, result, state)
    assert vr.passed is True


async def test_check_markdown_code_block_response(tmp_path):
    """响应被 markdown 代码块包裹时仍能解析"""
    settings = _make_settings(tmp_path)
    verifier = Verifier(settings.sunday)

    raw_json = json.dumps({"passed": True, "reason": "ok", "should_replan": False})
    wrapped = f"```json\n{raw_json}\n```"
    mock_cl = _mock_client(wrapped)

    step = Step(id="s1", intent="t", success_criteria="需要通过")
    result = StepResult(step_id="s1", output="good")
    state = AgentState(session_id="sess", task="test")

    with (
        patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-fake"}),
        patch("httpx.AsyncClient", return_value=mock_cl),
    ):
        vr = await verifier.check(step, result, state)

    assert vr.passed is True


async def test_check_invalid_json_falls_back_to_passed(tmp_path):
    """LLM 返回非 JSON 时兜底返回 passed=True"""
    settings = _make_settings(tmp_path)
    verifier = Verifier(settings.sunday)

    mock_cl = _mock_client("这不是 JSON")

    step = Step(id="s1", intent="t", success_criteria="标准")
    result = StepResult(step_id="s1", output="结果")
    state = AgentState(session_id="sess", task="test")

    with (
        patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-fake"}),
        patch("httpx.AsyncClient", return_value=mock_cl),
    ):
        vr = await verifier.check(step, result, state)

    assert vr.passed is True  # 兜底行为


# ── summarize ─────────────────────────────────────────────────────────────────

async def test_summarize_returns_str(tmp_path):
    """summarize 返回字符串摘要"""
    settings = _make_settings(tmp_path)
    verifier = Verifier(settings.sunday)

    summary_text = "任务已完成，生成了一首五言绝句。"
    mock_cl = _mock_client(summary_text)

    state = AgentState(session_id="sess", task="写诗")
    state.step_results.append(
        StepResult(step_id="s1", status=StepStatus.DONE, output="诗的内容")
    )

    with (
        patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-fake"}),
        patch("httpx.AsyncClient", return_value=mock_cl),
    ):
        summary = await verifier.summarize(state)

    assert isinstance(summary, str)
    assert len(summary) > 0
    assert summary == summary_text


async def test_summarize_empty_results(tmp_path):
    """无步骤结果时 summarize 不崩溃"""
    settings = _make_settings(tmp_path)
    verifier = Verifier(settings.sunday)

    mock_cl = _mock_client("任务执行记录为空。")
    state = AgentState(session_id="sess", task="test")

    with (
        patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-fake"}),
        patch("httpx.AsyncClient", return_value=mock_cl),
    ):
        summary = await verifier.summarize(state)

    assert isinstance(summary, str)


# ── VerifyResult 模型 ─────────────────────────────────────────────────────────

def test_verify_result_model():
    """VerifyResult 可以正常构造"""
    vr = VerifyResult(passed=True, reason="ok")
    assert vr.should_replan is False

    vr2 = VerifyResult(passed=False, reason="fail", should_replan=True)
    assert vr2.should_replan is True
