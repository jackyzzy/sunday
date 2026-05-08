"""OpenAICompatProvider — DeepSeek/Qwen/Ollama 等 OpenAI 兼容接口的解析与回传契约。

重点回归：DeepSeek 思考模式的 `reasoning_content` 字段必须：
  1. parse_response 时从 message 中读出（用于展示思考过程）
  2. build_tool_result_messages 时回传到 assistant 消息（否则 DeepSeek 400）
"""
from __future__ import annotations

from sunday.agent.providers.base import LLMResponse, ToolCall
from sunday.agent.providers.openai_compat import OpenAICompatProvider


def _raw_with_reasoning(*, with_tool_call: bool = False) -> dict:
    """构造一个带 reasoning_content 的 DeepSeek 风格响应。"""
    msg: dict = {
        "role": "assistant",
        "content": "最终答案：42",
        "reasoning_content": "让我想一下... 6 * 7 = 42",
    }
    if with_tool_call:
        msg["content"] = ""
        msg["tool_calls"] = [{
            "id": "call_x1",
            "type": "function",
            "function": {"name": "calc", "arguments": '{"expr":"6*7"}'},
        }]
    return {"choices": [{"message": msg, "finish_reason": "stop" if not with_tool_call else "tool_calls"}]}


def test_parse_response_extracts_reasoning_content():
    provider = OpenAICompatProvider()
    resp = provider.parse_response(_raw_with_reasoning())
    assert resp.thinking == "让我想一下... 6 * 7 = 42"
    assert resp.text == "最终答案：42"
    # raw_content 必须保留 reasoning_content 供后续回传
    assert isinstance(resp.raw_content, dict)
    assert resp.raw_content["reasoning_content"] == "让我想一下... 6 * 7 = 42"


def test_parse_response_no_reasoning_falls_back_to_think_tag():
    provider = OpenAICompatProvider()
    raw = {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": "<think>quick</think>final",
            },
            "finish_reason": "stop",
        }],
    }
    resp = provider.parse_response(raw)
    assert resp.thinking == "quick"
    assert resp.text == "final"
    assert resp.raw_content is None


def test_parse_response_no_thinking_at_all():
    provider = OpenAICompatProvider()
    raw = {
        "choices": [{
            "message": {"role": "assistant", "content": "plain text"},
            "finish_reason": "stop",
        }],
    }
    resp = provider.parse_response(raw)
    # split_thinking 对无 <think> 标签的纯文本返回 thinking=None
    assert not resp.thinking
    assert resp.text == "plain text"
    assert resp.raw_content is None


def test_build_tool_result_messages_carries_reasoning_back():
    """关键回归：DeepSeek 要求 reasoning_content 必须随 assistant 消息回传，否则 400。"""
    provider = OpenAICompatProvider()
    response = LLMResponse(
        text="",
        thinking="思考链 X",
        tool_call=ToolCall(id="call_y", name="add", arguments='{"a":1,"b":2}'),
        finish_reason="tool_calls",
        raw_content={"reasoning_content": "思考链 X"},
    )
    messages = provider.build_tool_result_messages(response, observation="3")
    assert len(messages) == 2
    assistant_msg = messages[0]
    assert assistant_msg["role"] == "assistant"
    assert assistant_msg["reasoning_content"] == "思考链 X"
    assert assistant_msg["tool_calls"][0]["function"]["name"] == "add"
    tool_msg = messages[1]
    assert tool_msg["role"] == "tool"
    assert tool_msg["tool_call_id"] == "call_y"
    assert tool_msg["content"] == "3"


def test_build_tool_result_messages_skips_reasoning_when_absent():
    """非思考模型（raw_content=None）不应注入 reasoning_content 字段。"""
    provider = OpenAICompatProvider()
    response = LLMResponse(
        text="",
        thinking=None,
        tool_call=ToolCall(id="call_z", name="ping", arguments="{}"),
        finish_reason="tool_calls",
        raw_content=None,
    )
    messages = provider.build_tool_result_messages(response, observation="pong")
    assert "reasoning_content" not in messages[0]


def test_round_trip_deepseek_thinking_with_tool_call():
    """端到端：parse → build_tool_result_messages 把 reasoning_content 完整传回。"""
    provider = OpenAICompatProvider()
    raw = _raw_with_reasoning(with_tool_call=True)
    resp = provider.parse_response(raw)
    assert resp.tool_call is not None

    messages = provider.build_tool_result_messages(resp, observation="42")
    assert messages[0]["reasoning_content"] == "让我想一下... 6 * 7 = 42"
    assert messages[0]["content"] == ""  # 必须是 "" 不是 None
    assert messages[0]["tool_calls"][0]["id"] == "call_x1"
