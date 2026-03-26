"""Phase 5：SlashCommandHandler — Slash 命令解析与执行"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

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
  /help             显示此帮助
"""

VALID_THINKING_LEVELS = {"off", "minimal", "low", "medium", "high"}


class SlashCommandHandler:
    """解析并执行 Slash 命令。

    app: TUI App 对象（持有 session_id、thinking_level 等状态）
    ws:  WebSocket 连接（用于向 Gateway 发送消息）
    """

    def __init__(self, app, ws) -> None:
        self._app = app
        self._ws = ws

    async def handle(self, text: str) -> str | None:
        """解析并执行命令，返回要显示的反馈文本（或 None 表示无输出）。"""
        if not text.startswith("/"):
            return None
        parts = text.strip().split(maxsplit=1)
        cmd = parts[0].lstrip("/").lower()
        args = parts[1].strip() if len(parts) > 1 else ""

        if cmd == "think":
            return await self._cmd_think(args)
        if cmd == "model":
            return await self._cmd_model(args)
        if cmd == "abort":
            return await self._cmd_abort()
        if cmd == "new":
            return await self._cmd_new()
        if cmd == "sessions":
            return await self._cmd_sessions()
        if cmd == "session":
            return await self._cmd_session(args)
        if cmd == "reset":
            return await self._cmd_reset()
        if cmd == "memory":
            return await self._cmd_memory(args)
        if cmd == "skills":
            return await self._cmd_skills()
        if cmd == "help":
            return HELP_TEXT

        return f"[错误] 未知命令：/{cmd}（输入 /help 查看帮助）"

    # ── 各命令实现 ────────────────────────────────────────────────────────

    async def _cmd_think(self, args: str) -> str:
        if args not in VALID_THINKING_LEVELS:
            return f"[错误] invalid 思考等级，可选：{', '.join(VALID_THINKING_LEVELS)}"
        self._app.thinking_level = args
        return f"思考深度已设置为：{args}"

    async def _cmd_model(self, args: str) -> str:
        if not args:
            return f"当前模型：{self._app.model_override or '默认'}"
        self._app.model_override = args
        return f"模型已切换为：{args}"

    async def _cmd_abort(self) -> str:
        from sunday.gateway.protocol import EventType, Message
        msg = Message(type=EventType.ABORT, session_id=self._app.session_id)
        await self._ws.send(msg.to_json())
        return "已发送中止请求"

    async def _cmd_new(self) -> str:
        from sunday.gateway.protocol import EventType, Message
        msg = Message(type=EventType.SLASH, session_id=self._app.session_id,
                      data={"command": "new", "args": ""})
        await self._ws.send(msg.to_json())
        return "已请求创建新会话"

    async def _cmd_sessions(self) -> str:
        from sunday.gateway.protocol import EventType, Message
        msg = Message(type=EventType.SLASH, session_id=self._app.session_id,
                      data={"command": "sessions", "args": ""})
        await self._ws.send(msg.to_json())
        return None  # 结果由 Gateway 事件推回

    async def _cmd_session(self, args: str) -> str:
        if not args:
            return f"当前会话：{self._app.session_id}"
        self._app.session_id = args
        return f"已切换到会话：{args}"

    async def _cmd_reset(self) -> str:
        from sunday.gateway.protocol import EventType, Message
        msg = Message(type=EventType.SLASH, session_id=self._app.session_id,
                      data={"command": "reset", "args": ""})
        await self._ws.send(msg.to_json())
        return "会话上下文已重置"

    async def _cmd_memory(self, args: str) -> str:
        from sunday.gateway.protocol import EventType, Message
        msg = Message(type=EventType.SLASH, session_id=self._app.session_id,
                      data={"command": "memory", "args": args or "MEMORY"})
        await self._ws.send(msg.to_json())
        return None  # 结果由 Gateway 推回

    async def _cmd_skills(self) -> str:
        from sunday.gateway.protocol import EventType, Message
        msg = Message(type=EventType.SLASH, session_id=self._app.session_id,
                      data={"command": "skills", "args": ""})
        await self._ws.send(msg.to_json())
        return None
