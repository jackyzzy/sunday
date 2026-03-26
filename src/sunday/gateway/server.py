"""Phase 5：Gateway Server — WebSocket 守护进程"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Callable

import websockets
from websockets.asyncio.server import serve

from sunday.gateway.protocol import EventType, Message
from sunday.gateway.session import SessionManager

if TYPE_CHECKING:
    from sunday.config import Settings

logger = logging.getLogger(__name__)

DEFAULT_PORT = 7899


class Gateway:
    """本地 WebSocket 守护进程。

    职责：
    - 接受 WebSocket 连接（session_id 映射）
    - 路由消息到 _handle_send / _handle_abort / _handle_slash / _handle_confirm
    - 管理 AgentLoop asyncio.Task 生命周期
    - 提供 emit() 回调供 AgentLoop 使用
    """

    def __init__(self, settings: "Settings") -> None:
        self._settings = settings
        cfg = settings.sunday
        sessions_dir = cfg.agent.sessions_dir
        self._session_mgr = SessionManager(sessions_dir)

        # session_id → WebSocket
        self._connections: dict[str, Any] = {}
        # session_id → asyncio.Task
        self._running_tasks: dict[str, asyncio.Task] = {}
        # session_id → Future（等待用户确认）
        self._pending_confirms: dict[str, asyncio.Future] = {}

        # 测试注入点：替换 AgentLoop.run
        self._mock_loop_run: Callable | None = None

        self._server = None

    # ── 启动 / 停止 ───────────────────────────────────────────────────────

    async def start(self, port: int = DEFAULT_PORT) -> None:
        """启动 WebSocket 服务（永久运行）。"""
        logger.info("Gateway 启动，监听 ws://localhost:%d", port)
        async with serve(self._handle, "localhost", port):
            await asyncio.Future()  # 永久挂起

    async def start_test(self, port: int = 0) -> int:
        """测试用：绑定随机端口，不阻塞，返回实际端口。"""
        self._server = await serve(self._handle, "localhost", port)
        actual_port = self._server.sockets[0].getsockname()[1]
        logger.debug("Gateway 测试模式，端口=%d", actual_port)
        return actual_port

    async def stop(self) -> None:
        """关闭服务，取消所有运行中任务。"""
        for task in list(self._running_tasks.values()):
            task.cancel()
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    # ── 消息路由 ──────────────────────────────────────────────────────────

    async def _handle(self, ws) -> None:
        """WebSocket 连接处理器：注册连接 + 路由消息。"""
        session_id = None
        try:
            async for raw in ws:
                try:
                    msg = Message.from_json(raw)
                except Exception as e:
                    logger.warning("消息解析失败：%s", e)
                    continue

                session_id = msg.session_id
                if session_id and session_id not in self._connections:
                    self._connections[session_id] = ws

                if msg.type == EventType.SEND:
                    await self._handle_send(session_id, msg.data.get("content", ""))
                elif msg.type == EventType.ABORT:
                    await self._handle_abort(session_id)
                elif msg.type == EventType.SLASH:
                    await self._handle_slash(session_id, msg.data)
                elif msg.type == EventType.CONFIRM:
                    self._handle_confirm(session_id, msg.data.get("confirmed", False))

        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            if session_id and self._connections.get(session_id) is ws:
                del self._connections[session_id]

    async def _handle_send(self, session_id: str, content: str) -> None:
        """处理用户发送消息：检查串行，创建 AgentLoop Task。"""
        # 串行检查
        if session_id in self._running_tasks and not self._running_tasks[session_id].done():
            await self.emit(session_id, EventType.STATUS, {
                "state": "busy",
                "message": "当前会话有任务运行中，请等待完成或发送 /abort 中止",
            })
            return

        # 记录用户消息
        await self._session_mgr.append(session_id, EventType.SEND, {"content": content})

        # 构建 AgentLoop 并异步运行
        from sunday.agent.models import AgentState
        state = AgentState(session_id=session_id, task=content)

        async def run_loop():
            try:
                if self._mock_loop_run is not None:
                    result = await self._mock_loop_run(state)
                else:
                    loop = self._build_agent_loop(session_id)
                    result = await loop.run(state)
                await self.emit(session_id, EventType.DONE, {"content": result or ""})
            except asyncio.CancelledError:
                await self.emit(session_id, EventType.STATUS, {"state": "aborted"})
                raise
            except Exception as e:
                logger.exception("AgentLoop 异常：%s", e)
                await self.emit(session_id, EventType.ERROR, {"message": str(e)})
            finally:
                self._running_tasks.pop(session_id, None)

        task = asyncio.create_task(run_loop())
        self._running_tasks[session_id] = task

    async def _handle_abort(self, session_id: str) -> None:
        """取消运行中的 Task。"""
        task = self._running_tasks.get(session_id)
        if task and not task.done():
            task.cancel()
            logger.info("会话 %s 任务已中止", session_id)

    async def _handle_slash(self, session_id: str, data: dict) -> None:
        """处理 Slash 命令（不走 AgentLoop）。"""
        command = data.get("command", "")
        if command == "new":
            new_sid = self._session_mgr.new_session()
            await self.emit(session_id, EventType.SLASH_RESULT, {
                "command": "new",
                "new_session_id": new_sid,
            })
        elif command == "sessions":
            sessions = self._session_mgr.list_sessions()
            await self.emit(session_id, EventType.SLASH_RESULT, {
                "command": "sessions",
                "sessions": sessions,
            })
        elif command == "history":
            history = self._session_mgr.load_history(session_id)
            await self.emit(session_id, EventType.SLASH_RESULT, {
                "command": "history",
                "events": history,
            })
        else:
            await self.emit(session_id, EventType.SLASH_RESULT, {
                "command": command,
                "message": f"未知 slash 命令：{command}",
            })

    def _handle_confirm(self, session_id: str, confirmed: bool) -> None:
        """resolve pending confirm Future。"""
        fut = self._pending_confirms.get(session_id)
        if fut and not fut.done():
            fut.set_result(confirmed)

    # ── 公开 API ──────────────────────────────────────────────────────────

    async def emit(self, session_id: str, event_type: EventType, data: dict) -> None:
        """向 session_id 对应的 WebSocket 推送消息。"""
        ws = self._connections.get(session_id)
        if ws is None:
            return
        try:
            msg = Message(type=event_type, session_id=session_id, data=data)
            await ws.send(msg.to_json())
        except Exception as e:
            logger.debug("emit 失败（session=%s）：%s", session_id, e)

    async def request_confirm(
        self, tool_name: str, arguments: dict, session_id: str
    ) -> bool:
        """向客户端发送确认请求，等待 confirm 消息回复。"""
        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending_confirms[session_id] = fut
        await self.emit(session_id, EventType.CONFIRM_REQUEST, {
            "tool": tool_name,
            "args": arguments,
            "message": f"工具 '{tool_name}' 是不可逆操作，是否确认执行？",
        })
        try:
            return await asyncio.wait_for(fut, timeout=60.0)
        except asyncio.TimeoutError:
            return False
        finally:
            self._pending_confirms.pop(session_id, None)

    # ── 私有构建方法 ──────────────────────────────────────────────────────

    def _build_agent_loop(self, session_id: str):
        """构建完整 AgentLoop（注入所有依赖）。"""
        from sunday.agent.executor import Executor
        from sunday.agent.loop import AgentLoop
        from sunday.agent.planner import Planner
        from sunday.agent.verifier import Verifier
        from sunday.memory.context import ContextBuilder
        from sunday.memory.manager import MemoryManager
        from sunday.skills.loader import SkillLoader
        from sunday.tools.cli_tool import register_cli_tools
        from sunday.tools.registry import ToolRegistry

        cfg = self._settings
        workspace_dir = cfg.sunday.agent.workspace_dir

        async def gw_confirm(tool_name: str, arguments: dict, _sid: str) -> bool:
            return await self.request_confirm(tool_name, arguments, session_id)

        registry = ToolRegistry(cfg, confirmation_handler=gw_confirm)
        register_cli_tools(registry)

        skill_loader = SkillLoader(
            project_skills_dir=workspace_dir.parent.parent / "skills",
            user_skills_dir=workspace_dir / "skills",
        )
        skill_loader.discover()

        context_builder = ContextBuilder(workspace_dir, skill_loader=skill_loader)
        memory_manager = MemoryManager(workspace_dir, cfg)

        async def loop_emit(sid: str, event_type, data: dict) -> None:
            await self.emit(sid, event_type, data)

        return AgentLoop(
            planner=Planner(cfg),
            executor=Executor(cfg, tool_registry=registry),
            verifier=Verifier(cfg),
            emit=loop_emit,
            context_builder=context_builder,
            memory_manager=memory_manager,
        )
