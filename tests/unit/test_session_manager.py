"""T5-2 验证：SessionManager 单元测试（真实文件系统）"""
from __future__ import annotations

import asyncio
import json

from sunday.gateway.session import SessionManager


async def test_new_session_returns_id(tmp_path):
    """new_session 返回 12 位 hex 字符串"""
    sm = SessionManager(tmp_path)
    sid = sm.new_session()
    assert len(sid) == 12
    assert all(c in "0123456789abcdef" for c in sid)


async def test_new_session_creates_jsonl(tmp_path):
    """new_session 创建 sessions/<id>.jsonl 文件"""
    sm = SessionManager(tmp_path)
    sid = sm.new_session()
    assert (tmp_path / f"{sid}.jsonl").exists()


async def test_append_writes_jsonl(tmp_path):
    """append 后文件包含该事件的 JSON 行"""
    from sunday.gateway.protocol import EventType
    sm = SessionManager(tmp_path)
    sid = sm.new_session()
    await sm.append(sid, EventType.STATUS, {"state": "thinking"})
    lines = (tmp_path / f"{sid}.jsonl").read_text().splitlines()
    # 至少有 session_start + 新事件
    events = [json.loads(line) for line in lines if line.strip()]
    types = [e["type"] for e in events]
    assert "status" in types


async def test_append_concurrent_safe(tmp_path):
    """并发 50 次 append 后行数精确等于 50 + session_start(1)"""
    from sunday.gateway.protocol import EventType
    sm = SessionManager(tmp_path)
    sid = sm.new_session()
    tasks = [sm.append(sid, EventType.STREAM, {"delta": str(i)}) for i in range(50)]
    await asyncio.gather(*tasks)
    lines = [ln for ln in (tmp_path / f"{sid}.jsonl").read_text().splitlines() if ln.strip()]
    assert len(lines) == 51  # 1 session_start + 50 stream


async def test_load_history_returns_events(tmp_path):
    """load_history 返回已追加的事件列表"""
    from sunday.gateway.protocol import EventType
    sm = SessionManager(tmp_path)
    sid = sm.new_session()
    await sm.append(sid, EventType.STATUS, {"state": "idle"})
    history = sm.load_history(sid)
    assert len(history) >= 1
    types = [e["type"] for e in history]
    assert "status" in types


async def test_load_history_max_events(tmp_path):
    """max_events 参数正确截断返回行数"""
    from sunday.gateway.protocol import EventType
    sm = SessionManager(tmp_path)
    sid = sm.new_session()
    for i in range(20):
        await sm.append(sid, EventType.STREAM, {"delta": str(i)})
    history = sm.load_history(sid, max_events=5)
    assert len(history) == 5


async def test_list_sessions_sorted_by_last_active(tmp_path):
    """list_sessions 按 last_active 倒序"""
    sm = SessionManager(tmp_path)
    s1 = sm.new_session()
    await asyncio.sleep(0.01)
    s2 = sm.new_session()
    sessions = sm.list_sessions()
    ids = [s["session_id"] for s in sessions]
    # s2 更新，排前面
    assert ids.index(s2) < ids.index(s1)


async def test_index_json_updated_on_new_session(tmp_path):
    """new_session 后 index.json 包含该 session"""
    sm = SessionManager(tmp_path)
    sid = sm.new_session()
    index_path = tmp_path / "index.json"
    assert index_path.exists()
    data = json.loads(index_path.read_text())
    ids = [s["session_id"] for s in data]
    assert sid in ids


async def test_load_history_missing_session_returns_empty(tmp_path):
    """不存在的 session 返回空列表"""
    sm = SessionManager(tmp_path)
    result = sm.load_history("nonexistent000")
    assert result == []
