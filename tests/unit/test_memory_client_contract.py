"""MemoryClient 契约测试 — 跑 LocalMemoryClient 实现。

未来加入 HttpMemoryClient 时把 fixture 参数化即可同时覆盖两实现。

覆盖范围：
- sessions：create / ensure / list / append_event / write_turn /
  load_events / load_turns / save_thread / delete（级联 logs/）
- knowledge：append_daily / upsert_fact / upsert_user_profile /
  read_layer / read_daily
- workspace：read / read_runtime_rules / list_skills
- 并发回归：两个 client 实例并发写不丢条目（修复 index 覆盖 bug）
"""
from __future__ import annotations

import asyncio
import json
from datetime import date

import pytest

from sunday.agent.models import SessionThread
from sunday.memory.local import LocalMemoryClient
from sunday.memory.models import LogEvent, SessionEvent, TurnRecord


@pytest.fixture
def memory_client(tmp_path):
    """为每个测试构造独立的 LocalMemoryClient（关闭 janitor 后台任务）。"""
    client = LocalMemoryClient(
        sessions_dir=tmp_path / "sessions",
        memory_dir=tmp_path / "memory",
        log_dir=tmp_path / "logs",
        workspace_dir=tmp_path / "workspace",
        skills_dir=tmp_path / "skills",
        retention_days=30,
        run_janitor=False,
    )
    yield client


# ── sessions ───────────────────────────────────────────────────────────────


async def test_sessions_create_returns_meta(memory_client):
    meta = await memory_client.sessions.create()
    assert meta.session_id
    assert meta.turn_count == 0
    assert meta.created_at  # ISO timestamp


async def test_sessions_create_with_explicit_id(memory_client):
    meta = await memory_client.sessions.create(session_id="explicit-001")
    assert meta.session_id == "explicit-001"


async def test_sessions_ensure_idempotent(memory_client):
    a = await memory_client.sessions.ensure("sid-1")
    b = await memory_client.sessions.ensure("sid-1")
    assert a.session_id == b.session_id == "sid-1"


async def test_sessions_get_returns_none_when_missing(memory_client):
    assert await memory_client.sessions.get("does-not-exist") is None


async def test_sessions_list_sorted_by_last_active(memory_client):
    await memory_client.sessions.ensure("a")
    await asyncio.sleep(0.01)
    await memory_client.sessions.ensure("b")
    sessions = await memory_client.sessions.list()
    ids = [s.session_id for s in sessions]
    assert ids.index("b") < ids.index("a")


async def test_sessions_append_event_writes_stream(memory_client, tmp_path):
    await memory_client.sessions.ensure("sid")
    ev = SessionEvent(type="status", session_id="sid", turn_id="t1", data={"state": "thinking"})
    await memory_client.sessions.append_event("sid", ev)
    stream = (tmp_path / "sessions" / "sid" / "stream.jsonl").read_text()
    types = [json.loads(ln)["type"] for ln in stream.splitlines() if ln.strip()]
    assert "status" in types


async def test_sessions_append_send_sets_title(memory_client):
    await memory_client.sessions.ensure("sid")
    ev = SessionEvent(type="send", session_id="sid", data={"content": "你好世界"})
    await memory_client.sessions.append_event("sid", ev)
    meta = await memory_client.sessions.get("sid")
    assert meta.title == "你好世界"


async def test_sessions_write_turn_updates_meta_and_index(memory_client, tmp_path):
    await memory_client.sessions.ensure("sid")
    turn = TurnRecord(turn_id="t1", outcome="success",
                      ts_start="2026-04-27T00:00:00", ts_end="2026-04-27T00:01:00")
    await memory_client.sessions.write_turn("sid", turn)

    turn_path = tmp_path / "sessions" / "sid" / "turns" / "t1.json"
    assert turn_path.exists()
    saved = json.loads(turn_path.read_text())
    assert saved["turn_index"] == 1

    meta = await memory_client.sessions.get("sid")
    assert meta.turn_count == 1
    assert meta.last_turn_outcome == "success"


async def test_sessions_load_events_tail(memory_client):
    await memory_client.sessions.ensure("sid")
    for i in range(20):
        ev = SessionEvent(type="stream", session_id="sid", data={"i": i})
        await memory_client.sessions.append_event("sid", ev)
    events = await memory_client.sessions.load_events("sid", max_events=5)
    assert len(events) == 5


async def test_sessions_load_turns_sorted(memory_client):
    await memory_client.sessions.ensure("sid")
    await memory_client.sessions.write_turn(
        "sid", TurnRecord(turn_id="t1", outcome="success"),
    )
    await memory_client.sessions.write_turn(
        "sid", TurnRecord(turn_id="t2", outcome="success"),
    )
    turns = await memory_client.sessions.load_turns("sid")
    assert [t.turn_id for t in turns] == ["t1", "t2"]


async def test_sessions_save_and_get_thread(memory_client):
    await memory_client.sessions.ensure("sid")
    thread = SessionThread(summary="主线", key_entities=["A", "B"], updated_at_turn="t1")
    await memory_client.sessions.save_thread("sid", thread)
    loaded = await memory_client.sessions.get_thread("sid")
    assert loaded is not None
    assert loaded.summary == "主线"
    assert loaded.key_entities == ["A", "B"]


async def test_sessions_delete_cascades_logs(memory_client, tmp_path):
    await memory_client.sessions.ensure("sid")
    await memory_client.logs.emit("sid", LogEvent(event="session_start",
                                                   session_id="sid", data={}))
    log_path = tmp_path / "logs" / "sid.jsonl"
    assert log_path.exists()

    await memory_client.sessions.delete("sid")
    assert not (tmp_path / "sessions" / "sid").exists()
    assert not log_path.exists()
    index = json.loads((tmp_path / "sessions" / "index.json").read_text())
    assert all(e["session_id"] != "sid" for e in index)


async def test_sessions_reset_clears_turns_keeps_meta(memory_client, tmp_path):
    await memory_client.sessions.ensure("sid")
    await memory_client.sessions.write_turn(
        "sid", TurnRecord(turn_id="t1", outcome="success"),
    )
    await memory_client.sessions.reset("sid")
    assert list((tmp_path / "sessions" / "sid" / "turns").glob("*.json")) == []
    meta = await memory_client.sessions.get("sid")
    assert meta is not None
    assert meta.turn_count == 0


async def test_sessions_report_dir_path_only(memory_client, tmp_path):
    p = memory_client.sessions.report_dir("sid", "t1")
    assert p == tmp_path / "sessions" / "sid" / "reports" / "t1"
    assert not p.exists()  # 派生路径不创建目录


# ── 并发回归（修复 index.json 覆盖 bug）──────────────────────────────────


async def test_concurrent_clients_do_not_lose_index_entries(tmp_path):
    """两个 client 实例并发改 index，最终所有变更都应保留。"""
    client_a = LocalMemoryClient(
        sessions_dir=tmp_path / "s",
        memory_dir=tmp_path / "m",
        log_dir=tmp_path / "l",
        workspace_dir=tmp_path / "w",
        run_janitor=False,
    )
    client_b = LocalMemoryClient(
        sessions_dir=tmp_path / "s",
        memory_dir=tmp_path / "m",
        log_dir=tmp_path / "l",
        workspace_dir=tmp_path / "w",
        run_janitor=False,
    )
    # client_a 创建 1 个生产 session
    await client_a.sessions.create(session_id="prod-1")
    # client_b 并发创建 5 个测试 session
    await asyncio.gather(*[
        client_b.sessions.create(session_id=f"test-{i}") for i in range(5)
    ])
    # client_a 删除 prod-1
    await client_a.sessions.delete("prod-1")

    # 关键断言：测试 session 全部还在 index.json
    final_index = json.loads((tmp_path / "s" / "index.json").read_text())
    ids = {e["session_id"] for e in final_index}
    assert ids == {f"test-{i}" for i in range(5)}


# ── knowledge ──────────────────────────────────────────────────────────────


async def test_knowledge_append_daily_creates_file(memory_client, tmp_path):
    await memory_client.knowledge.append_daily("第一条日志\n")
    today = date.today().isoformat()
    path = tmp_path / "memory" / "daily" / f"{today}.md"
    assert path.exists()
    assert "第一条日志" in path.read_text()


async def test_knowledge_upsert_fact_creates_section(memory_client, tmp_path):
    await memory_client.knowledge.upsert_fact("用户偏好", "语言", "中文")
    content = (tmp_path / "memory" / "MEMORY.md").read_text()
    assert "## 用户偏好" in content
    assert "语言：中文" in content


async def test_knowledge_upsert_fact_updates_existing(memory_client, tmp_path):
    await memory_client.knowledge.upsert_fact("用户偏好", "语言", "中文")
    await memory_client.knowledge.upsert_fact("用户偏好", "语言", "英文")
    content = (tmp_path / "memory" / "MEMORY.md").read_text()
    assert content.count("语言：") == 1
    assert "英文" in content


async def test_knowledge_upsert_user_profile(memory_client, tmp_path):
    await memory_client.knowledge.upsert_user_profile("姓名", "张三")
    await memory_client.knowledge.upsert_user_profile("姓名", "李四")
    content = (tmp_path / "memory" / "USER.md").read_text()
    assert content.count("姓名：") == 1
    assert "李四" in content


async def test_knowledge_read_layer(memory_client):
    await memory_client.knowledge.upsert_fact("S", "K", "V")
    text = await memory_client.knowledge.read_layer("MEMORY")
    assert "K：V" in text


# ── workspace ──────────────────────────────────────────────────────────────


async def test_workspace_read_returns_empty_when_missing(memory_client):
    text = await memory_client.workspace.read("SOUL")
    assert text == ""


async def test_workspace_read_returns_content(memory_client, tmp_path):
    (tmp_path / "workspace" / "SOUL.md").write_text("# Soul")
    text = await memory_client.workspace.read("SOUL")
    assert "# Soul" in text


async def test_workspace_runtime_rules_falls_back_to_builtin(memory_client):
    rules = await memory_client.workspace.read_runtime_rules()
    assert rules.realtime_keywords  # 内置非空
    assert rules.subject_min_output_chars == 200


async def test_workspace_runtime_rules_parses_user_file(memory_client, tmp_path):
    (tmp_path / "workspace" / "RUNTIME_RULES.md").write_text(
        "## 时间敏感关键词\n\n阿尔法, 贝塔\n\n"
        "## 主题敏感最小输出长度\n\n500\n"
    )
    rules = await memory_client.workspace.read_runtime_rules()
    assert rules.realtime_keywords == ["阿尔法", "贝塔"]
    assert rules.subject_min_output_chars == 500


async def test_workspace_list_skills(memory_client, tmp_path):
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "alpha").mkdir()
    (skills / "beta").mkdir()
    (skills / "ignored.txt").write_text("not a skill dir")
    found = await memory_client.workspace.list_skills()
    assert found == ["alpha", "beta"]


# ── logs ───────────────────────────────────────────────────────────────────


async def test_logs_emit_and_read(memory_client):
    await memory_client.logs.emit("sid", LogEvent(
        event="session_start", session_id="sid", data={"task": "x"},
    ))
    await memory_client.logs.emit("sid", LogEvent(
        event="session_end", session_id="sid", data={"outcome": "success"},
    ))
    events = await memory_client.logs.read("sid")
    assert [e.event for e in events] == ["session_start", "session_end"]


async def test_logs_delete(memory_client, tmp_path):
    await memory_client.logs.emit("sid", LogEvent(
        event="x", session_id="sid", data={},
    ))
    assert (tmp_path / "logs" / "sid.jsonl").exists()
    await memory_client.logs.delete("sid")
    assert not (tmp_path / "logs" / "sid.jsonl").exists()


# ── 资源清理 ───────────────────────────────────────────────────────────────


async def test_aclose_cancels_background_tasks(tmp_path):
    """aclose() 不应抛异常；janitor 任务被取消。"""
    client = LocalMemoryClient(
        sessions_dir=tmp_path / "s",
        memory_dir=tmp_path / "m",
        log_dir=tmp_path / "l",
        workspace_dir=tmp_path / "w",
        run_janitor=True,
    )
    await client.aclose()
    assert client.knowledge._janitor_task is None
