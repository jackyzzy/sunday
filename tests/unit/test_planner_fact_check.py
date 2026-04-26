"""Planner 的 FACT_CHECK 子阶段集成测试（mock httpx + mock tool_registry）。"""
from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import yaml

from sunday.agent.models import AgentState, ThinkingLevel
from sunday.agent.planner import Planner


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


def _mk_response(text: str) -> dict:
    return {
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
    }


def _make_client(responses: list[dict]):
    """每次 post 返回一个不同 response。"""
    responses_iter = iter(responses)

    def _resp(*_args, **_kwargs):
        r = next(responses_iter)
        m = MagicMock()
        m.json.return_value = r
        m.raise_for_status.return_value = None
        m.is_success = True
        return m

    mc = AsyncMock()
    mc.post = AsyncMock(side_effect=_resp)
    return mc


def _mock_registry(execute_return: str = "搜索事实"):
    reg = MagicMock()
    reg.get_schemas = MagicMock(
        return_value=[{"name": "web_search", "description": "", "input_schema": {}}]
    )
    reg.execute = AsyncMock(return_value=execute_return)
    return reg


# ── FACT_CHECK 触发与注入 ─────────────────────────────────────────────────────

async def test_fact_check_triggers_when_claims_found(tmp_path):
    """think 返回 needs_fact_check=true + 非空 claims → 调用工具并把事实注入 plan prompt。"""
    settings = _make_settings(tmp_path, quality={"fact_check": {"enabled": True}})
    planner = Planner(settings.sunday)

    think_json = json.dumps({
        "needs_fact_check": True,
        "claims": ["自变量 是 什么行业的公司"],
    })
    plan_json = json.dumps({
        "goal": "围绕自变量（具身智能公司）做分析",
        "steps": [{
            "id": "s1", "intent": "做事",
            "expected_input": "", "expected_output": "",
            "success_criteria": "", "depends_on": [],
        }],
    })
    # planner 会依次调用：think（text 模式）→ plan（tools 模式）
    mc = _make_client([_mk_response(think_json), _mk_response(plan_json)])

    reg = _mock_registry(execute_return="自变量是一家具身智能公司")
    emit_calls: list[tuple] = []

    async def capture_emit(sid, event, data):
        emit_calls.append((sid, event, data))

    state = AgentState(session_id="s1", task="分析一下自变量这家公司",
                       thinking_level=ThinkingLevel.OFF)

    with patch("sunday.agent.llm_client._get_http_client", return_value=mc):
        plan = await planner.think_and_plan(state, tool_registry=reg, emit=capture_emit)

    assert plan.goal == "围绕自变量（具身智能公司）做分析"
    # 工具被调用
    reg.execute.assert_awaited_once()
    # 两次 emit：start + done
    events = [c for c in emit_calls if c[1] == "plan_fact_check"]
    assert len(events) == 2
    assert events[0][2]["phase"] == "start"
    assert events[1][2]["phase"] == "done"
    assert events[1][2]["facts"][0]["tool"] == "web_search"
    # plan LLM 的 body 中应包含 "已核实事实"
    plan_call_body = mc.post.call_args_list[1].kwargs["json"]
    plan_user_msg = plan_call_body["messages"][0]["content"]
    assert "已核实事实" in plan_user_msg
    assert "具身智能" in plan_user_msg


async def test_fact_check_skipped_when_needs_false(tmp_path):
    """think 返回 needs_fact_check=false → 不调用工具。"""
    settings = _make_settings(tmp_path)
    planner = Planner(settings.sunday)

    think_json = json.dumps({"needs_fact_check": False, "claims": []})
    plan_json = json.dumps({
        "goal": "目标",
        "steps": [{"id": "s1", "intent": "x",
                   "expected_input": "", "expected_output": "",
                   "success_criteria": "", "depends_on": []}],
    })
    mc = _make_client([_mk_response(think_json), _mk_response(plan_json)])
    reg = _mock_registry()

    state = AgentState(session_id="s1", task="写一首诗", thinking_level=ThinkingLevel.OFF)

    with patch("sunday.agent.llm_client._get_http_client", return_value=mc):
        await planner.think_and_plan(state, tool_registry=reg)

    reg.execute.assert_not_called()


async def test_fact_check_and_realtime_hints_disabled_skips_think(tmp_path):
    """fact_check 和 realtime_hints 都关时，think 阶段被跳过（仅 1 次 LLM 调用）。

    Cycle B 后 think 阶段被 fact_check 和 realtime_hints 共同消费；要完全跳过
    think 需要把两个开关都关掉。
    """
    settings = _make_settings(tmp_path, quality={
        "fact_check": {"enabled": False},
        "realtime_hints": {"enabled": False},
    })
    planner = Planner(settings.sunday)

    plan_json = json.dumps({
        "goal": "目标",
        "steps": [{"id": "s1", "intent": "x",
                   "expected_input": "", "expected_output": "",
                   "success_criteria": "", "depends_on": []}],
    })
    # 只需要一次响应（think 被跳过）
    mc = _make_client([_mk_response(plan_json)])
    reg = _mock_registry()

    state = AgentState(session_id="s1", task="任意", thinking_level=ThinkingLevel.OFF)

    with patch("sunday.agent.llm_client._get_http_client", return_value=mc):
        await planner.think_and_plan(state, tool_registry=reg)

    # 只有 plan 那次调用，没有 think
    assert mc.post.call_count == 1
    reg.execute.assert_not_called()


async def test_fact_check_off_but_realtime_hints_on_still_runs_think(tmp_path):
    """默认配置：fact_check 关 + realtime_hints 开 → think 仍跑（共享 claims 给 hints）。

    验证 think 阶段已正确解耦，不再嵌套在 fact_check 里。
    """
    settings = _make_settings(tmp_path, quality={
        "fact_check": {"enabled": False},
        "realtime_hints": {"enabled": True},
    })
    planner = Planner(settings.sunday)

    think_json = json.dumps({"needs_fact_check": True, "claims": ["xxx 公司 状态"]})
    plan_json = json.dumps({
        "goal": "目标",
        "steps": [{"id": "s1", "intent": "x",
                   "expected_input": "", "expected_output": "",
                   "success_criteria": "", "depends_on": []}],
    })
    mc = _make_client([_mk_response(think_json), _mk_response(plan_json)])
    reg = _mock_registry()

    state = AgentState(session_id="s1", task="调研最新动态",
                       thinking_level=ThinkingLevel.OFF)

    with patch("sunday.agent.llm_client._get_http_client", return_value=mc):
        await planner.think_and_plan(state, tool_registry=reg)

    # think + plan 两次 LLM；reg.execute 不被调用（fact_check 关）
    assert mc.post.call_count == 2
    reg.execute.assert_not_called()


async def test_fact_check_think_invalid_json_is_silent(tmp_path):
    """think 返回非 JSON 时退化到无 fact_check，不抛出。"""
    settings = _make_settings(tmp_path, quality={"fact_check": {"enabled": True}})
    planner = Planner(settings.sunday)

    plan_json = json.dumps({
        "goal": "目标",
        "steps": [{"id": "s1", "intent": "x",
                   "expected_input": "", "expected_output": "",
                   "success_criteria": "", "depends_on": []}],
    })
    mc = _make_client([_mk_response("这不是 JSON"), _mk_response(plan_json)])
    reg = _mock_registry()

    state = AgentState(session_id="s1", task="任务", thinking_level=ThinkingLevel.OFF)

    with patch("sunday.agent.llm_client._get_http_client", return_value=mc):
        plan = await planner.think_and_plan(state, tool_registry=reg)

    assert plan.goal == "目标"
    reg.execute.assert_not_called()


async def test_fact_check_claims_truncated_to_three(tmp_path):
    """think 返回超过 3 条 claims 时只保留前 3 条。"""
    settings = _make_settings(tmp_path)
    planner = Planner(settings.sunday)

    # 构造想要验证 _identify_uncertain_claims 的截断逻辑
    think_json = json.dumps({
        "needs_fact_check": True,
        "claims": ["c1", "c2", "c3", "c4", "c5"],
    })
    mc = _make_client([_mk_response(think_json)])

    state = AgentState(session_id="s1", task="任务", thinking_level=ThinkingLevel.OFF)
    with patch("sunday.agent.llm_client._get_http_client", return_value=mc):
        claims = await planner._identify_uncertain_claims(
            state, "", "", settings.sunday.model
        )
    assert claims == ["c1", "c2", "c3"]
