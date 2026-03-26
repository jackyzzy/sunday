"""T5-1 验证：Gateway 通信协议"""
from __future__ import annotations

from sunday.gateway.protocol import EventType, Message


def test_client_event_types_defined():
    """客户端→服务端事件类型均存在"""
    for name in ("send", "abort", "slash", "confirm"):
        assert hasattr(EventType, name.upper()), f"缺少 EventType.{name.upper()}"


def test_server_event_types_defined():
    """服务端→客户端事件类型均存在"""
    for name in ("status", "stream", "plan", "tool_start", "tool_end",
                 "confirm_request", "done", "error"):
        assert hasattr(EventType, name.upper()), f"缺少 EventType.{name.upper()}"


def test_message_serialization():
    """Message 序列化后可完整还原"""
    msg = Message(type=EventType.STATUS, session_id="abc123", data={"state": "thinking"})
    raw = msg.to_json()
    restored = Message.from_json(raw)
    assert restored.type == EventType.STATUS
    assert restored.session_id == "abc123"
    assert restored.data == {"state": "thinking"}


def test_message_has_ts():
    """ts 字段自动填充且非空"""
    msg = Message(type=EventType.DONE, session_id="s1", data={})
    assert msg.ts


def test_message_data_defaults_empty():
    """data 字段默认为空 dict"""
    msg = Message(type=EventType.ABORT, session_id="s1")
    assert msg.data == {}


def test_event_type_str_values():
    """EventType 字符串值与 design.md §8.3 一致"""
    assert EventType.SEND.value == "send"
    assert EventType.STATUS.value == "status"
    assert EventType.DONE.value == "done"
    assert EventType.CONFIRM_REQUEST.value == "confirm_request"
    assert EventType.TOOL_START.value == "tool_start"
    assert EventType.TOOL_END.value == "tool_end"
