"""Gateway Server — WebSocket 守护进程"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable

import httpx
import websockets
from websockets.asyncio.server import serve

from sunday.agent.models import AgentState, ThinkingLevel
from sunday.bootstrap import build_memory_client
from sunday.gateway.continuation import (
    build_effective_task,
    is_continuation_cue,
    resolve_continuation,
)
from sunday.gateway.history import extract_conversation
from sunday.gateway.protocol import EventType, Message
from sunday.memory.models import SessionEvent, TurnRecord

if TYPE_CHECKING:
    from sunday.config import Settings
    from sunday.memory.client import MemoryClient

logger = logging.getLogger(__name__)

DEFAULT_PORT = 7899

# stream.jsonl 持久化的事件白名单（其余仅 WebSocket 推送）
_STREAM_RECORDABLE = {
    EventType.PLAN, EventType.STEP_RESULT, EventType.TOOL_START,
    EventType.TOOL_END, EventType.STATUS,
}


class Gateway:
    """本地 WebSocket 守护进程。

    职责：
    - 接受 WebSocket 连接（session_id 映射）
    - 路由消息到 _handle_send / _handle_abort / _handle_slash / _handle_confirm
    - 管理 AgentLoop asyncio.Task 生命周期
    - 提供 emit() 回调供 AgentLoop 使用
    - 所有持久化都通过 self._client (MemoryClient)
    """

    def __init__(self, settings: "Settings") -> None:
        self._settings = settings
        self._client: MemoryClient = build_memory_client(settings.sunday)

        # session_id → WebSocket
        self._connections: dict[str, Any] = {}
        # session_id → asyncio.Task
        self._running_tasks: dict[str, asyncio.Task] = {}
        # session_id → Future（等待用户确认）
        self._pending_confirms: dict[str, asyncio.Future] = {}
        self._trusted_sessions: set[str] = set()

        # 测试注入点：替换 AgentLoop.run
        self._mock_loop_run: Callable | None = None

        self._server = None

    # ── 启动 / 停止 ───────────────────────────────────────────────────────

    async def start(self, port: int = DEFAULT_PORT) -> None:
        logger.info("Gateway 启动，监听 ws://localhost:%d", port)
        async with serve(self._handle, "localhost", port, ping_interval=None):
            await asyncio.Future()

    async def start_test(self, port: int = 0) -> int:
        self._server = await serve(self._handle, "localhost", port, ping_interval=None)
        actual_port = self._server.sockets[0].getsockname()[1]
        logger.debug("Gateway 测试模式，端口=%d", actual_port)
        return actual_port

    async def stop(self) -> None:
        for task in list(self._running_tasks.values()):
            task.cancel()
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        await self._client.aclose()

    # ── 消息路由 ──────────────────────────────────────────────────────────

    async def _handle(self, ws) -> None:
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
                    await self._handle_send(session_id, msg.data)
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

    async def _handle_send(self, session_id: str, data: dict) -> None:
        content = data.get("content", "")
        thinking_raw = data.get("thinking_level", "")
        model_override = data.get("model_override") or None

        if session_id in self._running_tasks and not self._running_tasks[session_id].done():
            await self.emit(session_id, EventType.STATUS, {
                "state": "busy",
                "message": "当前会话有任务运行中，请等待完成或发送 /abort 中止",
            })
            return

        await self._client.sessions.ensure(session_id)

        # 连续词识别
        effective_task = content
        if is_continuation_cue(content):
            events = await self._client.sessions.load_events(session_id, max_events=200)
            events_for_resolution = [_event_to_dict(e) for e in events]
            resolution = resolve_continuation(events_for_resolution, content)
            if resolution.found:
                effective_task = build_effective_task(resolution)
            else:
                await self.emit(session_id, EventType.STATUS, {
                    "state": "needs_input",
                    "message": "当前会话还没有可继续的任务，请告诉我要做什么。",
                })
                return

        turn_id = uuid.uuid4().hex[:8]
        turn_start_ts = datetime.now(timezone.utc).isoformat()
        turn_buffer: dict = {
            "turn_id": turn_id,
            "turn_index": 0,
            "ts_start": turn_start_ts,
            "ts_end": "",
            "outcome": "",
            "user_input": content,
            "plan": None,
            "execution": [],
            "output": "",
        }

        await self._append_event(
            session_id, EventType.TURN_START, {"content": content}, turn_id,
        )
        await self._append_event(
            session_id, EventType.SEND, {"content": content}, turn_id,
        )

        # 注入历史 + 主线
        recent_events = await self._client.sessions.load_events(session_id, max_events=50)
        history = extract_conversation([_event_to_dict(e) for e in recent_events], max_turns=5)
        thread = await self._client.sessions.get_thread(session_id)
        if thread is None:
            session_meta = await self._client.sessions.get(session_id)
            if session_meta and session_meta.turn_count > 0 and session_meta.title:
                from sunday.agent.models import SessionThread
                thread = SessionThread(
                    summary=f"本会话主题：{session_meta.title}",
                    key_entities=[], updated_at_turn="",
                )
        try:
            thinking = ThinkingLevel(thinking_raw)
        except ValueError:
            thinking = ThinkingLevel(self._settings.sunday.reasoning.thinking_level)

        state = AgentState(
            session_id=session_id, task=effective_task, history=history,
            session_thread=thread, thinking_level=thinking, turn_id=turn_id,
        )

        async def run_loop():
            outcome = "error"
            display = ""
            try:
                if self._mock_loop_run is not None:
                    result = await self._mock_loop_run(state)
                else:
                    loop = self._build_agent_loop(
                        session_id, model_override=model_override,
                        turn_id=turn_id, turn_buffer=turn_buffer,
                    )
                    result = await loop.run(state)
                from sunday.tools.cli_tool import format_report_footer
                report_dir = self._client.sessions.report_dir(session_id, turn_id)
                footer = format_report_footer(result or "", report_dir)
                display = (result or "") + footer
                turn_buffer["output"] = display
                outcome = "success"
                await self.emit(session_id, EventType.DONE, {"content": display})
            except asyncio.CancelledError:
                outcome = "aborted"
                await self.emit(session_id, EventType.STATUS, {"state": "aborted"})
                raise
            except Exception as e:
                if isinstance(e, (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError)):
                    msg = f"网络连接失败（{type(e).__name__}），请检查网络或代理配置"
                    logger.error("AgentLoop 网络异常：%s", msg)
                else:
                    msg = str(e) or type(e).__name__
                    logger.exception("AgentLoop 异常：%s", e)
                await self.emit(session_id, EventType.ERROR, {"message": msg})
                try:
                    await self._append_event(
                        session_id, EventType.ERROR, {"message": msg}, turn_id,
                    )
                except Exception:
                    logger.exception("写入 ERROR 事件失败（不影响用户）")
            finally:
                ts_end = datetime.now(timezone.utc).isoformat()
                turn_buffer["ts_end"] = ts_end
                turn_buffer["outcome"] = outcome
                await self._append_event(
                    session_id, EventType.DONE, {"content": display}, turn_id,
                )
                await self._append_event(
                    session_id, EventType.TURN_END, {"outcome": outcome}, turn_id,
                )
                await self._client.sessions.write_turn(session_id, TurnRecord(**turn_buffer))
                try:
                    from sunday.memory.session_thread import update_session_thread
                    await update_session_thread(
                        session_id, turn_buffer, self._client, self._settings.sunday,
                    )
                except Exception:
                    logger.exception("update_session_thread 失败（不影响用户）")
                self._running_tasks.pop(session_id, None)

        task = asyncio.create_task(run_loop())
        self._running_tasks[session_id] = task

    async def _handle_abort(self, session_id: str) -> None:
        task = self._running_tasks.get(session_id)
        if task and not task.done():
            task.cancel()
            logger.info("会话 %s 任务已中止", session_id)

    async def _handle_slash(self, session_id: str, data: dict) -> None:
        command = data.get("command", "")
        if command == "new":
            new_meta = await self._client.sessions.create()
            await self.emit(session_id, EventType.SLASH_RESULT, {
                "command": "new",
                "new_session_id": new_meta.session_id,
            })
        elif command == "sessions":
            sessions = await self._client.sessions.list()
            await self.emit(session_id, EventType.SLASH_RESULT, {
                "command": "sessions",
                "sessions": [s.model_dump() for s in sessions],
            })
        elif command == "history":
            turns = await self._client.sessions.load_turns(session_id)
            thread = await self._client.sessions.get_thread(session_id)
            compact = [_compact_turn_for_display(t) for t in turns]
            await self.emit(session_id, EventType.SLASH_RESULT, {
                "command": "history",
                "session_id": session_id,
                "session_thread": thread.model_dump() if thread else None,
                "turns": compact,
                "message": f"会话 {session_id} 共 {len(compact)} 轮对话。",
            })
        elif command == "trust":
            self._trusted_sessions.add(session_id)
            await self.emit(session_id, EventType.SLASH_RESULT, {
                "command": "trust",
                "message": "当前会话已启用信任模式，所有危险操作将自动确认。",
            })
        elif command == "reset":
            await self._client.sessions.reset(session_id)
            await self.emit(session_id, EventType.SLASH_RESULT, {
                "command": "reset",
                "message": "会话上下文已清空。",
            })
        elif command == "delete":
            target_id = (data.get("args") or "").strip()
            if not target_id:
                await self.emit(session_id, EventType.SLASH_RESULT, {
                    "command": "delete",
                    "message": "[错误] 请指定要删除的 session ID，用法：/delete <session_id>",
                })
                return
            await self._client.sessions.delete(target_id)
            await self.emit(session_id, EventType.SLASH_RESULT, {
                "command": "delete",
                "deleted_id": target_id,
                "message": f"会话已删除：{target_id}",
            })
        elif command == "memory":
            file_key = (data.get("args") or "MEMORY").upper()
            if file_key in {"SOUL", "AGENTS", "TOOLS"}:
                content = await self._client.workspace.read(file_key)
                file_label = f"{file_key}.md"
            elif file_key == "USER":
                content = await self._client.knowledge.read_layer("USER")
                file_label = "USER.md"
            else:
                content = await self._client.knowledge.read_layer("MEMORY")
                file_label = "MEMORY.md"
            if not content:
                content = "（文件不存在）"
            await self.emit(session_id, EventType.SLASH_RESULT, {
                "command": "memory",
                "file": file_label,
                "content": content[:4000],
            })
        elif command == "skills":
            skill_list = await self._client.workspace.list_skills()
            await self.emit(session_id, EventType.SLASH_RESULT, {
                "command": "skills",
                "skills": skill_list,
            })
        else:
            await self.emit(session_id, EventType.SLASH_RESULT, {
                "command": command,
                "message": f"未知 slash 命令：{command}",
            })

    def _handle_confirm(self, session_id: str, confirmed: bool) -> None:
        fut = self._pending_confirms.get(session_id)
        if fut and not fut.done():
            fut.set_result(confirmed)

    # ── 公开 API ──────────────────────────────────────────────────────────

    async def emit(self, session_id: str, event_type, data: dict) -> None:
        ws = self._connections.get(session_id)
        if ws is None:
            return
        try:
            if not isinstance(event_type, EventType):
                event_type = EventType(event_type)
            msg = Message(type=event_type, session_id=session_id, data=data)
            await ws.send(msg.to_json())
        except Exception as e:
            logger.debug("emit 失败（session=%s）：%s", session_id, e)

    async def request_confirm(
        self, tool_name: str, arguments: dict, session_id: str
    ) -> bool:
        loop = asyncio.get_running_loop()
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

    # ── 私有 helper ────────────────────────────────────────────────────────

    async def _append_event(
        self, session_id: str, event_type: EventType, data: dict, turn_id: str,
    ) -> None:
        ts = datetime.now(timezone.utc).isoformat()
        await self._client.sessions.append_event(
            session_id, SessionEvent(
                type=event_type.value, session_id=session_id,
                turn_id=turn_id, data=data, ts=ts,
            ),
        )

    def _build_agent_loop(self, session_id: str, *,
                           model_override: str | None = None,
                           turn_id: str | None = None,
                           turn_buffer: dict | None = None):
        """构建 ReactAgent，注入 emit 回调（推送 WebSocket + 写 stream.jsonl）。"""
        from sunday.bootstrap import build_agent_loop

        ET = EventType
        cfg = self._settings.sunday

        if model_override:
            parts = model_override.split("/", 1)
            new_model = cfg.model.model_copy(
                update={"provider": parts[0], "id": parts[1]} if len(parts) == 2
                else {"id": parts[0]}
            )
            cfg = cfg.model_copy(update={"model": new_model})

        async def gw_confirm(tool_name: str, arguments: dict, _sid: str) -> bool:
            if session_id in self._trusted_sessions:
                return True
            return await self.request_confirm(tool_name, arguments, session_id)

        async def loop_emit(sid: str, event_type, data: dict) -> None:
            await self.emit(sid, event_type, data)
            if turn_id is None:
                return
            try:
                et = event_type if isinstance(event_type, ET) else ET(event_type)
            except ValueError:
                return
            if et in _STREAM_RECORDABLE:
                await self._append_event(sid, et, data, turn_id)
            if turn_buffer is not None:
                if et == ET.PLAN:
                    turn_buffer["plan"] = data
                elif et == ET.STEP_RESULT:
                    turn_buffer["execution"].append(dict(data))
                elif et == ET.TOOL_END and turn_buffer["execution"]:
                    last_step = turn_buffer["execution"][-1]
                    last_step.setdefault("tool_calls", []).append(data)

        return build_agent_loop(
            cfg, loop_emit, mode="gateway", confirmation_handler=gw_confirm,
            memory_client=self._client,
        )


# ── 显示辅助 ──────────────────────────────────────────────────────────────

_HISTORY_OUTPUT_LIMIT = 400
_HISTORY_USER_LIMIT = 200
_HISTORY_GOAL_LIMIT = 200


def _compact_turn_for_display(turn: TurnRecord) -> dict:
    plan = turn.plan or {}
    plan_goal = plan.get("goal", "") if isinstance(plan, dict) else ""
    return {
        "turn_id": turn.turn_id,
        "turn_index": turn.turn_index,
        "ts_start": turn.ts_start,
        "outcome": turn.outcome,
        "user_input": _ellipsize(turn.user_input, _HISTORY_USER_LIMIT),
        "plan_goal": _ellipsize(plan_goal, _HISTORY_GOAL_LIMIT),
        "output": _ellipsize(turn.output, _HISTORY_OUTPUT_LIMIT),
    }


def _ellipsize(text: str, limit: int) -> str:
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + "……"


def _event_to_dict(event: SessionEvent) -> dict:
    """SessionEvent → continuation/extract_conversation 兼容的字典。"""
    return {
        "type": event.type,
        "session_id": event.session_id,
        "turn_id": event.turn_id,
        "data": event.data,
        "ts": event.ts,
    }
