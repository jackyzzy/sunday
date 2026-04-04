"""agent 包共用工具函数。"""
from __future__ import annotations

from typing import Awaitable, Callable


# emit 回调类型
EmitCallable = Callable[[str, str, dict], Awaitable[None]]


async def noop_emit(session_id: str, event_type: str, data: dict) -> None:
    """默认空 emit，用于不需要推送事件的场景。"""
    _ = session_id, event_type, data


def strip_code_fence(text: str) -> str:
    """去除 markdown 代码块包装（```json...``` 或 ```...```）。"""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        inner = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
        text = "\n".join(inner).strip()
    return text
