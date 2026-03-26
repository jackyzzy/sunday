"""T5-7 验证：SlashCommandHandler 单元测试"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from sunday.tui.commands import SlashCommandHandler


def _make_handler():
    ws = MagicMock()
    ws.send = AsyncMock()
    app = MagicMock()
    app.session_id = "sess001"
    app.thinking_level = "medium"
    app.model_override = None
    handler = SlashCommandHandler(app=app, ws=ws)
    return handler, app, ws


async def test_think_sets_level():
    """/think high 更新 app.thinking_level"""
    handler, app, ws = _make_handler()
    result = await handler.handle("/think high")
    assert app.thinking_level == "high"
    assert result is not None


async def test_think_invalid_level():
    """/think invalid 返回错误提示"""
    handler, app, _ = _make_handler()
    result = await handler.handle("/think invalid")
    assert "error" in result.lower() or "invalid" in result.lower() or "无效" in result


async def test_model_sets_override():
    """/model openai/gpt-4 更新 app.model_override"""
    handler, app, _ = _make_handler()
    await handler.handle("/model openai/gpt-4")
    assert app.model_override == "openai/gpt-4"


async def test_abort_sends_abort_message():
    """/abort 通过 ws 发送 abort 消息"""
    from sunday.gateway.protocol import EventType, Message
    handler, app, ws = _make_handler()
    await handler.handle("/abort")
    ws.send.assert_called_once()
    sent = ws.send.call_args[0][0]
    msg = Message.from_json(sent)
    assert msg.type == EventType.ABORT


async def test_new_sends_slash_new():
    """/new 发送 slash new 消息"""
    from sunday.gateway.protocol import EventType, Message
    handler, app, ws = _make_handler()
    await handler.handle("/new")
    ws.send.assert_called_once()
    sent = ws.send.call_args[0][0]
    msg = Message.from_json(sent)
    assert msg.type == EventType.SLASH
    assert msg.data.get("command") == "new"


async def test_sessions_sends_slash_sessions():
    """/sessions 发送 slash sessions 消息"""
    from sunday.gateway.protocol import EventType, Message
    handler, app, ws = _make_handler()
    await handler.handle("/sessions")
    ws.send.assert_called_once()
    sent = ws.send.call_args[0][0]
    msg = Message.from_json(sent)
    assert msg.type == EventType.SLASH
    assert msg.data.get("command") == "sessions"


async def test_help_returns_help_text():
    """/help 返回包含命令列表的帮助文本"""
    handler, _, _ = _make_handler()
    result = await handler.handle("/help")
    assert "/think" in result
    assert "/model" in result
    assert "/abort" in result


async def test_unknown_command_returns_error():
    """未知命令返回错误提示"""
    handler, _, _ = _make_handler()
    result = await handler.handle("/unknown_cmd_xyz")
    assert "unknown" in result.lower() or "未知" in result or "error" in result.lower()
