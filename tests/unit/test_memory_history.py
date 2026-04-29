"""S2-B 验证：memory/history.py 是 extract_conversation + event_to_dict 的归位之地。

extract_conversation 行为契约由原 test_extract_conversation.py 充分覆盖；本文件
仅验证：(1) 模块位置正确、(2) event_to_dict 的字段映射。
"""
from __future__ import annotations

from sunday.memory.history import event_to_dict, extract_conversation
from sunday.memory.models import SessionEvent


def test_extract_conversation_is_importable_from_memory():
    """从 sunday.memory.history 直接 import（不再走 service.history）。"""
    msgs = extract_conversation([], max_turns=5)
    assert msgs == []


def test_event_to_dict_maps_all_fields():
    ev = SessionEvent(
        type="send",
        session_id="abc12345",
        turn_id="t001-deadbe",
        data={"content": "hello"},
        ts="2026-04-29T00:00:00Z",
    )
    d = event_to_dict(ev)
    assert d == {
        "type": "send",
        "session_id": "abc12345",
        "turn_id": "t001-deadbe",
        "data": {"content": "hello"},
        "ts": "2026-04-29T00:00:00Z",
    }


def test_event_to_dict_preserves_empty_data():
    ev = SessionEvent(type="abort", session_id="x", turn_id="t1", data={}, ts="ts")
    d = event_to_dict(ev)
    assert d["data"] == {}
