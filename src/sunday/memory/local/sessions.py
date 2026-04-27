"""LocalSessionsClient — 文件系统实现的 sessions sub-client。

目录结构：
    sessions/
    ├── index.json                  # 会话概要列表（last_active 倒序）
    └── {session_id}/
        ├── meta.json               # 会话完整元数据 + session_thread + turns 摘要
        ├── stream.jsonl            # 原始事件流（append-only，含 turn_id）
        ├── reports/{turn_id}/      # 每轮报告（由 ReactAgent / 工具写入）
        └── turns/
            └── {turn_id}.json      # 每轮结构化记录

并发安全：所有写操作都在 asyncio.Lock 内串行化，且 index.json 写入前会先
reload 磁盘当前内容，再 apply 本次变更，避免多 client 实例并发覆盖。
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sunday.agent.models import SessionThread
from sunday.memory._io import (
    atomic_write_text,
    read_json,
    read_json_or,
    read_jsonl_tail,
    write_json,
)
from sunday.memory.models import (
    SessionEvent,
    SessionMeta,
    TurnRecord,
    TurnSummary,
)

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _meta_dict_to_model(meta: dict) -> SessionMeta:
    return SessionMeta(
        session_id=meta["session_id"],
        created_at=meta.get("created_at", ""),
        last_active=meta.get("last_active", ""),
        title=meta.get("title", ""),
        turn_count=meta.get("turn_count", 0),
        last_turn_outcome=meta.get("last_turn_outcome", ""),
    )


class LocalSessionsClient:
    """sessions/ 资源域的本地实现。"""

    def __init__(self, sessions_dir: Path, *, logs_client=None) -> None:
        """`logs_client` 用于 delete() 级联删 logs/{id}.jsonl。"""
        self._dir = sessions_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._logs_client = logs_client  # set by LocalMemoryClient after composition

    def _bind_logs(self, logs_client) -> None:
        """LocalMemoryClient 组装后调用以建立级联删除依赖。"""
        self._logs_client = logs_client

    # ── 生命周期 ───────────────────────────────────────────────────────────

    async def create(self, *, session_id: str | None = None) -> SessionMeta:
        sid = session_id or str(uuid.uuid4())
        async with self._lock:
            return self._create_unlocked(sid)

    async def ensure(self, session_id: str) -> SessionMeta:
        async with self._lock:
            session_dir = self._dir / session_id
            if session_dir.exists():
                meta = self._read_meta(session_id)
                if meta is not None:
                    return _meta_dict_to_model(meta)
            return self._create_unlocked(session_id)

    async def get(self, session_id: str) -> SessionMeta | None:
        meta = self._read_meta(session_id)
        if meta is None:
            return None
        return _meta_dict_to_model(meta)

    async def list(self, *, limit: int = 100) -> list[SessionMeta]:
        """扫盘聚合所有会话，按 last_active 倒序返回前 limit 条。

        不依赖 index.json 内存缓存——这正是历史 bug（陈旧快照覆盖磁盘）的根源。
        index.json 仅作为快速概要的派生物，写入操作在锁内 reload-then-write。
        """
        sessions: list[SessionMeta] = []
        for session_dir in self._dir.iterdir():
            if not session_dir.is_dir():
                continue
            meta = self._read_meta(session_dir.name)
            if meta is None:
                continue
            sessions.append(_meta_dict_to_model(meta))
        sessions.sort(key=lambda s: s.last_active, reverse=True)
        return sessions[:limit]

    async def delete(self, session_id: str) -> None:
        """删除会话目录 + index 条目；级联删除 logs/{id}.jsonl。"""
        async with self._lock:
            session_dir = self._dir / session_id
            if session_dir.exists():
                shutil.rmtree(session_dir)
            self._update_index_unlocked(remove_id=session_id)
        if self._logs_client is not None:
            await self._logs_client.delete(session_id)

    async def reset(self, session_id: str) -> None:
        async with self._lock:
            session_dir = self._dir / session_id
            if not session_dir.exists():
                return
            turns_dir = session_dir / "turns"
            if turns_dir.exists():
                for f in turns_dir.glob("*.json"):
                    f.unlink()
            stream_path = session_dir / "stream.jsonl"
            if stream_path.exists():
                lines = stream_path.read_text(encoding="utf-8").splitlines()
                first = next((ln for ln in lines if ln.strip()), "")
                stream_path.write_text((first + "\n") if first else "", encoding="utf-8")
            meta_path = session_dir / "meta.json"
            if meta_path.exists():
                meta = read_json(meta_path)
                meta["turn_count"] = 0
                meta["turns"] = []
                write_json(meta_path, meta)

    # ── 事件流 + 轮次 ──────────────────────────────────────────────────────

    async def append_event(self, session_id: str, event: SessionEvent) -> None:
        """追加事件到 stream.jsonl，更新 meta + index 的 last_active 与 title。"""
        entry = {
            "type": event.type,
            "session_id": event.session_id or session_id,
            "turn_id": event.turn_id,
            "data": event.data,
            "ts": event.ts,
        }
        async with self._lock:
            session_dir = self._dir / session_id
            stream_path = session_dir / "stream.jsonl"
            if stream_path.exists():
                with stream_path.open("a", encoding="utf-8") as f:
                    import json as _json
                    f.write(_json.dumps(entry, ensure_ascii=False) + "\n")
            # 维护 title：首次 send 事件用 user_input 前 40 字作为 title
            new_title: str | None = None
            if event.type == "send":
                content = event.data.get("content", "") if isinstance(event.data, dict) else ""
                meta = self._read_meta(session_id)
                if meta is not None and not meta.get("title") and content:
                    new_title = content[:40].replace("\n", " ")
                    meta["title"] = new_title
                    meta["last_active"] = event.ts
                    write_json(session_dir / "meta.json", meta)
            self._touch_index_unlocked(session_id, last_active=event.ts, title=new_title)

    async def write_turn(self, session_id: str, turn: TurnRecord) -> None:
        """写 turns/{turn_id}.json，同步更新 meta（turn_count + turns[]）和 index。"""
        async with self._lock:
            session_dir = self._dir / session_id
            meta = self._read_meta(session_id)
            if meta is None:
                logger.warning("write_turn 跳过：session %s meta.json 不存在", session_id)
                return

            meta["last_active"] = turn.ts_end or _utc_now_iso()
            meta["turn_count"] = int(meta.get("turn_count", 0)) + 1
            meta["last_turn_outcome"] = turn.outcome
            turn_index = turn.turn_index or meta["turn_count"]

            summary = TurnSummary(
                turn_id=turn.turn_id,
                turn_index=turn_index,
                ts_start=turn.ts_start,
                ts_end=turn.ts_end,
                outcome=turn.outcome,
            ).model_dump()
            meta.setdefault("turns", []).append(summary)
            write_json(session_dir / "meta.json", meta)

            persisted = turn.model_copy(update={"turn_index": turn_index}).model_dump()
            write_json(session_dir / "turns" / f"{turn.turn_id}.json", persisted)

            self._touch_index_unlocked(
                session_id,
                last_active=meta["last_active"],
                turn_count=meta["turn_count"],
                last_turn_outcome=turn.outcome,
            )

    async def load_events(
        self, session_id: str, *, max_events: int = 200
    ) -> list[SessionEvent]:
        stream_path = self._dir / session_id / "stream.jsonl"
        raw = read_jsonl_tail(stream_path, max_events)
        return [SessionEvent(**e) for e in raw if "type" in e]

    async def load_turns(self, session_id: str) -> list[TurnRecord]:
        turns_dir = self._dir / session_id / "turns"
        if not turns_dir.exists():
            return []
        records: list[TurnRecord] = []
        for f in sorted(turns_dir.glob("*.json")):
            try:
                records.append(TurnRecord(**read_json(f)))
            except Exception:
                logger.exception("解析 turn 文件失败：%s", f)
        records.sort(key=lambda t: t.turn_index)
        return records

    async def load_turn(self, session_id: str, turn_id: str) -> TurnRecord | None:
        path = self._dir / session_id / "turns" / f"{turn_id}.json"
        if not path.exists():
            return None
        return TurnRecord(**read_json(path))

    # ── 主线 ───────────────────────────────────────────────────────────────

    async def get_thread(self, session_id: str) -> SessionThread | None:
        meta = self._read_meta(session_id)
        if meta is None:
            return None
        thread = meta.get("session_thread")
        if not isinstance(thread, dict):
            return None
        return SessionThread(**thread)

    async def save_thread(self, session_id: str, thread: SessionThread) -> None:
        async with self._lock:
            meta_path = self._dir / session_id / "meta.json"
            if not meta_path.exists():
                return
            meta = read_json(meta_path)
            meta["session_thread"] = thread.model_dump()
            write_json(meta_path, meta)

    # ── 派生路径 ───────────────────────────────────────────────────────────

    def report_dir(self, session_id: str, turn_id: str) -> Path:
        """sessions/{session_id}/reports/{turn_id}/ 路径（不创建目录）。"""
        return self._dir / session_id / "reports" / turn_id

    # ── 内部 helper ────────────────────────────────────────────────────────

    def _create_unlocked(self, session_id: str) -> SessionMeta:
        """假定调用方已持有 self._lock。"""
        now = _utc_now_iso()
        session_dir = self._dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "turns").mkdir(exist_ok=True)

        meta = {
            "session_id": session_id,
            "created_at": now,
            "last_active": now,
            "title": "",
            "turn_count": 0,
            "turns": [],
        }
        write_json(session_dir / "meta.json", meta)

        first_event = SessionEvent(
            type="session_start", session_id=session_id, turn_id=None, data={}, ts=now,
        )
        atomic_write_text(
            session_dir / "stream.jsonl",
            __import__("json").dumps({
                "type": first_event.type,
                "session_id": session_id,
                "turn_id": None,
                "data": {},
                "ts": now,
            }, ensure_ascii=False) + "\n",
        )

        self._touch_index_unlocked(session_id, last_active=now)
        return SessionMeta(
            session_id=session_id, created_at=now, last_active=now,
            title="", turn_count=0, last_turn_outcome="",
        )

    def _read_meta(self, session_id: str) -> dict | None:
        meta_path = self._dir / session_id / "meta.json"
        if not meta_path.exists():
            return None
        try:
            return read_json(meta_path)
        except Exception:
            logger.exception("读取 meta.json 失败：%s", meta_path)
            return None

    def _touch_index_unlocked(
        self,
        session_id: str,
        *,
        last_active: str | None = None,
        title: str | None = None,
        turn_count: int | None = None,
        last_turn_outcome: str | None = None,
    ) -> None:
        """读盘 → 更新某 session 条目（不存在则插入）→ 原子写。

        每次都重新读盘，避免内存陈旧快照覆盖其他进程的写入（修复历史 bug）。
        """
        index = self._load_index_from_disk()
        idx_map = {entry["session_id"]: entry for entry in index}
        meta = self._read_meta(session_id)

        entry = idx_map.get(session_id)
        if entry is None:
            entry = {
                "session_id": session_id,
                "created_at": (meta.get("created_at", "") if meta else last_active or ""),
                "last_active": last_active or (meta.get("last_active", "") if meta else ""),
                "title": title or (meta.get("title", "") if meta else ""),
                "turn_count": turn_count if turn_count is not None
                              else (meta.get("turn_count", 0) if meta else 0),
                "last_turn_outcome": last_turn_outcome or "",
            }
            index.insert(0, entry)
        else:
            if last_active is not None:
                entry["last_active"] = last_active
            if title is not None:
                entry["title"] = title
            if turn_count is not None:
                entry["turn_count"] = turn_count
            if last_turn_outcome is not None:
                entry["last_turn_outcome"] = last_turn_outcome
            elif meta is not None and not entry.get("title") and meta.get("title"):
                entry["title"] = meta["title"]
        write_json(self._dir / "index.json", index)

    def _update_index_unlocked(self, *, remove_id: str) -> None:
        index = [e for e in self._load_index_from_disk() if e["session_id"] != remove_id]
        write_json(self._dir / "index.json", index)

    def _load_index_from_disk(self) -> list[dict]:
        return read_json_or(self._dir / "index.json", [])
