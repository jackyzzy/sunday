"""Memory 客户端跨进程数据模型。

所有模型均为 Pydantic BaseModel，可干净地序列化为 JSON，便于未来 HTTP Memory
服务 API 直接复用同一份契约。

注：`SessionThread` 仍定义在 `sunday.agent.models` 中（被 AgentState 引用），
此处仅做类型 re-export，保持单一来源。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from sunday.agent.models import SessionThread

__all__ = [
    "SessionMeta",
    "SessionEvent",
    "TurnRecord",
    "TurnSummary",
    "LogEvent",
    "RuntimeRules",
    "SessionThread",
    "new_turn_id",
]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_turn_id(turn_index: int) -> str:
    """生成可读、单调可排序的 turn_id：`t{index:03d}-{short_uuid}`。

    例：t001-ab12cd、t002-3f4a5b。同 session 下按字典序天然递增，
    日志 / 调试时一眼识别归属（与 8 位裸 UUID 相比体验更好）。
    """
    return f"t{turn_index:03d}-{uuid.uuid4().hex[:6]}"


class SessionMeta(BaseModel):
    """sessions/index.json 与 sessions/{id}/meta.json 的会话概要。"""

    session_id: str
    created_at: str
    last_active: str
    title: str = ""
    turn_count: int = 0
    last_turn_outcome: str = ""


class TurnSummary(BaseModel):
    """meta.json 的 turns[] 中每轮的摘要项。"""

    turn_id: str
    turn_index: int
    ts_start: str = ""
    ts_end: str = ""
    outcome: str = ""


class SessionEvent(BaseModel):
    """sessions/{id}/stream.jsonl 中的一条事件。"""

    type: str
    session_id: str
    turn_id: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    ts: str = Field(default_factory=_utc_now_iso)


class TurnRecord(BaseModel):
    """sessions/{id}/turns/{turn_id}.json 的完整轮次记录。"""

    turn_id: str
    turn_index: int = 0
    ts_start: str = ""
    ts_end: str = ""
    outcome: str = ""
    user_input: str = ""
    plan: dict[str, Any] | None = None
    execution: list[dict[str, Any]] = Field(default_factory=list)
    output: str = ""


class LogEvent(BaseModel):
    """logs/{session_id}.jsonl 中的一条结构化日志。"""

    event: str
    session_id: str
    data: dict[str, Any] = Field(default_factory=dict)
    ts: str = Field(default_factory=_utc_now_iso)


class RuntimeRules(BaseModel):
    """workspace/RUNTIME_RULES.md 解析后的运行规则。"""

    realtime_keywords: list[str] = Field(default_factory=list)
    subject_min_output_chars: int = 200
