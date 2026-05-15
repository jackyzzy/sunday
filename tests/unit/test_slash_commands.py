"""SlashCommandHandler 单元测试（新 API：console + state + ws_send）。"""
from __future__ import annotations

from io import StringIO
from unittest.mock import AsyncMock

import pytest
from rich.console import Console

from sunday.service.protocol import EventType, Message
from sunday.tui.cli import SundayState
from sunday.tui.commands import SlashCommandHandler


def _make_handler():
    """构造一个隔离的 handler 三件套：console+state+ws_send（mock）。"""
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, no_color=True, markup=True, width=200)
    state = SundayState(
        session_id="sess001",
        model_id="anthropic/claude-sonnet-4-6",
        thinking_level="medium",
        service_url="ws://localhost:7899",
    )
    ws_send = AsyncMock()
    handler = SlashCommandHandler(console, state, ws_send)
    return handler, state, ws_send, buf


# ── 本地命令（无 ws 往返）──────────────────────────────────────────────────

async def test_think_sets_level_in_state():
    handler, state, ws_send, buf = _make_handler()
    await handler.handle("/think high")
    assert state.thinking_level == "high"
    assert "high" in buf.getvalue()
    ws_send.assert_not_called()


async def test_think_invalid_level_prints_error():
    handler, state, ws_send, buf = _make_handler()
    await handler.handle("/think invalid")
    assert state.thinking_level == "medium"  # 不变
    out = buf.getvalue()
    assert "invalid" in out or "无效" in out or "错误" in out
    ws_send.assert_not_called()


async def test_model_sets_override():
    handler, state, _, _ = _make_handler()
    await handler.handle("/model openai/gpt-4")
    assert state.model_override == "openai/gpt-4"


async def test_trust_enables_trust_mode():
    handler, state, _, buf = _make_handler()
    assert state.trust_mode is False
    await handler.handle("/trust")
    assert state.trust_mode is True
    assert "信任" in buf.getvalue()


async def test_info_prints_current_state():
    handler, state, _, buf = _make_handler()
    await handler.handle("/info")
    out = buf.getvalue()
    assert state.session_id in out
    assert state.model_id in out
    assert "medium" in out  # thinking_level


async def test_help_returns_help_text():
    handler, _, _, buf = _make_handler()
    await handler.handle("/help")
    out = buf.getvalue()
    assert "/think" in out
    assert "/model" in out
    assert "/abort" in out
    assert "/info" in out  # 新增命令也要出现


async def test_unknown_command_prints_error():
    handler, _, _, buf = _make_handler()
    await handler.handle("/unknown_cmd_xyz")
    out = buf.getvalue()
    assert "unknown_cmd_xyz" in out or "未知" in out


# ── 远程命令（需要通过 ws_send 推到 Service）────────────────────────────────

async def test_abort_sends_abort_message():
    handler, state, ws_send, _ = _make_handler()
    await handler.handle("/abort")
    ws_send.assert_called_once()
    msg = ws_send.call_args[0][0]
    assert isinstance(msg, Message)
    assert msg.type == EventType.ABORT
    assert msg.session_id == "sess001"


async def test_new_sends_slash_new():
    handler, _, ws_send, _ = _make_handler()
    await handler.handle("/new")
    ws_send.assert_called_once()
    msg = ws_send.call_args[0][0]
    assert msg.type == EventType.SLASH
    assert msg.data.get("command") == "new"


async def test_sessions_sends_slash_sessions():
    handler, _, ws_send, _ = _make_handler()
    await handler.handle("/sessions")
    ws_send.assert_called_once()
    msg = ws_send.call_args[0][0]
    assert msg.type == EventType.SLASH
    assert msg.data.get("command") == "sessions"


async def test_history_sends_slash_history():
    handler, _, ws_send, _ = _make_handler()
    await handler.handle("/history")
    ws_send.assert_called_once()
    msg = ws_send.call_args[0][0]
    assert msg.data.get("command") == "history"


async def test_memory_passes_args_default_MEMORY():
    """/memory 不带参数，默认请求 MEMORY 文件。"""
    handler, _, ws_send, _ = _make_handler()
    await handler.handle("/memory")
    msg = ws_send.call_args[0][0]
    assert msg.data.get("command") == "memory"
    assert msg.data.get("args") == "MEMORY"


async def test_memory_passes_args_explicit():
    """/memory USER 透传 args。"""
    handler, _, ws_send, _ = _make_handler()
    await handler.handle("/memory USER")
    msg = ws_send.call_args[0][0]
    assert msg.data.get("args") == "USER"


async def test_session_switch_changes_state_and_fetches_history():
    """/session <id> 修改 state.session_id 并触发 /history。"""
    handler, state, ws_send, _ = _make_handler()
    await handler.handle("/session new-session-id")
    assert state.session_id == "new-session-id"
    # 应至少发送一条 /history（_cmd_session 调用 _cmd_history）
    history_calls = [
        c.args[0] for c in ws_send.call_args_list
        if c.args[0].data.get("command") == "history"
    ]
    assert len(history_calls) == 1


async def test_session_id_normalizes_underscore_to_dash():
    """/session abc_def 标准化为 abc-def（UUID 格式）。"""
    handler, state, _, _ = _make_handler()
    await handler.handle("/session abc_def_ghi")
    assert state.session_id == "abc-def-ghi"


async def test_delete_without_args_prints_error():
    handler, _, ws_send, buf = _make_handler()
    await handler.handle("/delete")
    out = buf.getvalue()
    assert "session" in out.lower() or "用法" in out or "请指定" in out
    ws_send.assert_not_called()


async def test_delete_with_args_sends_slash_delete():
    handler, _, ws_send, _ = _make_handler()
    await handler.handle("/delete abc_def-123")
    ws_send.assert_called_once()
    msg = ws_send.call_args[0][0]
    assert msg.data.get("command") == "delete"
    assert msg.data.get("args") == "abc-def-123"  # 下划线已标准化
