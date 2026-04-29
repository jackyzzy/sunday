"""`请继续` 端到端：Service 从本 session stream 解析原任务并改写 task。"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import websockets
import yaml

from sunday.service.protocol import EventType, Message


def _prepare_configs(tmp_path: Path) -> Path:
    configs_dir = tmp_path / "configs"
    prompts_dir = configs_dir / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    (configs_dir / "agent.yaml").write_text(yaml.dump({
        "model": {"provider": "anthropic", "id": "claude-test", "max_tokens": 4096},
        "agent": {
            "workspace_dir": str(tmp_path / "workspace"),
            "sessions_dir": str(tmp_path / "sessions"),
        },
    }))
    real_prompt = Path(__file__).parent.parent.parent / "configs" / "prompts" / "thread_update.md"
    shutil.copy(real_prompt, prompts_dir / "thread_update.md")
    return configs_dir


def _make_settings(tmp_path: Path):
    from sunday.config import Settings

    from tests.conftest import seed_workspace
    configs_dir = _prepare_configs(tmp_path)
    seed_workspace(tmp_path / "workspace")
    with patch.dict(os.environ, {
        "ANTHROPIC_API_KEY": "sk-ant-fake",
        "SUNDAY_CONFIGS_DIR": str(configs_dir),
    }, clear=False):
        s = Settings()
        _ = s.sunday
        return s


async def _start_service(tmp_path: Path, mock_loop_run):
    from sunday.service.server import SundayService
    settings = _make_settings(tmp_path)
    gw = SundayService(settings)
    gw._mock_loop_run = mock_loop_run
    port = await gw.start_test()
    return gw, port


async def _recv_until(ws, target_type: str, timeout: float = 3.0) -> dict:
    async def _loop():
        while True:
            raw = await ws.recv()
            msg = json.loads(raw)
            if msg.get("type") == target_type:
                return msg
    return await asyncio.wait_for(_loop(), timeout=timeout)


async def _wait_task_finished(gw, session_id: str):
    task = gw._running_tasks.get(session_id)
    if task is None:
        return
    try:
        await asyncio.wait_for(task, timeout=5.0)
    except Exception:
        pass


def _seed_failed_session(
    sessions_dir: Path, session_id: str, title: str, original_task: str
) -> None:
    """手工构造一个"首轮失败"的 session 目录。"""
    sdir = sessions_dir / session_id
    (sdir / "turns").mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    meta = {
        "session_id": session_id,
        "created_at": now,
        "last_active": now,
        "title": title,
        "turn_count": 1,
        "turns": [{
            "turn_id": "abc00001",
            "turn_index": 1,
            "ts_start": now,
            "ts_end": now,
            "outcome": "error",
        }],
    }
    (sdir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    events = [
        {"type": "session_start", "session_id": session_id,
         "turn_id": None, "data": {}, "ts": now},
        {"type": "turn_start", "session_id": session_id,
         "turn_id": "abc00001", "data": {"content": original_task}, "ts": now},
        {"type": "send", "session_id": session_id,
         "turn_id": "abc00001", "data": {"content": original_task}, "ts": now},
        {"type": "error", "session_id": session_id,
         "turn_id": "abc00001",
         "data": {"message": "Server disconnected without sending a response."},
         "ts": now},
        {"type": "done", "session_id": session_id,
         "turn_id": "abc00001", "data": {"content": ""}, "ts": now},
        {"type": "turn_end", "session_id": session_id,
         "turn_id": "abc00001", "data": {"outcome": "error"}, "ts": now},
    ]
    (sdir / "stream.jsonl").write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n",
        encoding="utf-8",
    )


async def test_continuation_rewrites_task_from_session_stream(tmp_path: Path):
    """主修复：'请继续' 触发 resolve_continuation → state.task 改写成原任务+说明。"""
    captured = {}

    async def fake_run(state):
        captured["task"] = state.task
        captured["session_thread"] = state.session_thread
        return "（mock 完成）"

    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    sid = "aa7af48e-4bcc-44e9-b4d6-660107917d01"
    original = "帮我分析一下，自变量这家公司怎么样，是否值得进入？"
    _seed_failed_session(sessions_dir, sid, title=original[:40], original_task=original)

    thread_mock = AsyncMock(return_value="")
    with patch("sunday.agent.llm_client.LLMClient.call_text", new=thread_mock):
        gw, port = await _start_service(tmp_path, fake_run)
        try:
            async with websockets.connect(f"ws://localhost:{port}") as ws:
                await ws.send(Message(
                    type=EventType.SEND,
                    session_id=sid,
                    data={"content": "请继续"},
                ).to_json())
                await _recv_until(ws, "done", timeout=3.0)
                await _wait_task_finished(gw, sid)
        finally:
            await gw.stop()

    assert captured["task"].startswith(original)
    assert "前一次未完成任务的继续" in captured["task"]
    assert "Server disconnected" in captured["task"]
    # session_thread 兜底注入应包含 title
    assert captured["session_thread"] is not None
    assert "自变量" in captured["session_thread"].summary


async def test_continuation_empty_session_emits_needs_input(tmp_path: Path):
    """空 session + 请继续 → 收到 STATUS needs_input，AgentLoop.run 不被调用。"""
    called = {"count": 0}

    async def fake_run(state):
        called["count"] += 1
        return ""

    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    sid = "emptysess01"

    gw, port = await _start_service(tmp_path, fake_run)
    try:
        async with websockets.connect(f"ws://localhost:{port}") as ws:
            await ws.send(Message(
                type=EventType.SEND,
                session_id=sid,
                data={"content": "请继续"},
            ).to_json())
            status = await _recv_until(ws, "status", timeout=3.0)
    finally:
        await gw.stop()

    assert status["data"]["state"] == "needs_input"
    assert called["count"] == 0


async def test_substantive_task_bypasses_continuation_rewrite(tmp_path: Path):
    """对照：有实质内容的任务不触发改写。"""
    captured = {}

    async def fake_run(state):
        captured["task"] = state.task
        return "ok"

    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    sid = "realtask001"
    _seed_failed_session(sessions_dir, sid, title="旧任务", original_task="旧任务")

    thread_mock = AsyncMock(return_value="")
    with patch("sunday.agent.llm_client.LLMClient.call_text", new=thread_mock):
        gw, port = await _start_service(tmp_path, fake_run)
        try:
            async with websockets.connect(f"ws://localhost:{port}") as ws:
                await ws.send(Message(
                    type=EventType.SEND,
                    session_id=sid,
                    data={"content": "分析小红书的商业化路径"},
                ).to_json())
                await _recv_until(ws, "done", timeout=3.0)
                await _wait_task_finished(gw, sid)
        finally:
            await gw.stop()

    assert captured["task"] == "分析小红书的商业化路径"
