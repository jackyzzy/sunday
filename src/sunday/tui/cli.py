"""新 TUI 入口（prompt_toolkit + rich）—— 替代 Textual TUI。

设计：
- prompt_toolkit 提供底部多行输入，输出直接打印到 stdout（终端 scrollback 完整可用）
- rich.live spinner 显示运行中状态，结束时自动清除
- 复用现有的 ServiceClient、InputHistory、SlashCommandHandler（已去 Textual 耦合）
"""
from __future__ import annotations

import asyncio
import logging
import re
import secrets
import sys
from dataclasses import dataclass
from typing import Awaitable, Callable

from prompt_toolkit.application import Application, get_app
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.filters import Condition, is_done
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.history import History
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import ConditionalContainer, HSplit, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.dimension import D
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import Frame
from rich.console import Console

from sunday.config import settings
from sunday.service.client import (
    ServiceClient, new_session_id, spawn_service_if_needed,
)
from sunday.service.protocol import EventType, Message
from sunday.tui import output
from sunday.tui.commands import SlashCommandHandler
from sunday.tui.input_history import InputHistory

logger = logging.getLogger(__name__)


# ── 数据结构 ────────────────────────────────────────────────────────────────

@dataclass
class SundayState:
    """运行时状态（持有 session/model/thinking/trust）。"""
    session_id: str
    model_id: str
    thinking_level: str
    service_url: str
    model_override: str | None = None
    trust_mode: bool = False


# ── 粘贴折叠 ────────────────────────────────────────────────────────────────

_PLACEHOLDER_RE = re.compile(r"\[Pasted (\d+) lines #([0-9a-f]{6})\]")


class PasteFolder:
    """超过 threshold 行的粘贴折叠为占位符，提交时还原原文。"""

    def __init__(self, threshold: int = 4) -> None:
        self._threshold = threshold
        self._pending: dict[str, str] = {}

    def maybe_fold(self, text: str) -> str:
        line_count = text.count("\n") + 1
        if line_count <= self._threshold:
            return text
        placeholder_id = secrets.token_hex(3)
        self._pending[placeholder_id] = text
        return f"[Pasted {line_count} lines #{placeholder_id}]"

    def expand(self, text: str) -> str:
        if not self._pending:
            return text
        def _sub(m: re.Match[str]) -> str:
            return self._pending.get(m.group(2), m.group(0))
        return _PLACEHOLDER_RE.sub(_sub, text)

    def reset(self) -> None:
        self._pending.clear()


# ── History 适配器 ────────────────────────────────────────────────────────

class _PtkHistoryAdapter(History):
    """把 InputHistory 适配为 prompt_toolkit 的 History 接口。

    prompt_toolkit 的 History.load_history_strings 要求"从新到旧"yield。
    """

    def __init__(self, inner: InputHistory) -> None:
        super().__init__()
        self._inner = inner

    def load_history_strings(self):
        for s in reversed(list(self._inner)):
            yield s

    def store_string(self, string: str) -> None:
        self._inner.append(string)


# ── 事件分发 ────────────────────────────────────────────────────────────────

WsSendFn = Callable[[Message], Awaitable[None]]
HistoryCb = Callable[[dict], None] | None


class EventDispatcher:
    """把 Service 推送事件分发到 render 函数 + spinner 更新。

    混合模式：
    - 关键事件（PLAN/STEP_RESULT/SUB_STEP_RESULT/TOOL_START/replanning/DONE/ERROR）写持久行
    - 临时状态（thinking/executing 子状态/team:*/summarizing）只更新 spinner
    - 终止状态（idle/aborted/DONE/ERROR）停止 spinner
    """

    def __init__(self, console: Console, state: SundayState,
                 spinner: output.StatusSpinner, ws_send: WsSendFn) -> None:
        self._console = console
        self._state = state
        self._spinner = spinner
        self._ws_send = ws_send
        self._current_step_id: str = ""
        self._history_callback: HistoryCb = None

    def set_history_callback(self, cb: HistoryCb) -> None:
        self._history_callback = cb

    async def dispatch(self, msg: Message) -> None:
        et = msg.type
        payload = msg.data

        if et == EventType.STATUS:
            self._handle_status(payload)
        elif et == EventType.PLAN:
            self._spinner.stop()
            output.render_plan(self._console, payload.get("goal", ""),
                               payload.get("steps", []))
        elif et == EventType.STEP_RESULT:
            self._spinner.stop()
            output.render_step_result(
                self._console,
                payload.get("step_id", ""),
                payload.get("status", ""),
                payload.get("verified"),
            )
        elif et == EventType.SUB_STEP_RESULT:
            output.render_sub_step_result(
                self._console,
                payload.get("sub_step_id", ""),
                payload.get("verified"),
            )
        elif et == EventType.TOOL_START:
            tool_name = payload.get("tool", "")
            args_preview = payload.get("args_preview", "")
            output.render_tool_activity(self._console, tool_name, args_preview)
            self._spinner.update(f"工具调用 {tool_name}")
        elif et == EventType.TOOL_END:
            if self._current_step_id:
                self._spinner.update(f"执行中 {self._current_step_id}")
        elif et == EventType.DONE:
            self._spinner.stop()
            output.render_assistant_message(self._console, payload.get("content", ""))
        elif et == EventType.ERROR:
            self._spinner.stop()
            output.render_error_message(self._console, payload.get("message", "未知错误"))
        elif et == EventType.CONFIRM_REQUEST:
            self._spinner.stop()
            output.render_confirm_request(
                self._console,
                payload.get("tool", ""),
                payload.get("message", ""),
            )
            if self._state.trust_mode:
                await self._ws_send(Message(
                    type=EventType.CONFIRM,
                    session_id=self._state.session_id,
                    data={"confirmed": True},
                ))
        elif et == EventType.SLASH_RESULT:
            self._spinner.stop()
            self._handle_slash_result(payload)
        # 其他事件（PLAN_FACT_CHECK / PLAN_REALTIME_HINTS / TEAM_ERROR）暂不显示

    def _handle_status(self, payload: dict) -> None:
        state = payload.get("status", "")
        if state == "thinking":
            self._spinner.update("思考中...")
        elif state.startswith("executing"):
            step_id = state.split(":", 1)[-1] if ":" in state else ""
            self._current_step_id = step_id
            self._spinner.update(f"执行中 {step_id}" if step_id else "执行中")
        elif state.startswith("team:") and state.endswith(":planning"):
            step_id = state.split(":")[1]
            self._spinner.update(f"子规划 {step_id}")
        elif state.startswith("team:"):
            parts = state.split(":")
            label = f"{parts[1]}/{parts[2]}" if len(parts) >= 3 else state[5:]
            self._spinner.update(f"子步骤 {label}")
        elif state.startswith("simple:"):
            step_id = state.split(":", 1)[1]
            self._current_step_id = step_id
            self._spinner.update(f"执行中 {step_id}")
        elif state == "replanning":
            self._spinner.stop()
            output.render_replan(
                self._console,
                payload.get("step_id", ""),
                payload.get("failure_reason", ""),
            )
            self._spinner.update("重规划中...")
        elif state == "summarizing":
            self._spinner.update("汇总中...")
        elif state == "idle":
            self._spinner.stop()
        elif state == "aborted":
            self._spinner.stop()
            output.render_system_message(self._console, "● 任务已中止")
        elif state == "error":
            self._spinner.stop()
            output.render_error_message(self._console, payload.get("message", ""))
        elif state == "busy":
            output.render_system_message(self._console, payload.get("message", "任务运行中"))

    def _handle_slash_result(self, payload: dict) -> None:
        cmd = payload.get("command", "")
        if cmd == "new":
            new_sid = payload.get("new_session_id", "")
            self._state.session_id = new_sid
            output.render_system_message(self._console, f"新会话已创建：{new_sid}")
        elif cmd == "sessions":
            sessions = payload.get("sessions", [])
            if sessions:
                lines = "\n".join(
                    "  {sid}  ({date})  {title}".format(
                        sid=s["session_id"],
                        date=s.get("last_active", "")[:10],
                        title=s.get("title", "(无标题)"),
                    )
                    for s in sessions
                )
                output.render_system_message(self._console, f"会话列表：\n{lines}")
            else:
                output.render_system_message(self._console, "（无会话）")
        elif cmd == "memory":
            file_name = payload.get("file", "")
            content = payload.get("content", "")
            output.render_system_message(self._console, f"[{file_name}]\n{content}")
        elif cmd == "skills":
            skills = payload.get("skills", [])
            lines = "\n".join(f"  · {s}" for s in skills) or "  (无可用技能)"
            output.render_system_message(self._console, f"可用技能：\n{lines}")
        elif cmd == "delete":
            output.render_system_message(self._console, payload.get("message", "会话已删除"))
        elif cmd == "history":
            if self._history_callback:
                self._history_callback(payload)
            self._print_history_human(payload)
        else:
            if msg_text := payload.get("message"):
                output.render_system_message(self._console, msg_text)

    def _print_history_human(self, payload: dict) -> None:
        output.render_system_message(
            self._console, output.format_history_payload(payload)
        )


# ── Key bindings ────────────────────────────────────────────────────────────

def _build_keybindings(
    send_abort: Callable[[], Awaitable[None]],
    paste_folder: "PasteFolder",
    is_busy: Callable[[], bool],
) -> KeyBindings:
    kb = KeyBindings()

    @kb.add(Keys.BracketedPaste, eager=True)
    def _on_paste(event):
        # 拦截 bracketed-paste：多行粘贴折叠为 [Pasted N lines #xxxxxx]
        # 规范化换行（Windows \r\n / 老 Mac \r）—— 否则 count("\n") 算少
        # eager=True 让 KeyProcessor 在匹配到本 binding 时立即执行、绕过同一 key 的默认 emacs handler
        data = event.data.replace("\r\n", "\n").replace("\r", "\n")
        line_count = data.count("\n") + 1
        logger.info("BracketedPaste 命中：%d 字符 / %d 行", len(data), line_count)
        folded = paste_folder.maybe_fold(data)
        event.current_buffer.insert_text(folded)

    @kb.add("enter")
    def _on_enter(event):
        buf = event.current_buffer
        text = buf.text
        # 反斜杠续行：行尾 `\` 删 \ 并换行
        if text.endswith("\\"):
            buf.delete_before_cursor(count=1)
            buf.insert_text("\n")
            return
        if not text.strip():
            return  # 空输入忽略
        buf.validate_and_handle()

    # 各种"换行"组合（终端兼容性 fallback）
    @kb.add("c-j")  # Ctrl+J = LF；许多终端会把 Ctrl+Enter 映射为 c-j
    def _newline_cj(event):
        event.current_buffer.insert_text("\n")

    @kb.add("escape", "enter")  # Alt+Enter（meta-enter）
    def _newline_alt(event):
        event.current_buffer.insert_text("\n")

    @kb.add("up")
    def _on_up(event):
        buf = event.current_buffer
        if buf.document.cursor_position_row == 0:
            buf.history_backward()
        else:
            buf.cursor_up()

    @kb.add("down")
    def _on_down(event):
        buf = event.current_buffer
        if buf.document.cursor_position_row == buf.document.line_count - 1:
            buf.history_forward()
        else:
            buf.cursor_down()

    @kb.add("pageup")
    def _on_pgup(event):
        event.current_buffer.history_backward()

    @kb.add("pagedown")
    def _on_pgdn(event):
        event.current_buffer.history_forward()

    @kb.add("escape", eager=True)
    def _on_escape(event):
        # busy（spinner 在跑）→ 中止当前任务；空闲 → 退出 TUI
        # 用 result=None 路径而非 exception=EOFError，绕开 patch_stdout 异常传播潜在死路
        if is_busy():
            event.app.create_background_task(send_abort())
        else:
            event.app.exit(result=None)

    return kb


# ── 主入口 ────────────────────────────────────────────────────────────────

async def _async_main(port: int) -> None:
    console = Console()

    if not spawn_service_if_needed(port=port):
        console.print(
            "[bold red]Service 启动失败，请检查 ~/.sunday/logs/service.log[/bold red]"
        )
        sys.exit(1)

    service_url = f"ws://localhost:{port}"
    state = SundayState(
        session_id=new_session_id(),
        model_id=settings.sunday.model.id,
        thinking_level=settings.sunday.reasoning.thinking_level,
        service_url=service_url,
    )

    input_history = InputHistory(maxlen=settings.sunday.tui.input_history_size)
    paste_folder = PasteFolder(threshold=settings.sunday.tui.paste_fold_threshold)
    spinner = output.StatusSpinner(console)

    async with ServiceClient(port=port) as client:
        async def ws_send(msg: Message) -> None:
            await client._ws.send(msg.to_json())

        async def send_abort() -> None:
            await ws_send(Message(type=EventType.ABORT, session_id=state.session_id))

        dispatcher = EventDispatcher(console, state, spinner, ws_send)
        slash = SlashCommandHandler(console, state, ws_send)

        def on_history(payload: dict) -> None:
            input_history.load_from_turns(payload.get("turns") or [])
        dispatcher.set_history_callback(on_history)

        async def recv_loop():
            try:
                async for raw in client._ws:
                    try:
                        msg = Message.from_json(raw)
                    except Exception as e:
                        logger.warning("消息解析失败：%s", e)
                        continue
                    await dispatcher.dispatch(msg)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("接收循环异常退出：%s", e)

        recv_task = asyncio.create_task(recv_loop())

        # 启动 banner + 初始化输入历史
        output.render_banner(
            console,
            session_id=state.session_id,
            model_id=state.model_id,
            thinking_level=state.thinking_level,
            service_url=service_url,
        )
        await slash.handle("/history")

        kb = _build_keybindings(send_abort, paste_folder, lambda: spinner.is_running)
        ptk_style = Style.from_dict({
            "frame.border": "ansibrightcyan",
            "bottom-toolbar": "ansibrightblack",
            "status-line": "ansibrightcyan",
        })

        _DEFAULT_HINT = "Enter 提交  ·  Ctrl+J 换行  ·  ↑↓ 历史  ·  /help 命令  ·  Esc 中止"

        # 输入 buffer：multiline + history + accept_handler(让 Enter 触发 app.exit)
        def _accept(buf: Buffer) -> bool:
            get_app().exit(result=buf.text)
            return True  # reset buffer（兜底，主循环还会再 reset 一次）

        input_buffer = Buffer(
            multiline=True,
            history=_PtkHistoryAdapter(input_history),
            enable_history_search=False,
            accept_handler=_accept,
        )

        # 行前缀：首行显示 [用户]，续行显示 .... （与原 prompt_continuation 一致）
        _PROMPT_FIRST = ANSI("\x1b[1;36m[用户]\x1b[0m ")
        _PROMPT_CONT = ".... "
        def _line_prefix(line_number: int, wrap_count: int):
            if line_number == 0 and wrap_count == 0:
                return _PROMPT_FIRST
            return _PROMPT_CONT

        input_window = Window(
            content=BufferControl(buffer=input_buffer),
            get_line_prefix=_line_prefix,
            dont_extend_height=True,
            height=D(min=1),
            wrap_lines=True,
        )

        # spinner 行：等待输入期间始终占 1 行（spinner 没跑时显示空白）。
        # Why not `is_running & ~is_done`：那样 layout 高度会随 spinner 启停切换 1↔2 行，
        # 触发 prompt_toolkit non-full-screen 的"lift"机制 —— 每帧 spinner 都被推到
        # scrollback。固定占 1 行让 layout 高度只在 is_done 翻转那一次变化，不会污染 scrollback。
        status_window = ConditionalContainer(
            content=Window(
                content=FormattedTextControl(text=lambda: spinner.toolbar_text("")),
                height=1,
                style="class:status-line",
            ),
            filter=~is_done,
        )

        # 固定 toolbar（始终显示快捷键，提交瞬间随 app 退出消失）
        toolbar_window = ConditionalContainer(
            content=Window(
                content=FormattedTextControl(text=lambda: _DEFAULT_HINT),
                height=1,
                style="class:bottom-toolbar",
            ),
            filter=~is_done,
        )

        # Frame 仅在等待输入时显示，提交后自动消失，让输入文本以裸行留在 scrollback
        framed_input = ConditionalContainer(
            content=Frame(input_window),
            filter=~is_done,
        )
        bare_input = ConditionalContainer(
            content=input_window,
            filter=is_done,
        )

        root_layout = HSplit([
            status_window,
            framed_input,
            bare_input,
            toolbar_window,
        ])

        app: Application[str] = Application(
            layout=Layout(root_layout, focused_element=input_window),
            key_bindings=kb,
            style=ptk_style,
            full_screen=False,
            mouse_support=False,
            refresh_interval=0.1,  # 让 spinner 帧动画转起来
            erase_when_done=False,
        )

        try:
            while True:
                input_buffer.reset()
                try:
                    with patch_stdout(raw=True):
                        text = await app.run_async()
                except (EOFError, KeyboardInterrupt):
                    break

                if text is None:
                    break
                text = paste_folder.expand(text)
                paste_folder.reset()
                text = text.strip()
                if not text:
                    continue

                if text.startswith("/"):
                    await slash.handle(text)
                    continue

                input_history.append(text)
                await ws_send(Message(
                    type=EventType.SEND,
                    session_id=state.session_id,
                    data={
                        "content": text,
                        "thinking_level": state.thinking_level,
                        "model_override": state.model_override,
                    },
                ))
        finally:
            spinner.stop()
            recv_task.cancel()
            try:
                await recv_task
            except (asyncio.CancelledError, Exception):
                pass

    console.print("\n[dim]再见 👋[/dim]")


def run(port: int = 7899) -> None:
    """同步入口（click 命令调用）。"""
    try:
        asyncio.run(_async_main(port=port))
    except KeyboardInterrupt:
        pass
