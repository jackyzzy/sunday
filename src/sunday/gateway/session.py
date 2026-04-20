"""Session Manager — 会话目录存储

目录结构（L3 会话层）：
    sessions/
    ├── index.json                  # 快速概要列表
    └── {session_id}/
        ├── meta.json               # 会话完整元数据
        ├── stream.jsonl            # 原始事件流（append-only）
        └── turns/
            └── {turn_id}.json      # 每轮结构化记录（turn_end 时写入）
"""
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
    """管理会话目录（sessions/{id}/）、stream.jsonl 和 turns/*.json。

    写操作通过 asyncio.Lock 串行化；index.json 使用 .tmp rename 原子写入。
    """

    def __init__(self, sessions_dir: Path) -> None:
        self._dir = sessions_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._index: list[dict] = self._load_index()

    # ── 会话生命周期 ──────────────────────────────────────────────────────────

    def new_session(self) -> str:
        """创建新会话目录，返回 UUID 格式 session_id。"""
        sid = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        session_dir = self._dir / sid
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "turns").mkdir(exist_ok=True)

        # 写 meta.json
        meta = {
            "session_id": sid,
            "created_at": now,
            "last_active": now,
            "title": "",
            "turn_count": 0,
            "turns": [],
        }
        _atomic_write(session_dir / "meta.json",
                      json.dumps(meta, ensure_ascii=False, indent=2))

        # stream.jsonl 首行
        stream_entry = json.dumps({
            "type": "session_start",
            "session_id": sid,
            "turn_id": None,
            "data": {},
            "ts": now,
        }, ensure_ascii=False)
        (session_dir / "stream.jsonl").write_text(stream_entry + "\n", encoding="utf-8")

        # 更新 index
        index_meta = {"session_id": sid, "created_at": now, "last_active": now,
                      "title": "", "turn_count": 0, "last_turn_outcome": ""}
        self._index.insert(0, index_meta)
        self._write_index()
        logger.debug("新会话：%s", sid)
        return sid

    def ensure_session(self, session_id: str) -> None:
        """如果 session 目录不存在则创建（用于接受客户端传入的 session_id）。

        TUI 在客户端本地生成 session_id，Gateway 首次收到该 id 的消息时调用此方法
        确保 sessions/{id}/ 目录结构完整。已存在则幂等跳过。
        """
        session_dir = self._dir / session_id
        if session_dir.exists():
            return
        now = datetime.now(timezone.utc).isoformat()
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
        _atomic_write(session_dir / "meta.json",
                      json.dumps(meta, ensure_ascii=False, indent=2))

        stream_entry = json.dumps({
            "type": "session_start",
            "session_id": session_id,
            "turn_id": None,
            "data": {},
            "ts": now,
        }, ensure_ascii=False)
        (session_dir / "stream.jsonl").write_text(stream_entry + "\n", encoding="utf-8")

        index_meta = {"session_id": session_id, "created_at": now, "last_active": now,
                      "title": "", "turn_count": 0, "last_turn_outcome": ""}
        self._index.insert(0, index_meta)
        self._write_index()
        logger.debug("自动注册会话：%s", session_id)

    def reset_session(self, session_id: str) -> None:
        """清空 turns/，保留 meta.json 和 stream.jsonl 首行。"""
        session_dir = self._dir / session_id
        if not session_dir.exists():
            return

        # 清空 turns 目录
        turns_dir = session_dir / "turns"
        if turns_dir.exists():
            for f in turns_dir.glob("*.json"):
                f.unlink()

        # stream.jsonl 只保留首行
        stream_path = session_dir / "stream.jsonl"
        if stream_path.exists():
            lines = stream_path.read_text(encoding="utf-8").splitlines()
            first = next((ln for ln in lines if ln.strip()), "")
            stream_path.write_text((first + "\n") if first else "", encoding="utf-8")

        # 重置 meta.json 中的 turn 信息
        meta_path = session_dir / "meta.json"
        if meta_path.exists():
            meta = _read_json(meta_path)
            meta["turn_count"] = 0
            meta["turns"] = []
            _atomic_write(meta_path, json.dumps(meta, ensure_ascii=False, indent=2))

        logger.debug("会话已重置：%s", session_id)

    def delete_session(self, session_id: str) -> None:
        """删除整个会话目录及 index 条目。"""
        import shutil
        session_dir = self._dir / session_id
        if session_dir.exists():
            shutil.rmtree(session_dir)

        # 兼容旧单文件格式
        old_file = self._dir / f"{session_id}.jsonl"
        if old_file.exists():
            old_file.unlink()

        self._index = [e for e in self._index if e["session_id"] != session_id]
        self._write_index()
        logger.debug("会话已删除：%s", session_id)

    # ── 事件写入 ─────────────────────────────────────────────────────────────

    async def append_stream(
        self,
        session_id: str,
        event_type: "EventType",
        data: dict,
        turn_id: str | None = None,
    ) -> None:
        """追加事件到 stream.jsonl，并更新 index 的 last_active / title。"""
        now = datetime.now(timezone.utc).isoformat()
        entry = json.dumps({
            "type": event_type.value if hasattr(event_type, "value") else event_type,
            "session_id": session_id,
            "turn_id": turn_id,
            "data": data,
            "ts": now,
        }, ensure_ascii=False)

        stream_path = self._dir / session_id / "stream.jsonl"
        async with self._lock:
            if stream_path.exists():
                with stream_path.open("a", encoding="utf-8") as f:
                    f.write(entry + "\n")
            # 更新 index
            ev_type_val = event_type.value if hasattr(event_type, "value") else event_type
            for meta in self._index:
                if meta["session_id"] == session_id:
                    meta["last_active"] = now
                    if ev_type_val == "send" and data.get("content") and not meta.get("title"):
                        meta["title"] = data["content"][:40].replace("\n", " ")
                        # 同步更新 meta.json title
                        _update_meta_title(self._dir / session_id / "meta.json",
                                           meta["title"], now)
                    break
            self._write_index()

    async def write_turn(self, session_id: str, turn: dict) -> None:
        """写入 turns/{turn_id}.json，更新 meta.json 和 index.json。"""
        turn_id = turn["turn_id"]
        turn_path = self._dir / session_id / "turns" / f"{turn_id}.json"

        async with self._lock:
            # 更新 meta.json（需先拿到 turn_count，以便回填 turn_index）
            meta_path = self._dir / session_id / "meta.json"
            if meta_path.exists():
                meta = _read_json(meta_path)
                meta["last_active"] = turn.get("ts_end", "")
                meta["turn_count"] = meta.get("turn_count", 0) + 1
                # 回填 turn["turn_index"]（dict.get 对 None 值不生效，需显式判空）
                if not turn.get("turn_index"):
                    turn["turn_index"] = meta["turn_count"]
                turn_summary = {
                    "turn_id": turn_id,
                    "turn_index": turn["turn_index"],
                    "ts_start": turn.get("ts_start", ""),
                    "ts_end": turn.get("ts_end", ""),
                    "outcome": turn.get("outcome", ""),
                }
                meta.setdefault("turns", []).append(turn_summary)
                _atomic_write(meta_path, json.dumps(meta, ensure_ascii=False, indent=2))

            # 写 turn 文件（此时 turn["turn_index"] 已定型）
            _atomic_write(turn_path, json.dumps(turn, ensure_ascii=False, indent=2))

            # 更新 index
            for idx_meta in self._index:
                if idx_meta["session_id"] == session_id:
                    idx_meta["last_active"] = turn.get("ts_end", "")
                    idx_meta["turn_count"] = idx_meta.get("turn_count", 0) + 1
                    idx_meta["last_turn_outcome"] = turn.get("outcome", "")
                    break
            self._write_index()

    # ── 向后兼容：旧 append() 接口 ───────────────────────────────────────────

    async def append(
        self,
        session_id: str,
        event_type: "EventType",
        data: dict,
        turn_id: str | None = None,
    ) -> None:
        """向后兼容接口，委托给 append_stream()。"""
        await self.append_stream(session_id, event_type, data, turn_id)

    # ── 读取接口 ──────────────────────────────────────────────────────────────

    def load_history(
        self,
        session_id: str,
        max_events: int = 200,
        group_by_turn: bool = False,
    ) -> list[dict]:
        """读取会话历史。

        group_by_turn=False（默认）：读 stream.jsonl 末尾 N 条（向后兼容）。
        group_by_turn=True：读 turns/*.json，返回按 turn 分组的结构。
        """
        if group_by_turn:
            return self._load_turns_grouped(session_id)

        # 优先读新目录格式
        stream_path = self._dir / session_id / "stream.jsonl"
        if stream_path.exists():
            return _read_jsonl_tail(stream_path, max_events)

        # 兼容旧单文件格式
        old_path = self._dir / f"{session_id}.jsonl"
        if old_path.exists():
            return _read_jsonl_tail(old_path, max_events)

        return []

    def load_turn(self, session_id: str, turn_id: str) -> dict | None:
        """读取单个 turn 结构化记录。"""
        turn_path = self._dir / session_id / "turns" / f"{turn_id}.json"
        if not turn_path.exists():
            return None
        return _read_json(turn_path)

    # ── 会话主线（session_thread） ──────────────────────────────────────────

    def get_session_title(self, session_id: str) -> str:
        """读取 meta.json 的 title 字段。不存在则返回空字符串。"""
        meta_path = self._dir / session_id / "meta.json"
        if not meta_path.exists():
            return ""
        try:
            meta = _read_json(meta_path)
        except Exception:
            return ""
        title = meta.get("title", "")
        return title if isinstance(title, str) else ""

    def get_prior_turn_count(self, session_id: str) -> int:
        """读取 meta.json 的 turn_count（本 session 已完成的轮数）。

        用于判断"当前是首轮还是有历史"——例如 session_thread 空值回退时，
        仅当 turn_count > 0（即本轮之前至少有一轮尝试过）才注入 title。
        """
        meta_path = self._dir / session_id / "meta.json"
        if not meta_path.exists():
            return 0
        try:
            meta = _read_json(meta_path)
        except Exception:
            return 0
        count = meta.get("turn_count", 0)
        return count if isinstance(count, int) else 0

    def get_session_thread(self, session_id: str) -> dict | None:
        """读取 meta.json 的 session_thread（跨轮主线摘要）。不存在时返回 None。"""
        meta_path = self._dir / session_id / "meta.json"
        if not meta_path.exists():
            return None
        try:
            meta = _read_json(meta_path)
        except Exception:
            return None
        thread = meta.get("session_thread")
        if not isinstance(thread, dict):
            return None
        return thread

    async def save_session_thread(self, session_id: str, thread: dict) -> None:
        """写入 meta.json 的 session_thread 字段（原子替换）。"""
        meta_path = self._dir / session_id / "meta.json"
        async with self._lock:
            if not meta_path.exists():
                return
            try:
                meta = _read_json(meta_path)
            except Exception:
                return
            meta["session_thread"] = thread
            _atomic_write(meta_path, json.dumps(meta, ensure_ascii=False, indent=2))

    def list_sessions(self) -> list[dict]:
        """返回所有会话元数据，按 last_active 倒序。兼容旧单文件格式。"""
        indexed = {m["session_id"]: m for m in self._index}

        # 扫描新目录格式
        for session_dir in self._dir.iterdir():
            if not session_dir.is_dir():
                continue
            sid = session_dir.name
            if sid in indexed:
                continue
            meta_path = session_dir / "meta.json"
            if meta_path.exists():
                try:
                    meta = _read_json(meta_path)
                    indexed[sid] = {
                        "session_id": sid,
                        "title": meta.get("title", ""),
                        "created_at": meta.get("created_at", ""),
                        "last_active": meta.get("last_active", ""),
                        "turn_count": meta.get("turn_count", 0),
                        "last_turn_outcome": "",
                    }
                except Exception:
                    pass

        # 兼容旧单文件格式
        for path in self._dir.glob("*.jsonl"):
            sid = path.stem
            if sid == "index" or sid in indexed:
                continue
            try:
                first_line = path.read_text(encoding="utf-8").splitlines()[0]
                data = json.loads(first_line)
                ts = data.get("ts", "")
            except Exception:
                ts = ""
            indexed[sid] = {"session_id": sid, "last_active": ts, "created_at": ts,
                            "title": "", "turn_count": 0, "last_turn_outcome": ""}

        # 回填旧格式的 title
        index_dirty = False
        for sid, meta in indexed.items():
            if meta.get("title"):
                continue
            old_path = self._dir / f"{sid}.jsonl"
            if old_path.exists():
                try:
                    for ln in old_path.read_text(encoding="utf-8").splitlines():
                        ev = json.loads(ln)
                        if ev.get("type") == "send":
                            content = ev.get("data", {}).get("content", "")
                            if content:
                                meta["title"] = content[:40].replace("\n", " ")
                                index_dirty = True
                            break
                except Exception:
                    pass

        if index_dirty:
            self._index = list(indexed.values())
            self._write_index()

        return sorted(indexed.values(), key=lambda s: s.get("last_active", ""), reverse=True)

    # ── 私有方法 ──────────────────────────────────────────────────────────────

    def _load_turns_grouped(self, session_id: str) -> list[dict]:
        """读取 turns/*.json，按 turn_index 排序返回。"""
        turns_dir = self._dir / session_id / "turns"
        if not turns_dir.exists():
            return []
        turns = []
        for f in sorted(turns_dir.glob("*.json")):
            try:
                turns.append(_read_json(f))
            except Exception:
                pass
        turns.sort(key=lambda t: t.get("turn_index", 0))
        return turns

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
        _atomic_write(index_path, json.dumps(self._index, ensure_ascii=False, indent=2))


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def _atomic_write(path: Path, content: str) -> None:
    """原子写入：先写 .tmp 再 rename。"""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.rename(path)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl_tail(path: Path, max_events: int) -> list[dict]:
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    events = []
    for ln in lines:
        try:
            events.append(json.loads(ln))
        except json.JSONDecodeError:
            pass
    return events[-max_events:]


def _update_meta_title(meta_path: Path, title: str, ts: str) -> None:
    """同步更新 meta.json 的 title 和 last_active（在锁内调用）。"""
    if not meta_path.exists():
        return
    try:
        meta = _read_json(meta_path)
        meta["title"] = title
        meta["last_active"] = ts
        _atomic_write(meta_path, json.dumps(meta, ensure_ascii=False, indent=2))
    except Exception:
        pass
