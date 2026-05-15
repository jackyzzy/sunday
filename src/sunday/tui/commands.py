"""SlashCommandHandler — Slash 命令解析与执行（无 UI 框架耦合版本）。

设计：依赖 `console + state + ws` 三件套，所有 UI 操作通过 console.print 输出，
不再引用任何 widget tree。新增 `/info` 命令查询当前会话信息。
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sunday.service.protocol import EventType, Message
from sunday.tui import output

if TYPE_CHECKING:
    from rich.console import Console
    from sunday.tui.cli import SundayState

logger = logging.getLogger(__name__)

HELP_TEXT = """\
Sunday Slash 命令：
  /think <level>    设置思考深度 (off/minimal/low/medium/high)
  /model <id>       临时切换模型 (provider/model-id)
  /session <id>     切换到指定会话
  /sessions         列出所有会话
  /new              开始新会话
  /reset            重置当前会话上下文
  /abort            中止当前运行任务
  /memory [file]    查看记忆文件 (SOUL/MEMORY/USER/TOOLS)
  /skills           列出可用技能
  /history          查看当前会话历史
  /delete <id>      删除指定会话（清除日志与报告，不可恢复）
  /trust            启用信任模式，危险操作自动确认（当前会话有效）
  /info             显示当前会话/模型/连接信息
  /help             显示此帮助

输入 / 复制粘贴：
  Enter             发送当前消息
  Ctrl+Enter        换行
  行尾 \\ + Enter    反斜杠续行
  ↑ / ↓             历史回溯（光标在首/末行触发；当前 session 内）
  Esc               中止当前任务
  Ctrl+D            退出 TUI
  鼠标拖拽 / 右键    终端原生选区/粘贴（终端默认行为）
  Shift+PgUp / 滚轮  终端原生 scrollback 翻阅历史输出
"""

VALID_THINKING_LEVELS = {"off", "minimal", "low", "medium", "high"}


class SlashCommandHandler:
    """解析并执行 Slash 命令。

    console: 用于本地输出
    state:   SundayState（持有 session_id / thinking / model 等）
    ws_send: 异步函数 `(msg: Message) -> None`，由 cli.py 注入；
             通过函数注入而非传 ws 对象，让 handler 不感知 ServiceClient 细节
    """

    def __init__(self, console: "Console", state: "SundayState",
                 ws_send) -> None:
        self._console = console
        self._state = state
        self._ws_send = ws_send  # async callable

    async def handle(self, text: str) -> None:
        """解析并执行命令；输出由方法内部直接 console.print。"""
        if not text.startswith("/"):
            return
        parts = text.strip().split(maxsplit=1)
        cmd = parts[0].lstrip("/").lower()
        args = parts[1].strip() if len(parts) > 1 else ""

        handlers = {
            "think": self._cmd_think,
            "model": self._cmd_model,
            "abort": self._cmd_abort,
            "new": self._cmd_new,
            "sessions": self._cmd_sessions,
            "session": self._cmd_session,
            "reset": self._cmd_reset,
            "memory": self._cmd_memory,
            "skills": self._cmd_skills,
            "history": self._cmd_history,
            "delete": self._cmd_delete,
            "trust": self._cmd_trust,
            "info": self._cmd_info,
            "help": self._cmd_help,
        }
        handler = handlers.get(cmd)
        if handler is None:
            output.render_error_message(
                self._console, f"未知命令：/{cmd}（输入 /help 查看帮助）"
            )
            return
        # 所有 handler 统一签名 (args: str) -> None，未用到 args 的方法直接忽略
        await handler(args)

    # ── 本地处理（无需 Service 往返）────────────────────────────────────────

    async def _cmd_help(self, args: str) -> None:
        output.render_system_message(self._console, HELP_TEXT)

    async def _cmd_info(self, args: str) -> None:
        output.render_info(
            self._console,
            session_id=self._state.session_id,
            model_id=self._state.model_id,
            model_override=self._state.model_override,
            thinking_level=self._state.thinking_level,
            service_url=self._state.service_url,
            trust_mode=self._state.trust_mode,
        )

    async def _cmd_think(self, args: str) -> None:
        if args not in VALID_THINKING_LEVELS:
            output.render_error_message(
                self._console,
                f"invalid 思考等级，可选：{', '.join(sorted(VALID_THINKING_LEVELS))}",
            )
            return
        self._state.thinking_level = args
        output.render_system_message(self._console, f"思考深度已设置为：{args}")

    async def _cmd_model(self, args: str) -> None:
        if not args:
            output.render_system_message(
                self._console,
                f"当前模型：{self._state.model_override or self._state.model_id}",
            )
            return
        self._state.model_override = args
        output.render_system_message(self._console, f"模型已切换为：{args}")

    async def _cmd_trust(self, args: str) -> None:
        self._state.trust_mode = True
        output.render_system_message(
            self._console,
            "已启用信任模式，当前会话的危险操作将自动确认。",
        )

    async def _cmd_session(self, args: str) -> None:
        if not args:
            output.render_system_message(
                self._console, f"当前会话：{self._state.session_id}"
            )
            return
        normalized = args.replace("_", "-")
        self._state.session_id = normalized
        output.render_system_message(self._console, f"已切换到会话：{normalized}")
        # 异步拉取目标 session 历史
        await self._cmd_history("")

    # ── 需要 Service 处理的命令（结果通过 SLASH_RESULT 事件推回）────────────

    async def _cmd_abort(self, args: str) -> None:
        await self._ws_send(Message(
            type=EventType.ABORT, session_id=self._state.session_id,
        ))
        output.render_system_message(self._console, "已发送中止请求")

    async def _cmd_new(self, args: str) -> None:
        await self._ws_send(Message(
            type=EventType.SLASH, session_id=self._state.session_id,
            data={"command": "new", "args": ""},
        ))

    async def _cmd_sessions(self, args: str) -> None:
        await self._ws_send(Message(
            type=EventType.SLASH, session_id=self._state.session_id,
            data={"command": "sessions", "args": ""},
        ))

    async def _cmd_reset(self, args: str) -> None:
        await self._ws_send(Message(
            type=EventType.SLASH, session_id=self._state.session_id,
            data={"command": "reset", "args": ""},
        ))
        output.render_system_message(self._console, "会话上下文已重置")

    async def _cmd_memory(self, args: str) -> None:
        await self._ws_send(Message(
            type=EventType.SLASH, session_id=self._state.session_id,
            data={"command": "memory", "args": args or "MEMORY"},
        ))

    async def _cmd_skills(self, args: str) -> None:
        await self._ws_send(Message(
            type=EventType.SLASH, session_id=self._state.session_id,
            data={"command": "skills", "args": ""},
        ))

    async def _cmd_history(self, args: str) -> None:
        await self._ws_send(Message(
            type=EventType.SLASH, session_id=self._state.session_id,
            data={"command": "history", "args": ""},
        ))

    async def _cmd_delete(self, args: str) -> None:
        if not args:
            output.render_error_message(
                self._console,
                "请指定要删除的 session ID，用法：/delete <session_id>",
            )
            return
        normalized = args.strip().replace("_", "-")
        await self._ws_send(Message(
            type=EventType.SLASH, session_id=self._state.session_id,
            data={"command": "delete", "args": normalized},
        ))
