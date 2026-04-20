"""Phase 5：Gateway Server — WebSocket 守护进程"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable

import httpx
import websockets
from websockets.asyncio.server import serve

from sunday.agent.models import AgentState, Message as ConvMessage, SessionThread, ThinkingLevel
from sunday.gateway.continuation import (
    build_effective_task,
    is_continuation_cue,
    resolve_continuation,
)
from sunday.gateway.history import extract_conversation
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
        # 信任模式会话集合：在此集合中的 session 跳过所有危险操作确认
        self._trusted_sessions: set[str] = set()

        # 测试注入点：替换 AgentLoop.run
        self._mock_loop_run: Callable | None = None

        self._server = None

    # ── 启动 / 停止 ───────────────────────────────────────────────────────

    async def start(self, port: int = DEFAULT_PORT) -> None:
        """启动 WebSocket 服务（永久运行）。"""
        logger.info("Gateway 启动，监听 ws://localhost:%d", port)
        async with serve(self._handle, "localhost", port, ping_interval=None):
            await asyncio.Future()  # 永久挂起

    async def start_test(self, port: int = 0) -> int:
        """测试用：绑定随机端口，不阻塞，返回实际端口。"""
        self._server = await serve(self._handle, "localhost", port, ping_interval=None)
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
        """处理用户发送消息：检查串行，创建 AgentLoop Task。"""
        content = data.get("content", "")
        thinking_raw = data.get("thinking_level", "")
        model_override = data.get("model_override") or None

        # 串行检查
        if session_id in self._running_tasks and not self._running_tasks[session_id].done():
            await self.emit(session_id, EventType.STATUS, {
                "state": "busy",
                "message": "当前会话有任务运行中，请等待完成或发送 /abort 中止",
            })
            return

        # 确保 session 目录存在（TUI 在客户端生成 session_id，Gateway 首次收到时自动创建）
        self._session_mgr.ensure_session(session_id)

        # 连续词（"请继续" / "continue" 等）识别：从本 session 的历史中解析出
        # 原始任务，改写 effective_task 交给 Planner；空 session 则直接反问。
        effective_task = content
        if is_continuation_cue(content):
            events_for_resolution = self._session_mgr.load_history(
                session_id, max_events=200
            )
            resolution = resolve_continuation(events_for_resolution, content)
            if resolution.found:
                effective_task = build_effective_task(resolution)
            else:
                await self.emit(session_id, EventType.STATUS, {
                    "state": "needs_input",
                    "message": "当前会话还没有可继续的任务，请告诉我要做什么。",
                })
                return

        # 生成轮次 ID，计算当前轮次序号
        turn_id = uuid.uuid4().hex[:8]
        turn_start_ts = datetime.now(timezone.utc).isoformat()
        # turn_buffer 在内存中积累本轮数据，turn_end 时一次性写入 turns/{turn_id}.json
        turn_buffer: dict = {
            "turn_id": turn_id,
            "turn_index": None,  # write_turn 时由 meta.json turn_count 决定
            "ts_start": turn_start_ts,
            "ts_end": None,
            "outcome": None,
            "user_input": content,
            "plan": None,
            "execution": [],
            "output": None,
        }

        # 写 TURN_START + SEND 到 stream（stream 里记录用户真实输入，保留可追溯性）
        await self._session_mgr.append_stream(
            session_id, EventType.TURN_START, {"content": content}, turn_id
        )
        await self._session_mgr.append_stream(
            session_id, EventType.SEND, {"content": content}, turn_id
        )

        # 构建 AgentLoop 并异步运行
        # 读取最近对话历史 + 会话主线，注入 Planner 上下文
        recent_events = self._session_mgr.load_history(session_id, max_events=50)
        history = extract_conversation(recent_events, max_turns=5)
        thread_raw = self._session_mgr.get_session_thread(session_id)
        if not thread_raw and self._session_mgr.get_prior_turn_count(session_id) > 0:
            # session_thread 为空（首轮全失败、update_session_thread 未触发）时，
            # 若本 session 已有过至少一轮尝试，用 meta.title 兜底，至少给 Planner
            # 一条本会话主题的锚点。首轮不注入（title == task，无额外信息）。
            title = self._session_mgr.get_session_title(session_id)
            if title:
                thread_raw = {
                    "summary": f"本会话主题：{title}",
                    "key_entities": [],
                    "updated_at_turn": "",
                }
        session_thread = SessionThread(**thread_raw) if thread_raw else None
        try:
            thinking = ThinkingLevel(thinking_raw)
        except ValueError:
            thinking = ThinkingLevel(self._settings.sunday.reasoning.thinking_level)
        state = AgentState(session_id=session_id, task=effective_task, history=history,
                           session_thread=session_thread,
                           thinking_level=thinking, turn_id=turn_id)

        async def run_loop():
            nonlocal turn_buffer
            outcome = "error"
            display = ""
            try:
                loop = None
                if self._mock_loop_run is not None:
                    result = await self._mock_loop_run(state)
                else:
                    loop = self._build_agent_loop(session_id, task=content,
                                                  model_override=model_override,
                                                  turn_id=turn_id,
                                                  turn_buffer=turn_buffer)
                    result = await loop.run(state)
                from sunday.tools.cli_tool import format_report_footer
                report_path = self._session_mgr._dir / session_id / "reports" / turn_id
                footer = format_report_footer(result or "", report_path)
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
                # 同步把 ERROR 事件写入 stream.jsonl（L3 history 能看到失败原因）
                try:
                    await self._session_mgr.append_stream(
                        session_id, EventType.ERROR, {"message": msg}, turn_id
                    )
                except Exception:
                    logger.exception("写入 ERROR 事件到 stream 失败（不影响用户）")
            finally:
                ts_end = datetime.now(timezone.utc).isoformat()
                turn_buffer["ts_end"] = ts_end
                turn_buffer["outcome"] = outcome
                # 写 DONE 到 stream（修复 DONE 未落盘的 bug）
                await self._session_mgr.append_stream(
                    session_id, EventType.DONE, {"content": display}, turn_id
                )
                # 写 TURN_END 到 stream
                await self._session_mgr.append_stream(
                    session_id, EventType.TURN_END, {"outcome": outcome}, turn_id
                )
                # 写结构化 turn 文件
                await self._session_mgr.write_turn(session_id, turn_buffer)
                # 增量更新会话主线（失败静默，不影响用户）
                try:
                    from sunday.memory.session_thread import update_session_thread
                    await update_session_thread(
                        session_id, turn_buffer, self._session_mgr, self._settings.sunday
                    )
                except Exception:
                    logger.exception("update_session_thread 失败（不影响用户）")
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
            turns = self._session_mgr.load_history(session_id, group_by_turn=True)
            thread = self._session_mgr.get_session_thread(session_id)
            compact_turns = [_compact_turn_for_display(t) for t in turns]
            await self.emit(session_id, EventType.SLASH_RESULT, {
                "command": "history",
                "session_id": session_id,
                "session_thread": thread,
                "turns": compact_turns,
                "message": f"会话 {session_id} 共 {len(compact_turns)} 轮对话。",
            })
        elif command == "trust":
            self._trusted_sessions.add(session_id)
            await self.emit(session_id, EventType.SLASH_RESULT, {
                "command": "trust",
                "message": "当前会话已启用信任模式，所有危险操作将自动确认。",
            })
        elif command == "reset":
            self._session_mgr.reset_session(session_id)
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
            # 1. 删除 session 目录（含 reports/ 子目录）及 index
            self._session_mgr.delete_session(target_id)
            # 2. 删除 log 文件
            log_file = self._settings.sunday.agent.log_dir / f"{target_id}.jsonl"
            if log_file.exists():
                log_file.unlink()
            await self.emit(session_id, EventType.SLASH_RESULT, {
                "command": "delete",
                "deleted_id": target_id,
                "message": f"会话已删除：{target_id}",
            })
        elif command == "memory":
            file_key = data.get("args", "MEMORY").upper()
            _FILE_MAP = {
                "SOUL": "SOUL.md",
                "MEMORY": "MEMORY.md",
                "USER": "memory/user.md",
                "TOOLS": "memory/tools.md",
            }
            workspace = self._settings.sunday.agent.workspace_dir
            target = workspace / _FILE_MAP.get(file_key, "MEMORY.md")
            content = target.read_text(encoding="utf-8") if target.exists() else "（文件不存在）"
            await self.emit(session_id, EventType.SLASH_RESULT, {
                "command": "memory",
                "file": target.name,
                "content": content[:4000],
            })
        elif command == "skills":
            from sunday.skills.loader import SkillLoader
            workspace = self._settings.sunday.agent.workspace_dir
            loader = SkillLoader(
                project_skills_dir=workspace.parent.parent / "skills",
                user_skills_dir=workspace / "skills",
            )
            skill_list = [m.name for m in loader.discover()]
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
        """resolve pending confirm Future。"""
        fut = self._pending_confirms.get(session_id)
        if fut and not fut.done():
            fut.set_result(confirmed)

    # ── 公开 API ──────────────────────────────────────────────────────────

    async def emit(self, session_id: str, event_type, data: dict) -> None:
        """向 session_id 对应的 WebSocket 推送消息。"""
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
        """向客户端发送确认请求，等待 confirm 消息回复。"""
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

    # ── 私有构建方法 ──────────────────────────────────────────────────────

    def _build_agent_loop(self, session_id: str, task: str = "",
                           model_override: str | None = None,
                           turn_id: str | None = None,
                           turn_buffer: dict | None = None):
        """构建完整 AgentLoop（注入所有依赖）。"""
        from sunday.bootstrap import build_agent_loop

        ET = EventType
        cfg = self._settings.sunday  # SundayConfig

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

        # 需要写入 stream.jsonl 的事件类型
        _STREAM_RECORDABLE = {
            ET.PLAN, ET.STEP_RESULT, ET.TOOL_START, ET.TOOL_END, ET.STATUS,
        }

        async def loop_emit(sid: str, event_type, data: dict) -> None:
            # 推送 WebSocket（不变）
            await self.emit(sid, event_type, data)
            # 写入 stream.jsonl
            if turn_id is not None:
                try:
                    et = event_type if isinstance(event_type, ET) else ET(event_type)
                except ValueError:
                    return
                if et in _STREAM_RECORDABLE:
                    await self._session_mgr.append_stream(sid, et, data, turn_id=turn_id)
                # 更新 turn_buffer
                if turn_buffer is not None:
                    if et == ET.PLAN:
                        turn_buffer["plan"] = data
                    elif et == ET.STEP_RESULT:
                        turn_buffer["execution"].append(dict(data))
                    elif et == ET.TOOL_END and turn_buffer["execution"]:
                        last_step = turn_buffer["execution"][-1]
                        last_step.setdefault("tool_calls", []).append(data)

        return build_agent_loop(cfg, loop_emit, mode="gateway", confirmation_handler=gw_confirm)


# 历史提取逻辑已抽到 sunday.gateway.history.extract_conversation（供 CLI 复用）


_HISTORY_OUTPUT_LIMIT = 400
_HISTORY_USER_LIMIT = 200
_HISTORY_GOAL_LIMIT = 200


def _compact_turn_for_display(turn: dict) -> dict:
    """把 turns/*.json 压缩成 TUI /history 可渲染的扁平字典。

    仅保留 turn_index / ts_start / user_input / plan_goal / output，
    长字符串以 `……` 截断。
    """
    plan = turn.get("plan") or {}
    plan_goal = plan.get("goal", "") if isinstance(plan, dict) else ""
    user_input = turn.get("user_input") or ""
    output = turn.get("output") or ""

    return {
        "turn_id": turn.get("turn_id", ""),
        "turn_index": turn.get("turn_index", 0),
        "ts_start": turn.get("ts_start", ""),
        "outcome": turn.get("outcome", ""),
        "user_input": _ellipsize(user_input, _HISTORY_USER_LIMIT),
        "plan_goal": _ellipsize(plan_goal, _HISTORY_GOAL_LIMIT),
        "output": _ellipsize(output, _HISTORY_OUTPUT_LIMIT),
    }


def _ellipsize(text: str, limit: int) -> str:
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + "……"
