"""T5-2 验证：SessionManager 单元测试（真实文件系统）

新目录结构（每 session 一个子目录）：
    sessions/
    ├── index.json
    └── {session_id}/
        ├── meta.json
        ├── stream.jsonl
        └── turns/
"""
from __future__ import annotations

import asyncio
import json

from sunday.gateway.session import SessionManager


async def test_new_session_returns_id(tmp_path):
    """new_session 返回标准 UUID 字符串（含连字符，36 位）"""
    import uuid
    sm = SessionManager(tmp_path)
    sid = sm.new_session()
    assert len(sid) == 36
    uuid.UUID(sid)  # 合法 UUID 不抛异常


async def test_new_session_creates_directory(tmp_path):
    """new_session 创建 sessions/{id}/ 目录（含 meta.json、stream.jsonl、turns/）"""
    sm = SessionManager(tmp_path)
    sid = sm.new_session()
    session_dir = tmp_path / sid
    assert session_dir.is_dir()
    assert (session_dir / "meta.json").exists()
    assert (session_dir / "stream.jsonl").exists()
    assert (session_dir / "turns").is_dir()


async def test_new_session_meta_json_fields(tmp_path):
    """meta.json 包含预期字段"""
    sm = SessionManager(tmp_path)
    sid = sm.new_session()
    meta = json.loads((tmp_path / sid / "meta.json").read_text())
    assert meta["session_id"] == sid
    assert meta["turn_count"] == 0
    assert "created_at" in meta


async def test_append_stream_writes_event(tmp_path):
    """append_stream 追加事件到 stream.jsonl"""
    from sunday.gateway.protocol import EventType
    sm = SessionManager(tmp_path)
    sid = sm.new_session()
    await sm.append_stream(sid, EventType.STATUS, {"state": "thinking"}, turn_id="abc")
    lines = (tmp_path / sid / "stream.jsonl").read_text().splitlines()
    events = [json.loads(ln) for ln in lines if ln.strip()]
    types = [e["type"] for e in events]
    assert "status" in types
    # 验证 turn_id 字段存在
    status_event = next(e for e in events if e["type"] == "status")
    assert status_event["turn_id"] == "abc"


async def test_append_backward_compat(tmp_path):
    """旧 append() 接口委托给 append_stream()，不报错"""
    from sunday.gateway.protocol import EventType
    sm = SessionManager(tmp_path)
    sid = sm.new_session()
    await sm.append(sid, EventType.STATUS, {"state": "thinking"})
    history = sm.load_history(sid)
    types = [e["type"] for e in history]
    assert "status" in types


async def test_append_concurrent_safe(tmp_path):
    """并发 50 次 append_stream 后行数精确等于 50 + session_start(1)"""
    from sunday.gateway.protocol import EventType
    sm = SessionManager(tmp_path)
    sid = sm.new_session()
    tasks = [sm.append_stream(sid, EventType.STREAM, {"delta": str(i)}) for i in range(50)]
    await asyncio.gather(*tasks)
    lines = [ln for ln in (tmp_path / sid / "stream.jsonl").read_text().splitlines() if ln.strip()]
    assert len(lines) == 51  # 1 session_start + 50 stream


async def test_write_turn_creates_file(tmp_path):
    """write_turn 写入 turns/{turn_id}.json 并更新 meta.json"""
    sm = SessionManager(tmp_path)
    sid = sm.new_session()
    turn = {
        "turn_id": "t1ab2cd3",
        "turn_index": 1,
        "ts_start": "2026-04-17T10:00:00Z",
        "ts_end": "2026-04-17T10:01:00Z",
        "outcome": "success",
        "user_input": "测试消息",
        "plan": {"goal": "目标", "steps": []},
        "execution": [],
        "output": "回答",
    }
    await sm.write_turn(sid, turn)
    turn_path = tmp_path / sid / "turns" / "t1ab2cd3.json"
    assert turn_path.exists()
    saved = json.loads(turn_path.read_text())
    assert saved["user_input"] == "测试消息"
    # meta.json 应更新
    meta = json.loads((tmp_path / sid / "meta.json").read_text())
    assert meta["turn_count"] == 1
    assert meta["turns"][0]["turn_id"] == "t1ab2cd3"


async def test_load_history_returns_events(tmp_path):
    """load_history 返回已追加的事件列表"""
    from sunday.gateway.protocol import EventType
    sm = SessionManager(tmp_path)
    sid = sm.new_session()
    await sm.append_stream(sid, EventType.STATUS, {"state": "idle"})
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
        await sm.append_stream(sid, EventType.STREAM, {"delta": str(i)})
    history = sm.load_history(sid, max_events=5)
    assert len(history) == 5


async def test_load_history_group_by_turn(tmp_path):
    """load_history(group_by_turn=True) 返回按轮次分组的结构"""
    sm = SessionManager(tmp_path)
    sid = sm.new_session()
    for i in range(2):
        turn = {
            "turn_id": f"turn{i}",
            "turn_index": i + 1,
            "ts_start": "", "ts_end": "", "outcome": "success",
            "user_input": f"消息{i}", "plan": None, "execution": [], "output": f"回答{i}",
        }
        await sm.write_turn(sid, turn)
    turns = sm.load_history(sid, group_by_turn=True)
    assert len(turns) == 2
    assert turns[0]["user_input"] == "消息0"


async def test_load_turn_returns_dict(tmp_path):
    """load_turn 读取单个 turn 文件"""
    sm = SessionManager(tmp_path)
    sid = sm.new_session()
    turn = {
        "turn_id": "abc123",
        "turn_index": 1,
        "ts_start": "", "ts_end": "", "outcome": "success",
        "user_input": "查询", "plan": None, "execution": [], "output": "结果",
    }
    await sm.write_turn(sid, turn)
    loaded = sm.load_turn(sid, "abc123")
    assert loaded is not None
    assert loaded["output"] == "结果"


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


async def test_ensure_session_creates_directory(tmp_path):
    """ensure_session 对客户端传入的 session_id 创建目录结构（TUI 场景）"""
    sm = SessionManager(tmp_path)
    sid = "tui-client-uuid-1234"
    assert not (tmp_path / sid).exists()
    sm.ensure_session(sid)
    assert (tmp_path / sid).is_dir()
    assert (tmp_path / sid / "meta.json").exists()
    assert (tmp_path / sid / "stream.jsonl").exists()
    assert (tmp_path / sid / "turns").is_dir()
    # 幂等：再次调用不报错
    sm.ensure_session(sid)
    # 确认 index.json 包含该 session
    data = json.loads((tmp_path / "index.json").read_text())
    ids = [s["session_id"] for s in data]
    assert sid in ids


async def test_delete_session_removes_directory(tmp_path):
    """delete_session 删除整个会话目录"""
    sm = SessionManager(tmp_path)
    sid = sm.new_session()
    assert (tmp_path / sid).exists()
    sm.delete_session(sid)
    assert not (tmp_path / sid).exists()
    # index 中也不存在
    data = json.loads((tmp_path / "index.json").read_text())
    assert all(s["session_id"] != sid for s in data)


async def test_reset_session_clears_turns(tmp_path):
    """reset_session 清空 turns/ 保留 meta.json 和 stream.jsonl 首行"""
    from sunday.gateway.protocol import EventType
    sm = SessionManager(tmp_path)
    sid = sm.new_session()
    await sm.append_stream(sid, EventType.STATUS, {"state": "thinking"})
    turn = {
        "turn_id": "r1", "turn_index": 1, "ts_start": "", "ts_end": "", "outcome": "success",
        "user_input": "消息", "plan": None, "execution": [], "output": "回答",
    }
    await sm.write_turn(sid, turn)
    sm.reset_session(sid)
    # turns/ 目录为空
    assert list((tmp_path / sid / "turns").glob("*.json")) == []
    # stream.jsonl 只剩首行
    lines = [ln for ln in (tmp_path / sid / "stream.jsonl").read_text().splitlines() if ln.strip()]
    assert len(lines) == 1
    assert json.loads(lines[0])["type"] == "session_start"
