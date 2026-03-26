"""T5-3 验证：Gateway Server 集成测试（真实 WebSocket，mock AgentLoop）"""
from __future__ import annotations

import asyncio
import json
import os
from unittest.mock import patch

import websockets
import yaml

from sunday.gateway.protocol import EventType, Message


def _make_settings(tmp_path):
    from sunday.config import Settings
    config_file = tmp_path / "agent.yaml"
    config_file.write_text(yaml.dump({
        "model": {"provider": "anthropic", "id": "claude-test", "max_tokens": 4096},
        "agent": {
            "workspace": str(tmp_path / "workspace"),
            "sessions_dir": str(tmp_path / "sessions"),
        },
    }))
    with patch.dict(os.environ, {
        "ANTHROPIC_API_KEY": "sk-ant-fake",
        "SUNDAY_CONFIG_FILE": str(config_file),
    }):
        return Settings()


async def _start_gateway(tmp_path, mock_loop_run=None):
    """启动 Gateway，返回 (gateway, port)。mock_loop_run 替换 AgentLoop.run。"""
    from sunday.gateway.server import Gateway
    settings = _make_settings(tmp_path)
    gw = Gateway(settings)

    if mock_loop_run is not None:
        gw._mock_loop_run = mock_loop_run

    port = await gw.start_test()  # 绑定随机端口，不阻塞
    return gw, port


async def _send(ws, msg: Message):
    await ws.send(msg.to_json())


async def _recv(ws, timeout=2.0) -> dict:
    raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
    return json.loads(raw)


# ── 连接 ──────────────────────────────────────────────────────────────────────

async def test_gateway_starts_and_accepts_connection(tmp_path):
    """Gateway 启动后可建立 WebSocket 连接"""
    gw, port = await _start_gateway(tmp_path)
    try:
        async with websockets.connect(f"ws://localhost:{port}") as ws:
            # websockets 16+ 用 ws.state 检查，简单 ping 验证连通即可
            pong = await ws.ping()
            assert pong is not None
    finally:
        await gw.stop()


# ── send 消息触发 AgentLoop ───────────────────────────────────────────────────

async def test_send_message_triggers_agent_loop(tmp_path):
    """发送 send 消息后 AgentLoop.run 被调用"""
    loop_called = asyncio.Event()

    async def fake_run(state):
        loop_called.set()
        return "完成"

    gw, port = await _start_gateway(tmp_path, mock_loop_run=fake_run)
    try:
        async with websockets.connect(f"ws://localhost:{port}") as ws:
            sid = "testsession1"
            await _send(ws, Message(type=EventType.SEND, session_id=sid,
                                    data={"content": "你好"}))
            await asyncio.wait_for(loop_called.wait(), timeout=3.0)
            assert loop_called.is_set()
    finally:
        await gw.stop()


# ── abort ─────────────────────────────────────────────────────────────────────

async def test_abort_cancels_task(tmp_path):
    """发送 abort 后运行中的 task 被取消"""
    running = asyncio.Event()
    cancelled = asyncio.Event()

    async def slow_run(state):
        running.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return "done"

    gw, port = await _start_gateway(tmp_path, mock_loop_run=slow_run)
    try:
        async with websockets.connect(f"ws://localhost:{port}") as ws:
            sid = "abortsess01"
            await _send(ws, Message(type=EventType.SEND, session_id=sid,
                                    data={"content": "慢任务"}))
            await asyncio.wait_for(running.wait(), timeout=3.0)
            await _send(ws, Message(type=EventType.ABORT, session_id=sid))
            await asyncio.wait_for(cancelled.wait(), timeout=3.0)
            assert cancelled.is_set()
    finally:
        await gw.stop()


# ── 同 session 串行 ───────────────────────────────────────────────────────────

async def test_serial_same_session(tmp_path):
    """同一 session 有任务运行时第二条 send 被拒绝"""
    blocking = asyncio.Event()

    async def blocking_run(state):
        await blocking.wait()
        return "done"

    gw, port = await _start_gateway(tmp_path, mock_loop_run=blocking_run)
    try:
        async with websockets.connect(f"ws://localhost:{port}") as ws:
            sid = "serialsess1"
            await _send(ws, Message(type=EventType.SEND, session_id=sid,
                                    data={"content": "第一条"}))
            await asyncio.sleep(0.1)
            await _send(ws, Message(type=EventType.SEND, session_id=sid,
                                    data={"content": "第二条"}))
            # 应收到 busy/error 状态
            msg = await _recv(ws, timeout=2.0)
            assert msg.get("type") in ("status", "error")
            blocking.set()
    finally:
        await gw.stop()


# ── emit 推送客户端 ───────────────────────────────────────────────────────────

async def test_emit_pushes_to_client(tmp_path):
    """emit 回调触发后客户端收到对应消息"""
    emit_fn = None

    async def capture_emit_run(state):
        nonlocal emit_fn
        # 通过 state 拿到 emit，实际通过 gateway 注入
        return "done"

    gw, port = await _start_gateway(tmp_path)
    try:
        async with websockets.connect(f"ws://localhost:{port}") as ws:
            sid = "emitsess001"
            # 先建立连接（发个 send 消息）
            async def instant_run(state):
                await gw.emit(sid, EventType.STATUS, {"state": "thinking"})
                return "done"
            gw._mock_loop_run = instant_run

            await _send(ws, Message(type=EventType.SEND, session_id=sid,
                                    data={"content": "test"}))
            # 等待收到 status 消息
            received = []
            for _ in range(5):
                try:
                    msg = await _recv(ws, timeout=1.0)
                    received.append(msg)
                    if msg.get("type") == "status":
                        break
                except asyncio.TimeoutError:
                    break
            types = [m.get("type") for m in received]
            assert "status" in types
    finally:
        await gw.stop()


# ── confirm 解析 Future ───────────────────────────────────────────────────────

async def test_confirm_resolves_future(tmp_path):
    """发送 confirm 消息后 pending Future 被 resolve"""
    confirmed_value = None

    async def confirm_run(state):
        nonlocal confirmed_value
        result = await gw.request_confirm("danger_tool", {}, state.session_id)
        confirmed_value = result
        return "done"

    gw, port = await _start_gateway(tmp_path, mock_loop_run=confirm_run)
    try:
        async with websockets.connect(f"ws://localhost:{port}") as ws:
            sid = "confirmsess1"
            await _send(ws, Message(type=EventType.SEND, session_id=sid,
                                    data={"content": "测试确认"}))
            # 等待 confirm_request 消息
            for _ in range(10):
                msg = await _recv(ws, timeout=2.0)
                if msg.get("type") == "confirm_request":
                    break
            # 回复确认
            await _send(ws, Message(type=EventType.CONFIRM, session_id=sid,
                                    data={"confirmed": True}))
            await asyncio.sleep(0.3)
            assert confirmed_value is True
    finally:
        await gw.stop()


# ── slash /new ────────────────────────────────────────────────────────────────

async def test_slash_new_creates_session(tmp_path):
    """/new slash 命令返回新 session_id"""
    gw, port = await _start_gateway(tmp_path)
    try:
        async with websockets.connect(f"ws://localhost:{port}") as ws:
            sid = "slashsess001"
            await _send(ws, Message(type=EventType.SLASH, session_id=sid,
                                    data={"command": "new", "args": ""}))
            msg = await _recv(ws, timeout=2.0)
            # 应收到含 new session_id 的状态消息
            assert msg.get("type") in ("status", "done", "slash_result")
    finally:
        await gw.stop()
