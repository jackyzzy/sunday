"""Phase 5：Gateway 通信协议 — EventType + Message"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class EventType(str, Enum):
    """所有 WebSocket 消息类型（客户端↔服务端）。"""

    # 客户端 → 服务端
    SEND = "send"
    ABORT = "abort"
    SLASH = "slash"
    CONFIRM = "confirm"

    # 服务端 → 客户端
    STATUS = "status"
    STREAM = "stream"
    PLAN = "plan"
    TOOL_START = "tool_start"
    TOOL_END = "tool_end"
    CONFIRM_REQUEST = "confirm_request"
    DONE = "done"
    ERROR = "error"
    SLASH_RESULT = "slash_result"


class Message:
    """统一消息格式：{"type": ..., "session_id": ..., "data": {...}, "ts": ...}"""

    __slots__ = ("type", "session_id", "data", "ts")

    def __init__(
        self,
        type: EventType,
        session_id: str = "",
        data: dict[str, Any] | None = None,
        ts: str = "",
    ) -> None:
        self.type = type
        self.session_id = session_id
        self.data = data if data is not None else {}
        self.ts = ts or datetime.now(timezone.utc).isoformat()

    def to_json(self) -> str:
        return json.dumps({
            "type": self.type.value,
            "session_id": self.session_id,
            "data": self.data,
            "ts": self.ts,
        }, ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> "Message":
        d = json.loads(raw)
        return cls(
            type=EventType(d["type"]),
            session_id=d.get("session_id", ""),
            data=d.get("data", {}),
            ts=d.get("ts", ""),
        )
