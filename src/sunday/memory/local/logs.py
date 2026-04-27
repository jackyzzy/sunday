"""LocalLogsClient — 文件系统实现的 logs sub-client。

每个 session 一个 logs/{session_id}.jsonl。写入路径 emit() 为 async 接口
但内部同步追加文件（本地写文件无明显开销，崩溃时无尾部丢失风险）。
未来 HttpLogsClient 可改为后台队列 + 批量 flush。
"""
from __future__ import annotations

import logging
from pathlib import Path

from sunday.memory._io import append_jsonl, read_jsonl_tail
from sunday.memory.models import LogEvent

logger = logging.getLogger(__name__)


class LocalLogsClient:
    """logs/ 资源域的本地实现。"""

    def __init__(self, log_dir: Path) -> None:
        self._dir = log_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    async def emit(self, session_id: str, event: LogEvent) -> None:
        path = self._dir / f"{session_id}.jsonl"
        entry = {
            "ts": event.ts,
            "session_id": event.session_id or session_id,
            "event": event.event,
            "data": event.data,
        }
        try:
            append_jsonl(path, entry)
        except OSError as e:
            logger.warning("写日志失败 session=%s：%s", session_id, e)

    async def read(self, session_id: str, *, tail: int = 500) -> list[LogEvent]:
        path = self._dir / f"{session_id}.jsonl"
        raw = read_jsonl_tail(path, tail)
        out: list[LogEvent] = []
        for entry in raw:
            try:
                out.append(LogEvent(**entry))
            except Exception:
                continue
        return out

    async def delete(self, session_id: str) -> None:
        path = self._dir / f"{session_id}.jsonl"
        if path.exists():
            path.unlink()
