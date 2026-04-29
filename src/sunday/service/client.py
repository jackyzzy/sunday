"""Service WebSocket 客户端 —— TUI / CLI 共用的瘦客户端。

CLI 用法（典型）：
    spawn_service_if_needed(port=7899)  # 自动起 daemon
    async with ServiceClient(port=7899) as client:
        async for ev in client.submit_task(session_id, task, thinking_level):
            handle(ev)

测试场景：设置 SUNDAY_EMBED=1，CLI 不 spawn 子进程，而由调用方在同进程内
启动 SundayService.start_test() 占用同一端口。
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import websockets

from sunday.service.protocol import EventType, Message

logger = logging.getLogger(__name__)


_TERMINAL_EVENTS = {EventType.DONE, EventType.ERROR}


def service_pid_file() -> Path:
    """共享的 PID 文件路径（与 cli.py:_service_pid_file 一致）。"""
    return Path.home() / ".sunday" / "service.pid"


def is_service_running(port: int = 7899, timeout: float = 0.3) -> bool:
    """判断 service 是否在跑：PID 文件 + 进程存在 + 端口可连。"""
    pid_file = service_pid_file()
    if not pid_file.exists():
        return False
    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, 0)
    except (ProcessLookupError, ValueError, OSError):
        return False

    # 端口可连
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(("localhost", port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def spawn_service_if_needed(port: int = 7899, wait_seconds: float = 5.0) -> bool:
    """检测 service 是否在跑，不在则后台 spawn 并等其就绪。

    返回 True 表示运行中（已在跑或新 spawn 成功）；False 表示 spawn 失败。
    """
    if is_service_running(port):
        return True

    pid_file = service_pid_file()
    pid_file.parent.mkdir(parents=True, exist_ok=True)

    proc = subprocess.Popen(
        [sys.executable, "-m", "sunday.service", "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    pid_file.write_text(str(proc.pid))
    logger.info("Service 后台启动（PID=%d，端口=%d），等待就绪...", proc.pid, port)

    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        if is_service_running(port):
            return True
        time.sleep(0.2)
    logger.error("Service 启动超时（%.1fs），请查看 ~/.sunday/logs/service.log", wait_seconds)
    return False


class ServiceClient:
    """瘦 WebSocket 客户端：发任务、接事件流、抽象不可逆操作的确认。"""

    def __init__(self, port: int = 7899) -> None:
        self._url = f"ws://localhost:{port}"
        self._ws: websockets.ClientConnection | None = None

    async def __aenter__(self) -> "ServiceClient":
        self._ws = await websockets.connect(self._url, ping_interval=None)
        return self

    async def __aexit__(self, *_exc) -> None:
        if self._ws is not None:
            await self._ws.close()

    async def submit_task(
        self,
        session_id: str,
        task: str,
        thinking_level: str = "medium",
        model_override: str | None = None,
        auto_confirm: bool = False,
    ) -> AsyncIterator[Message]:
        """发起任务，yield 服务端推送的每个事件直到 DONE/ERROR。

        auto_confirm=True 时遇到 CONFIRM_REQUEST 自动回复 confirmed=true（CLI --yes）。
        """
        if self._ws is None:
            raise RuntimeError("ServiceClient 未连接，请用 async with")

        send_msg = Message(
            type=EventType.SEND,
            session_id=session_id,
            data={
                "content": task,
                "thinking_level": thinking_level,
                "model_override": model_override,
            },
        )
        await self._ws.send(send_msg.to_json())

        async for raw in self._ws:
            try:
                msg = Message.from_json(raw)
            except Exception as e:
                logger.warning("Service 推送解析失败：%s", e)
                continue

            # 处理服务端发起的不可逆操作确认请求
            if msg.type == EventType.CONFIRM_REQUEST:
                confirmed = auto_confirm
                if not auto_confirm:
                    # 由调用方拿到事件后通过 send_confirm() 回复；这里仍 yield 出去
                    yield msg
                    continue
                await self._ws.send(Message(
                    type=EventType.CONFIRM,
                    session_id=session_id,
                    data={"confirmed": confirmed},
                ).to_json())
                continue

            yield msg

            if msg.type in _TERMINAL_EVENTS:
                break

    async def send_abort(self, session_id: str) -> None:
        if self._ws is None:
            return
        await self._ws.send(Message(
            type=EventType.ABORT, session_id=session_id,
        ).to_json())

    async def send_confirm(self, session_id: str, confirmed: bool) -> None:
        if self._ws is None:
            return
        await self._ws.send(Message(
            type=EventType.CONFIRM,
            session_id=session_id,
            data={"confirmed": confirmed},
        ).to_json())


def new_session_id() -> str:
    """8 位 hex（与原 cli.py 行为一致）。"""
    return uuid.uuid4().hex[:8]
