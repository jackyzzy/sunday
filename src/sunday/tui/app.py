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
from sunday.tui.widgets.status_bar import StatusBar

logger = logging.getLogger(__name__)

DEFAULT_SERVICE_URL = "ws://localhost:7899"


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


def _promote_selection_to_clipboard() -> bool:
    """将终端 PRIMARY 选区提升到 CLIPBOARD（仅 Linux X11/Wayland 需要）。

    - WSL/Windows Terminal：鼠标松开时已自动写入 Windows 剪贴板，无需操作
    - macOS：终端松开时已写入剪贴板，无需操作
    - Linux X11：拖拽选区进入 PRIMARY，需手动提升到 CLIPBOARD
    - Linux Wayland：同 X11，使用 wl-paste/wl-copy
    返回 True 表示有选区内容被提升（或平台已自动处理），False 表示无选区或工具缺失。
    """
    import shutil
    import subprocess
    import sys

    # WSL2 / Windows：终端已自动处理
    if shutil.which("clip.exe"):
        return True

    # macOS：终端已自动处理
    if sys.platform == "darwin":
        return True

    def _run(cmd: list[str], input_text: str | None = None) -> tuple[int, str]:
        try:
            r = subprocess.run(
                cmd,
                input=input_text,
                capture_output=True,
                text=True,
                timeout=2,
            )
            return r.returncode, r.stdout
        except Exception:
            return 1, ""

    # Linux X11 — xclip
    if shutil.which("xclip"):
        code, text = _run(["xclip", "-o", "-selection", "primary"])
        if code == 0 and text.strip():
            _run(["xclip", "-i", "-selection", "clipboard"], text)
            return True

    # Linux X11 — xsel（备选）
    if shutil.which("xsel"):
        code, text = _run(["xsel", "-o", "--primary"])
        if code == 0 and text.strip():
            _run(["xsel", "-i", "--clipboard"], text)
            return True

    # Linux Wayland
    if shutil.which("wl-paste") and shutil.which("wl-copy"):
        code, text = _run(["wl-paste", "--primary"])
        if code == 0 and text.strip():
            _run(["wl-copy"], text)
            return True

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
        Binding("ctrl+y", "toggle_copy_mode", "复制模式", show=False),
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
        self._ws = None
        self._slash_handler: SlashCommandHandler | None = None
        # 等待确认的 Future
        self._pending_confirm: asyncio.Future | None = None
        # 复制模式：禁用 Textual 鼠标捕获，让终端处理原生文本选择
        self._copy_mode: bool = False

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
        """后台 Worker：连接 Service，循环接收事件，断连后自动重连。"""
        import websockets

        while True:
            try:
                async with websockets.connect(
                    self.service_url, ping_interval=None
                ) as ws:
                    self._ws = ws
                    self._slash_handler = SlashCommandHandler(app=self, ws=ws)
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

    # ── 鼠标捕获切换（跨平台：WSL / Linux / macOS / Windows Terminal）────────

    def _set_terminal_mouse(self, enabled: bool) -> None:
        """通过写入 xterm 鼠标序列，启用或禁用终端鼠标事件报告。

        使用 Textual driver 的输出通道（而非直接写 sys.stdout），避免
        与 Textual 渲染缓冲区冲突。所有现代终端（Windows Terminal、
        iTerm2、GNOME Terminal、Alacritty 等）均支持这些序列。
        """
        try:
            driver = getattr(self, "_driver", None)
            if driver is None:
                return
            if enabled:
                # 恢复 Textual 默认开启的四条鼠标模式
                driver.write("\x1b[?1000h\x1b[?1003h\x1b[?1015h\x1b[?1006h")
            else:
                # 关闭所有鼠标事件报告，终端接管鼠标（原生选中、右键菜单）
                driver.write("\x1b[?1000l\x1b[?1003l\x1b[?1015l\x1b[?1006l")
            if hasattr(driver, "flush"):
                driver.flush()
        except Exception:
            pass

    @staticmethod
    def _copy_mode_hint() -> str:
        """根据当前平台返回复制快捷键提示（显示在状态栏）。"""
        import shutil
        import sys
        if shutil.which("clip.exe"):
            # WSL / Windows Terminal：右键直接复制已选文字（无菜单）
            return "拖拽选中 | 右键 或 Ctrl+Shift+C 复制 | Ctrl+C 退出"
        if sys.platform == "darwin":
            return "拖拽选中 | Cmd+C 复制 | Ctrl+C 退出"
        return "拖拽选中 | Ctrl+Shift+C 复制 | Ctrl+C 退出"

    async def action_toggle_copy_mode(self) -> None:
        """Ctrl+Y / /copy：切换复制模式。

        进入后 Textual 停止捕获鼠标事件，终端接管：
        - 拖拽可看到原生高亮选中
        - WSL/Windows：右键 或 Ctrl+Shift+C 复制；松开鼠标时已自动入剪贴板
        - Linux：Ctrl+Shift+C 复制到剪贴板
        - macOS：Cmd+C 复制
        - 或直接按 Ctrl+C：自动将选区提升到剪贴板（Linux X11/Wayland）后退出
        进入时焦点转到 ChatLog 以支持键盘滚动（↑/↓/PgUp/PgDn）；
        退出时焦点还给输入框。
        """
        self._copy_mode = not self._copy_mode
        self._set_terminal_mouse(enabled=not self._copy_mode)
        try:
            self.query_one(StatusBar).set_copy_mode(
                self._copy_mode, self._copy_mode_hint()
            )
        except Exception:
            pass
        if self._copy_mode:
            # 进入复制模式：聚焦 ChatLog，允许键盘滚动（鼠标滚轮已不可用）
            try:
                self.query_one(ChatLog).focus()
            except Exception:
                pass
        else:
            # 退出复制模式：强制将焦点还给输入框
            try:
                from textual.widgets import Input
                self.query_one("#main-input", Input).focus()
            except Exception:
                pass

    async def action_abort_task(self) -> None:
        # Esc 优先退出复制模式
        if self._copy_mode:
            await self.action_toggle_copy_mode()
            return
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
        """Ctrl+C：复制对话或退出复制模式。

        - 复制模式中：将终端 PRIMARY 选区提升到 CLIPBOARD（Linux X11/Wayland），
          然后退出复制模式并还给输入框焦点。
          WSL/macOS 终端松开鼠标时已自动写入剪贴板，无需额外操作。
        - 普通模式：将全部对话内容复制到系统剪贴板。
        """
        if self._copy_mode:
            # 复制模式：提升选区 + 退出
            _promote_selection_to_clipboard()
            await self.action_toggle_copy_mode()
            return

        # 普通模式：复制全部对话
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
