"""Phase 5：SundayApp — Textual TUI 主应用"""
from __future__ import annotations

import asyncio
import logging
import uuid

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Header, Label

from sunday.service.protocol import EventType, Message
from sunday.tui.commands import SlashCommandHandler
from sunday.tui.widgets.chat_log import ChatLog
from sunday.tui.widgets.input_bar import InputBar
from sunday.tui.widgets.input_history import InputHistory
from sunday.tui.widgets.status_bar import StatusBar

logger = logging.getLogger(__name__)

DEFAULT_SERVICE_URL = "ws://localhost:7899"

STATUS_HINT = (
    "Enter 发送 │ Ctrl+Enter 换行 │ ↑↓ 历史 │ 鼠标拖拽=选中 │ "
    "右键=粘贴 │ Ctrl+C 复制对话 │ Ctrl+Q 退出"
)


def _copy_to_clipboard(text: str) -> bool:
    """将文本写入系统剪贴板，返回是否成功。

    检测顺序：
      1. clip.exe      — WSL2 / Windows 原生
      2. pbcopy        — macOS
      3. wl-copy       — Linux Wayland
      4. xclip         — Linux X11
      5. xsel          — Linux X11（备选）
      6. pyperclip     — Python 跨平台库（需单独安装）
    """
    import shutil
    import subprocess
    import sys

    def _run(cmd: list[str], input_bytes: bytes) -> bool:
        try:
            proc = subprocess.run(cmd, input=input_bytes, check=False,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return proc.returncode == 0
        except OSError:
            return False

    encoded = text.encode("utf-8")

    # WSL2 / Windows — clip.exe 要求 UTF-16-LE
    if shutil.which("clip.exe"):
        return _run(["clip.exe"], text.encode("utf-16-le"))

    # macOS
    if sys.platform == "darwin" and shutil.which("pbcopy"):
        return _run(["pbcopy"], encoded)

    # Linux Wayland
    if shutil.which("wl-copy"):
        return _run(["wl-copy"], encoded)

    # Linux X11 — xclip
    if shutil.which("xclip"):
        return _run(["xclip", "-selection", "clipboard"], encoded)

    # Linux X11 — xsel
    if shutil.which("xsel"):
        return _run(["xsel", "--clipboard", "--input"], encoded)

    # Python 跨平台库兜底
    try:
        import pyperclip  # type: ignore
        pyperclip.copy(text)
        return True
    except Exception:
        pass

    return False


def _format_history_payload(payload: dict) -> str:
    """把 /history 的 SLASH_RESULT payload 渲染成多行文本。"""
    session_id = payload.get("session_id", "")
    thread = payload.get("session_thread") or {}
    turns = payload.get("turns") or []

    lines: list[str] = [f"会话 ID：{session_id}"]
    if thread:
        summary = thread.get("summary", "")
        entities = thread.get("key_entities") or []
        if summary:
            lines.append(f"主线：{summary}")
        if entities:
            lines.append(f"关键实体：{', '.join(entities)}")
    if not turns:
        lines.append("")
        lines.append("（当前会话还没有已完成的 turn）")
        return "\n".join(lines)

    lines.append(f"共 {len(turns)} 轮：")
    for t in turns:
        idx = t.get("turn_index", "?")
        ts = t.get("ts_start", "")[:19]
        outcome = t.get("outcome", "")
        header = f"─ Turn {idx}  [{ts}]"
        if outcome and outcome != "success":
            header += f"  ({outcome})"
        lines.append("")
        lines.append(header)
        if t.get("user_input"):
            lines.append(f"  用户：{t['user_input']}")
        if t.get("plan_goal"):
            lines.append(f"  目标：{t['plan_goal']}")
        if t.get("output"):
            lines.append(f"  回答：{t['output']}")
    return "\n".join(lines)


class SundayApp(App):
    """Sunday TUI — 5 区布局（Header / ChatLog / StatusBar / InfoBar / InputBar）。"""

    CSS_PATH = "style.tcss"

    BINDINGS = [
        Binding("ctrl+p", "switch_session", "切换会话", show=False),
        Binding("ctrl+l", "switch_model", "切换模型", show=False),
        Binding("ctrl+t", "toggle_thinking", "思考展开", show=False),
        Binding("ctrl+o", "toggle_tools", "工具卡片", show=False),
        # ctrl+c 覆盖 Textual 默认的 quit；退出改用 ctrl+q
        Binding("ctrl+c", "copy_chat", "复制对话", show=False),
        Binding("ctrl+q", "quit", "退出", show=False),
        Binding("escape", "abort_task", "中止任务", show=False),
    ]

    def __init__(
        self,
        service_url: str = DEFAULT_SERVICE_URL,
        auto_connect: bool = True,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.service_url = service_url
        self.auto_connect = auto_connect
        self.session_id: str = str(uuid.uuid4())
        # 从配置读取初始值
        from sunday.config import settings
        self.thinking_level: str = settings.sunday.reasoning.thinking_level
        self.model_override: str | None = None
        self._model_id: str = settings.sunday.model.id
        self._tui_cfg = settings.sunday.tui
        self._ws = None
        self._slash_handler: SlashCommandHandler | None = None
        # 等待确认的 Future
        self._pending_confirm: asyncio.Future | None = None
        # 输入历史：per-session，从 /history slash 返回的 turns payload 派生
        self._input_history = InputHistory(maxlen=self._tui_cfg.input_history_size)

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
        yield InputBar(
            history=self._input_history,
            paste_fold_threshold=self._tui_cfg.paste_fold_threshold,
        )

    def on_mount(self) -> None:
        self.title = "Sunday"
        try:
            self.query_one(StatusBar).set_hint(STATUS_HINT)
        except Exception:
            pass
        # mouse=False 时不能靠点击聚焦，必须主动 focus 输入框
        try:
            from sunday.tui.widgets.prompt_textarea import PromptTextArea
            self.query_one(PromptTextArea).focus()
        except Exception:
            logger.debug("启动时聚焦输入框失败（忽略）")
        if self.auto_connect:
            self._connect_worker()

    # ── WebSocket 连接 ────────────────────────────────────────────────────

    @work(exclusive=False, thread=False)
    async def _connect_worker(self) -> None:
        """后台 Worker：连接 Service，循环接收事件，断连后自动重连。"""
        import websockets

        while True:
            try:
                async with websockets.connect(
                    self.service_url, ping_interval=None
                ) as ws:
                    self._ws = ws
                    self._slash_handler = SlashCommandHandler(app=self, ws=ws)
                    # 连接建立后拉一次 /history，加载当前 session 的输入历史
                    try:
                        await self._slash_handler.handle("/history")
                    except Exception:
                        logger.debug("启动时拉取 /history 失败（忽略）")
                    await self._recv_loop(ws)
                # _recv_loop 正常退出（连接被对端关闭），自动重连
                self._ws = None
                await asyncio.sleep(1)
            except OSError:
                # Service 未启动，不重连，只提示一次
                self._ws = None
                self.query_one(ChatLog).add_system_message(
                    "Service 未连接，请先运行 sunday service start"
                )
                break
            except Exception as e:
                # 其他异常（含 ConnectionClosed）：短暂等待后重连
                logger.warning("Service 连接断开，尝试重连：%s", e)
                self._ws = None
                await asyncio.sleep(1)

    async def _recv_loop(self, ws) -> None:
        """持续接收 Service 推送事件。"""
        async for raw in ws:
            try:
                import json
                data = json.loads(raw)
                await self.handle_gateway_event(data)
            except Exception as e:
                logger.warning("事件处理失败：%s", e)

    async def handle_gateway_event(self, data: dict) -> None:
        """处理 Service 推送的事件，更新 UI 组件。"""
        event_type = data.get("type", "")
        payload = data.get("data", {})
        chat = self.query_one(ChatLog)
        status = self.query_one(StatusBar)

        if event_type == EventType.STATUS.value:
            state = payload.get("status", "")
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

        elif event_type == EventType.STEP_RESULT.value:
            chat.mark_step(
                step_id=payload.get("step_id", ""),
                status=payload.get("status", ""),
                verified=payload.get("verified"),
            )

        elif event_type == EventType.SLASH_RESULT.value:
            cmd = payload.get("command", "")
            if cmd == "new":
                new_sid = payload.get("new_session_id", "")
                self.session_id = new_sid
                self._refresh_info_bar()
                # 新会话历史为空
                self._input_history.clear()
                chat.add_system_message(f"新会话已创建：{new_sid}")
            elif cmd == "sessions":
                sessions = payload.get("sessions", [])
                lines = "\n".join(
                    "  {sid}  （{date}）  {title}".format(
                        sid=s["session_id"],
                        date=s.get("last_active", "")[:10],
                        title=s.get("title", "（无记录）"),
                    )
                    for s in sessions
                )
                chat.add_system_message(f"会话列表：\n{lines or '  （无）'}")
            elif cmd == "memory":
                file_name = payload.get("file", "")
                content = payload.get("content", "")
                chat.add_system_message(f"[{file_name}]\n{content}")
            elif cmd == "skills":
                skills = payload.get("skills", [])
                lines = "\n".join(f"  · {s}" for s in skills) or "  （无可用技能）"
                chat.add_system_message(f"可用技能：\n{lines}")
            elif cmd == "delete":
                deleted_id = payload.get("deleted_id", "")
                msg = payload.get("message", f"会话已删除：{deleted_id}")
                if deleted_id and deleted_id == self.session_id:
                    msg += "\n提示：当前会话已被删除，建议使用 /new 创建新会话"
                chat.add_system_message(msg)
            elif cmd == "history":
                # 同时：① 用 turns 重建输入历史；② 把人类可读的历史展示在对话区
                self._input_history.load_from_turns(payload.get("turns", []) or [])
                chat.add_system_message(_format_history_payload(payload))
            elif msg_text := payload.get("message"):
                # 通用兜底：trust / reset / 未知命令
                chat.add_system_message(msg_text)

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

        # 普通消息：显示并发送到 Service
        self.query_one(ChatLog).add_user_message(text)
        if self._ws:
            try:
                msg = Message(
                    type=EventType.SEND,
                    session_id=self.session_id,
                    data={
                        "content": text,
                        "thinking_level": self.thinking_level,
                        "model_override": self.model_override or "",
                    },
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
        """Esc：中止当前任务。"""
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

    async def action_copy_chat(self) -> None:
        """Ctrl+C：复制全部对话内容到系统剪贴板。"""
        chat = self.query_one(ChatLog)
        text = chat.get_plain_text()
        if not text.strip():
            chat.add_system_message("（对话内容为空，无可复制）")
            return

        if _copy_to_clipboard(text):
            chat.add_system_message("✓ 对话已复制到剪贴板")
        else:
            chat.add_system_message(
                "剪贴板不可用（需要 clip.exe / pbcopy / wl-copy / xclip / xsel / pyperclip）"
            )
