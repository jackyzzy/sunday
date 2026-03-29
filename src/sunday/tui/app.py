"""Phase 5：SundayApp — Textual TUI 主应用"""
from __future__ import annotations

import asyncio
import logging
import uuid

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Header, Label

from sunday.gateway.protocol import EventType, Message
from sunday.tui.commands import SlashCommandHandler
from sunday.tui.widgets.chat_log import ChatLog
from sunday.tui.widgets.input_bar import InputBar
from sunday.tui.widgets.status_bar import StatusBar

logger = logging.getLogger(__name__)

DEFAULT_GATEWAY_URL = "ws://localhost:7899"


class SundayApp(App):
    """Sunday TUI — 5 区布局（Header / ChatLog / StatusBar / InfoBar / InputBar）。"""

    CSS_PATH = "style.tcss"

    BINDINGS = [
        Binding("ctrl+p", "switch_session", "切换会话", show=False),
        Binding("ctrl+l", "switch_model", "切换模型", show=False),
        Binding("ctrl+t", "toggle_thinking", "思考展开", show=False),
        Binding("ctrl+o", "toggle_tools", "工具卡片", show=False),
        Binding("escape", "abort_task", "中止任务", show=False),
    ]

    def __init__(
        self,
        gateway_url: str = DEFAULT_GATEWAY_URL,
        auto_connect: bool = True,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.gateway_url = gateway_url
        self.auto_connect = auto_connect
        self.session_id: str = str(uuid.uuid4())
        # 从配置读取初始值
        from sunday.config import settings
        self.thinking_level: str = settings.sunday.reasoning.thinking_level
        self.model_override: str | None = None
        self._model_id: str = settings.sunday.model.id
        self._ws = None
        self._slash_handler: SlashCommandHandler | None = None
        # 等待确认的 Future
        self._pending_confirm: asyncio.Future | None = None

    # ── 布局 ──────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        sid_short = self.session_id[:8]
        yield Label(
            f"Session: {sid_short}  │  Think: {self.thinking_level}  │  Model: {self._model_id}",
            id="info-bar",
        )
        yield ChatLog()
        yield StatusBar()
        yield InputBar()

    def on_mount(self) -> None:
        self.title = "Sunday"
        if self.auto_connect:
            self._connect_worker()

    # ── WebSocket 连接 ────────────────────────────────────────────────────

    @work(exclusive=False, thread=False)
    async def _connect_worker(self) -> None:
        """后台 Worker：连接 Gateway，循环接收事件。"""
        try:
            import websockets
            async with websockets.connect(self.gateway_url) as ws:
                self._ws = ws
                self._slash_handler = SlashCommandHandler(app=self, ws=ws)
                await self._recv_loop(ws)
        except Exception as e:
            logger.warning("Gateway 连接失败：%s", e)
            self.query_one(ChatLog).add_system_message(
                f"Gateway 未连接（{e}）。请先运行 sunday gateway start"
            )

    async def _recv_loop(self, ws) -> None:
        """持续接收 Gateway 推送事件。"""
        async for raw in ws:
            try:
                import json
                data = json.loads(raw)
                await self.handle_gateway_event(data)
            except Exception as e:
                logger.warning("事件处理失败：%s", e)

    async def handle_gateway_event(self, data: dict) -> None:
        """处理 Gateway 推送的事件，更新 UI 组件。"""
        event_type = data.get("type", "")
        payload = data.get("data", {})
        chat = self.query_one(ChatLog)
        status = self.query_one(StatusBar)

        if event_type == EventType.STATUS.value:
            state = payload.get("state", "")
            if state == "thinking":
                status.set_thinking()
            elif state.startswith("executing"):
                status.set_executing(state.split(":", 1)[-1] if ":" in state else "")
            elif state == "idle":
                status.set_idle()
            elif state == "aborted":
                status.set_aborted()
            elif state == "error":
                status.set_error(payload.get("message", ""))
            elif state == "busy":
                chat.add_system_message(payload.get("message", "任务运行中"))

        elif event_type == EventType.PLAN.value:
            chat.add_plan(
                goal=payload.get("goal", ""),
                steps=payload.get("steps", []),
            )

        elif event_type == EventType.STREAM.value:
            chat.append_stream(payload.get("delta", ""))

        elif event_type == EventType.DONE.value:
            chat.add_assistant_message(payload.get("content", ""))
            status.set_idle()

        elif event_type == EventType.ERROR.value:
            chat.add_error_message(payload.get("message", "未知错误"))
            status.set_error()

        elif event_type == EventType.CONFIRM_REQUEST.value:
            chat.add_confirm_request(
                tool=payload.get("tool", ""),
                message=payload.get("message", ""),
            )

        elif event_type == EventType.SLASH_RESULT.value:
            cmd = payload.get("command", "")
            if cmd == "new":
                new_sid = payload.get("new_session_id", "")
                self.session_id = new_sid
                self._refresh_info_bar()
                chat.add_system_message(f"新会话已创建：{new_sid}")
            elif cmd == "sessions":
                sessions = payload.get("sessions", [])
                lines = "\n".join(
                    f"  {s['session_id']} （{s.get('last_active', '')[:10]}）"
                    for s in sessions
                )
                chat.add_system_message(f"会话列表：\n{lines or '  （无）'}")

    # ── 输入处理 ──────────────────────────────────────────────────────────

    @on(InputBar.Submitted)
    async def _on_input_submitted(self, event: InputBar.Submitted) -> None:
        text = event.text
        if not text:
            return

        # Slash 命令
        if text.startswith("/") and self._slash_handler:
            result = await self._slash_handler.handle(text)
            if result:
                self.query_one(ChatLog).add_system_message(result)
            return

        # 普通消息：显示并发送到 Gateway
        self.query_one(ChatLog).add_user_message(text)
        if self._ws:
            try:
                msg = Message(
                    type=EventType.SEND,
                    session_id=self.session_id,
                    data={"content": text},
                )
                await self._ws.send(msg.to_json())
            except Exception as e:
                self.query_one(ChatLog).add_error_message(f"发送失败：{e}")

    def _refresh_info_bar(self) -> None:
        """同步 info-bar 显示的 session/think/model 信息。"""
        sid_short = self.session_id[:8]
        model_display = self.model_override or self._model_id
        try:
            self.query_one("#info-bar", Label).update(
                f"Session: {sid_short}  │  Think: {self.thinking_level}  │  Model: {model_display}"
            )
        except Exception:
            pass

    # ── 快捷键 Action ─────────────────────────────────────────────────────

    async def action_abort_task(self) -> None:
        if self._ws:
            msg = Message(type=EventType.ABORT, session_id=self.session_id)
            await self._ws.send(msg.to_json())

    async def action_switch_session(self) -> None:
        self.query_one(ChatLog).add_system_message(
            "提示：使用 /sessions 列出并用 /session <id> 切换"
        )

    async def action_switch_model(self) -> None:
        self.query_one(ChatLog).add_system_message("提示：使用 /model <provider/model-id> 切换模型")

    async def action_toggle_thinking(self) -> None:
        self.query_one(ChatLog).add_system_message("提示：使用 /think <level> 设置思考深度")

    async def action_toggle_tools(self) -> None:
        pass  # 工具卡片折叠在 Phase 5 TUI 内处理
