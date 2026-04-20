"""Planner 注入 session_thread 与首尾截断的单元测试。"""
from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import yaml

from sunday.agent.models import AgentState, Message, SessionThread, ThinkingLevel
from sunday.agent.planner import Planner, _truncate_head_tail


def _make_settings(tmp_path):
    from sunday.config import Settings
    config_file = tmp_path / "agent.yaml"
    config_file.write_text(yaml.dump({
        "model": {"provider": "anthropic", "id": "claude-test"},
        "reasoning": {"max_steps": 10},
        "memory": {"history_max_chars": 100},
    }))
    with patch.dict(os.environ, {
        "ANTHROPIC_API_KEY": "sk-ant-fake",
        "SUNDAY_CONFIGS_DIR": str(tmp_path),
    }):
        s = Settings()
        _ = s.sunday
        return s


def _mock_http(response_text: str):
    resp = MagicMock()
    resp.json.return_value = {
        "content": [{"type": "text", "text": response_text}],
        "stop_reason": "end_turn",
    }
    resp.raise_for_status.return_value = None
    resp.is_success = True
    client = AsyncMock()
    client.post = AsyncMock(return_value=resp)
    return client


def test_truncate_head_tail_keeps_both_ends():
    text = "A" * 30 + "MIDDLE" * 50 + "Z" * 30
    out = _truncate_head_tail(text, 100)
    assert out.startswith("A")
    assert out.endswith("Z")
    assert "中间省略" in out


def test_truncate_head_tail_short_untouched():
    assert _truncate_head_tail("hello", 100) == "hello"


async def test_think_and_plan_injects_session_thread(tmp_path):
    """session_thread 非空时，prompt 必须包含"会话主线"段和关键实体。"""
    settings = _make_settings(tmp_path)
    planner = Planner(settings.sunday, system_prompt="SYS")

    plan_json = json.dumps({"goal": "g", "steps": []})
    client = _mock_http(plan_json)

    thread = SessionThread(
        summary="围绕 DeepSeek V4 发布展开分析",
        key_entities=["DeepSeek V4", "AI 算力基础设施"],
    )
    state = AgentState(
        session_id="s1", task="产业链上下游报告",
        session_thread=thread,
        thinking_level=ThinkingLevel.OFF,
    )

    with (
        patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-fake"}),
        patch("sunday.agent.llm_client._get_http_client", return_value=client),
    ):
        await planner.think_and_plan(state)

    sent_body = client.post.call_args.kwargs["json"]
    sent_prompt = sent_body["messages"][0]["content"]
    assert "会话主线" in sent_prompt
    assert "DeepSeek V4" in sent_prompt
    assert "AI 算力基础设施" in sent_prompt
    assert "围绕 DeepSeek V4 发布展开分析" in sent_prompt


async def test_think_and_plan_injects_history_with_head_tail(tmp_path):
    """history 长内容走首尾截断，关键字在首尾应保留。"""
    settings = _make_settings(tmp_path)
    planner = Planner(settings.sunday)

    plan_json = json.dumps({"goal": "g", "steps": []})
    client = _mock_http(plan_json)

    long_answer = "HEAD_MARK " + "x" * 500 + " TAIL_MARK"
    state = AgentState(
        session_id="s1", task="当前任务",
        history=[
            Message(role="user", content="上一轮问题"),
            Message(role="assistant", content=long_answer),
        ],
        thinking_level=ThinkingLevel.OFF,
    )

    with (
        patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-fake"}),
        patch("sunday.agent.llm_client._get_http_client", return_value=client),
    ):
        await planner.think_and_plan(state)

    sent_prompt = client.post.call_args.kwargs["json"]["messages"][0]["content"]
    assert "对话历史" in sent_prompt
    assert "HEAD_MARK" in sent_prompt
    assert "TAIL_MARK" in sent_prompt
    assert "中间省略" in sent_prompt


async def test_think_and_plan_no_thread_no_thread_block(tmp_path):
    settings = _make_settings(tmp_path)
    planner = Planner(settings.sunday)
    client = _mock_http(json.dumps({"goal": "g", "steps": []}))
    state = AgentState(session_id="s1", task="t", thinking_level=ThinkingLevel.OFF)

    with (
        patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-fake"}),
        patch("sunday.agent.llm_client._get_http_client", return_value=client),
    ):
        await planner.think_and_plan(state)

    sent_prompt = client.post.call_args.kwargs["json"]["messages"][0]["content"]
    assert "主题概括：" not in sent_prompt
    assert "关键实体：" not in sent_prompt
