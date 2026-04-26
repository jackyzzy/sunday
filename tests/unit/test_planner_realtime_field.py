"""Planner 集成 realtime_hints + step.requires_realtime_data 字段单元测试。

验证三件事：
1. plan prompt 里被注入了 "# 实时数据信号" 节（mock LLM 请求体可检查）
2. plan_realtime_hints emit 在有信号时被触发
3. LLM 返回带 requires_realtime_data=true 的 step → 解析后字段正确
"""
from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import yaml

from sunday.agent.models import AgentState, ThinkingLevel
from sunday.agent.planner import Planner


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


def _resp(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "stop_reason": "end_turn"}


def _make_client(responses: list[dict]):
    it = iter(responses)

    def _make():
        r = next(it)
        m = MagicMock()
        m.json.return_value = r
        m.raise_for_status.return_value = None
        m.is_success = True
        return m

    mc = AsyncMock()
    mc.post = AsyncMock(side_effect=lambda *a, **kw: _make())
    return mc


# ── plan prompt 注入 hints ───────────────────────────────────────────────

async def test_hints_section_injected_into_plan_prompt(tmp_path):
    """task 含关键词 + claims 含实体 → plan prompt 含 hints 节。"""
    settings = _make_settings(tmp_path)
    planner = Planner(settings.sunday)

    think_json = json.dumps({
        "needs_fact_check": True,
        "claims": ["摩尔线程是 GPU 公司"],
    })
    plan_json = json.dumps({
        "goal": "调研摩尔线程",
        "steps": [{"id": "step_1", "intent": "调研摩尔线程",
                   "expected_output": "", "success_criteria": "",
                   "depends_on": [], "requires_realtime_data": True}],
    })
    mc = _make_client([_resp(think_json), _resp(plan_json)])

    state = AgentState(
        session_id="s1",
        task="调研摩尔线程的最新上市状态",
        thinking_level=ThinkingLevel.OFF,
    )

    with patch("sunday.agent.llm_client._get_http_client", return_value=mc):
        plan = await planner.think_and_plan(state, tool_registry=None)

    # plan LLM 请求体（第二次 post）应含 hints 节
    plan_body = mc.post.call_args_list[1].kwargs["json"]
    plan_user_msg = plan_body["messages"][0]["content"]
    # 实际注入 hints 节的标志（区分于 plan.md 模板里的指引引用）
    assert "任务文本命中关键词" in plan_user_msg
    assert "时效敏感实体（来自 think 阶段）" in plan_user_msg
    assert "调研" in plan_user_msg or "上市" in plan_user_msg
    assert "摩尔线程" in plan_user_msg
    # plan 解析出的 step 字段保留
    assert plan.steps[0].requires_realtime_data is True


async def test_hints_skipped_when_no_signal(tmp_path):
    """task 无关键词 + 无 claims → plan prompt 不含 hints 节。"""
    settings = _make_settings(tmp_path)
    planner = Planner(settings.sunday)

    think_json = json.dumps({"needs_fact_check": False, "claims": []})
    plan_json = json.dumps({
        "goal": "写诗",
        "steps": [{"id": "s1", "intent": "x",
                   "expected_output": "", "success_criteria": "",
                   "depends_on": []}],
    })
    mc = _make_client([_resp(think_json), _resp(plan_json)])

    state = AgentState(
        session_id="s1",
        task="帮我写一首关于春天的诗",
        thinking_level=ThinkingLevel.OFF,
    )
    with patch("sunday.agent.llm_client._get_http_client", return_value=mc):
        await planner.think_and_plan(state, tool_registry=None)

    plan_body = mc.post.call_args_list[1].kwargs["json"]
    plan_user_msg = plan_body["messages"][0]["content"]
    # hints 实际注入时才会出现这一行；plan.md 模板里只是引用"# 实时数据信号"作为指引
    assert "任务文本命中关键词" not in plan_user_msg
    assert "时效敏感实体（来自 think 阶段）" not in plan_user_msg


async def test_realtime_hints_disabled_skips_section(tmp_path):
    """quality.realtime_hints.enabled=false → 不注入 hints 段，且 think 也不跑（fact_check 也关）。"""
    settings = _make_settings(tmp_path, quality={
        "realtime_hints": {"enabled": False},
        "fact_check": {"enabled": False},
    })
    planner = Planner(settings.sunday)

    plan_json = json.dumps({
        "goal": "x",
        "steps": [{"id": "s1", "intent": "x",
                   "expected_output": "", "success_criteria": "",
                   "depends_on": []}],
    })
    mc = _make_client([_resp(plan_json)])  # 仅 plan，think 跳过

    state = AgentState(session_id="s1", task="调研摩尔线程",
                        thinking_level=ThinkingLevel.OFF)
    with patch("sunday.agent.llm_client._get_http_client", return_value=mc):
        await planner.think_and_plan(state, tool_registry=None)

    assert mc.post.call_count == 1
    plan_user_msg = mc.post.call_args_list[0].kwargs["json"]["messages"][0]["content"]
    # hints 实际注入时才会出现这一行；plan.md 模板里只是引用"# 实时数据信号"作为指引
    assert "任务文本命中关键词" not in plan_user_msg
    assert "时效敏感实体（来自 think 阶段）" not in plan_user_msg


# ── plan_realtime_hints emit ─────────────────────────────────────────────

async def test_plan_realtime_hints_emit_when_signal(tmp_path):
    settings = _make_settings(tmp_path)
    planner = Planner(settings.sunday)

    think_json = json.dumps({
        "needs_fact_check": True, "claims": ["摩尔线程"],
    })
    plan_json = json.dumps({
        "goal": "g",
        "steps": [{"id": "s1", "intent": "x",
                   "expected_output": "", "success_criteria": "",
                   "depends_on": []}],
    })
    mc = _make_client([_resp(think_json), _resp(plan_json)])

    captured: list[tuple] = []

    async def emit(sid, ev, data):
        captured.append((sid, ev, data))

    state = AgentState(
        session_id="s1", task="调研摩尔线程",
        thinking_level=ThinkingLevel.OFF,
    )
    with patch("sunday.agent.llm_client._get_http_client", return_value=mc):
        await planner.think_and_plan(state, tool_registry=None, emit=emit)

    realtime_events = [c for c in captured if c[1] == "plan_realtime_hints"]
    assert len(realtime_events) == 1
    payload = realtime_events[0][2]
    assert payload["phase"] == "done"
    assert "调研" in payload["task_keywords"]
    assert "摩尔线程" in payload["claim_entities"]


# ── step.requires_realtime_data 解析向后兼容 ─────────────────────────────

async def test_old_plan_without_field_defaults_false(tmp_path):
    """旧版 plan JSON 不含 requires_realtime_data → pydantic 默认 False，无报错。"""
    settings = _make_settings(tmp_path)
    planner = Planner(settings.sunday)

    plan_json = json.dumps({
        "goal": "g",
        "steps": [{"id": "s1", "intent": "x",
                   "expected_output": "", "success_criteria": "",
                   "depends_on": []}],  # 无 requires_realtime_data
    })
    # 关闭 realtime_hints 让 think 不跑，简化 mock
    settings = _make_settings(tmp_path, quality={
        "realtime_hints": {"enabled": False},
        "fact_check": {"enabled": False},
    })
    planner = Planner(settings.sunday)
    mc = _make_client([_resp(plan_json)])

    state = AgentState(session_id="s1", task="任务", thinking_level=ThinkingLevel.OFF)
    with patch("sunday.agent.llm_client._get_http_client", return_value=mc):
        plan = await planner.think_and_plan(state)

    assert plan.steps[0].requires_realtime_data is False
