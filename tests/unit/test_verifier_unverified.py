"""Verifier fail-open → 显式「未验证」降级 的单元测试。

覆盖：
- LLM 调用失败 → passed=True, unverified=True, reason 含 ⚠未验证
- 响应非 JSON（解析失败）→ passed=True, unverified=True
- apply_unverified_label：幂等 + config 开关
- SimpleNode：unverified 时打标签 + emit verify_unavailable
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import yaml

from sunday.agent.models import AgentState, Step, StepResult
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


def _resp(text: str) -> MagicMock:
    m = MagicMock()
    m.json.return_value = {"content": [{"type": "text", "text": text}], "stop_reason": "end_turn"}
    m.raise_for_status.return_value = None
    m.is_success = True
    return m


async def test_llm_failure_degrades_to_unverified(tmp_path):
    """验证 LLM 调用抛异常 → passed=True 但 unverified=True，reason 醒目。"""
    settings = _make_settings(tmp_path)
    verifier = Verifier(settings.sunday)
    step = Step(id="s", intent="x", success_criteria="必须包含 X")
    result = StepResult(step_id="s", output="some output")
    state = AgentState(session_id="sess", task="t")

    with patch.object(verifier, "_call_llm", AsyncMock(side_effect=RuntimeError("no key"))):
        vr = await verifier.check(step, result, state)

    assert vr.passed is True
    assert vr.unverified is True
    assert vr.reason.startswith("⚠ 未验证")
    assert "no key" in vr.reason


async def test_non_json_response_degrades_to_unverified(tmp_path):
    """LLM 返回非 JSON → 解析失败 → unverified 降级（不空转重规划）。"""
    settings = _make_settings(tmp_path)
    verifier = Verifier(settings.sunday)
    step = Step(id="s", intent="x", success_criteria="必须包含 X")
    result = StepResult(step_id="s", output="some output")
    state = AgentState(session_id="sess", task="t")

    mc = AsyncMock()
    mc.post = AsyncMock(return_value=_resp("这不是 JSON，只是闲聊"))
    with patch("sunday.agent.llm_client._get_http_client", return_value=mc):
        vr = await verifier.check(step, result, state)

    assert vr.passed is True
    assert vr.unverified is True
    assert vr.reason.startswith("⚠ 未验证")
    assert vr.should_replan is False


def test_apply_unverified_label_prepends_and_idempotent(tmp_path):
    """标签 prepend，且二次调用不重复打。"""
    settings = _make_settings(tmp_path)
    verifier = Verifier(settings.sunday)
    out1 = verifier.apply_unverified_label("正文内容")
    assert out1.startswith("> ⚠ 未验证")
    assert "正文内容" in out1
    out2 = verifier.apply_unverified_label(out1)
    assert out2 == out1  # 幂等


def test_apply_unverified_label_respects_config_off(tmp_path):
    """config 关闭时不打标签。"""
    settings = _make_settings(tmp_path, quality={"unverified_output_label": {"enabled": False}})
    verifier = Verifier(settings.sunday)
    out = verifier.apply_unverified_label("正文内容")
    assert out == "正文内容"


async def test_simple_node_labels_and_emits_on_unverified(tmp_path):
    """SimpleNode：verify 未验证 → 输出打标签 + emit verify_unavailable。"""
    from sunday.agent.simple import SimpleNode

    settings = _make_settings(tmp_path)
    emitted = []

    async def _emit(sid, et, data):
        emitted.append((et, data))

    node = SimpleNode(settings.sunday, tool_registry=MagicMock(), emit=_emit)
    # executor 产出普通结果；verifier 降级为未验证
    node.executor.run = AsyncMock(
        return_value=StepResult(step_id="s", output="执行结果")
    )
    from sunday.agent.verifier import VerifyResult
    node.verifier.check = AsyncMock(
        return_value=VerifyResult(
            passed=True, unverified=True, reason="⚠ 未验证（验证服务调用失败：x）",
        )
    )

    step = Step(id="s", intent="x", success_criteria="必须包含 X")
    state = AgentState(session_id="sess", task="t")
    tr = await node.run(step, state)

    assert tr.output.startswith("> ⚠ 未验证")
    events = [et for et, _ in emitted]
    assert "verify_unavailable" in events
