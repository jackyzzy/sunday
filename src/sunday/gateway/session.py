"""Phase 5：SessionManager — 会话 JSONL 存储"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sunday.gateway.protocol import EventType

logger = logging.getLogger(__name__)


class SessionManager:
    """管理会话 JSONL 文件和 index.json。

    写操作通过 asyncio.Lock 串行化；index.json 使用 .tmp rename 原子写入。
    """

    def __init__(self, sessions_dir: Path) -> None:
        self._dir = sessions_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        # 内存中维护 session 元数据
        self._index: list[dict] = self._load_index()

    # ── 公开接口 ──────────────────────────────────────────────────────────

    def new_session(self) -> str:
        """创建新会话，返回 12 位 hex session_id。"""
        sid = uuid.uuid4().hex[:12]
        now = datetime.now(timezone.utc).isoformat()
        # 写 session_start 行
        path = self._dir / f"{sid}.jsonl"
        entry = json.dumps({
            "type": "session_start",
            "session_id": sid,
            "ts": now,
        }, ensure_ascii=False)
        path.write_text(entry + "\n", encoding="utf-8")
        # 更新 index（同步，new_session 不在异步上下文中调用时使用）
        meta = {"session_id": sid, "last_active": now, "created_at": now}
        self._index.insert(0, meta)
        self._write_index()
        logger.debug("新会话：%s", sid)
        return sid

    async def append(self, session_id: str, event_type: "EventType", data: dict) -> None:
        """追加事件到会话 JSONL 文件。"""
        now = datetime.now(timezone.utc).isoformat()
        entry = json.dumps({
            "type": event_type.value,
            "session_id": session_id,
            "data": data,
            "ts": now,
        }, ensure_ascii=False)
        path = self._dir / f"{session_id}.jsonl"
        async with self._lock:
            with path.open("a", encoding="utf-8") as f:
                f.write(entry + "\n")
            # 更新 last_active
            for meta in self._index:
                if meta["session_id"] == session_id:
                    meta["last_active"] = now
                    break
            self._write_index()

    def load_history(self, session_id: str, max_events: int = 200) -> list[dict]:
        """读取会话历史，返回最多 max_events 条事件（取末尾）。"""
        path = self._dir / f"{session_id}.jsonl"
        if not path.exists():
            return []
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        events = []
        for ln in lines:
            try:
                events.append(json.loads(ln))
            except json.JSONDecodeError:
                pass
        return events[-max_events:]

    def list_sessions(self) -> list[dict]:
        """返回所有会话元数据，按 last_active 倒序。"""
        return sorted(self._index, key=lambda s: s.get("last_active", ""), reverse=True)

    # ── 私有方法 ──────────────────────────────────────────────────────────

    def _load_index(self) -> list[dict]:
        index_path = self._dir / "index.json"
        if not index_path.exists():
            return []
        try:
            return json.loads(index_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []

    def _write_index(self) -> None:
        """原子写入 index.json（.tmp + rename）。"""
        index_path = self._dir / "index.json"
        tmp = index_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._index, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.rename(index_path)
