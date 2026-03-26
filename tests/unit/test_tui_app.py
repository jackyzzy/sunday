"""T5-5/T5-6 验证：TUI 应用测试（Textual Pilot，mock WebSocket）"""
from __future__ import annotations


async def test_app_mounts_without_error():
    """TUI 启动不报错，基本 DOM 可挂载"""
    from sunday.tui.app import SundayApp

    app = SundayApp(gateway_url="ws://localhost:7899", auto_connect=False)
    async with app.run_test(headless=True) as pilot:
        assert pilot.app is not None


async def test_input_bar_visible():
    """InputBar 组件存在于 DOM"""
    from sunday.tui.app import SundayApp
    from sunday.tui.widgets.input_bar import InputBar

    app = SundayApp(gateway_url="ws://localhost:7899", auto_connect=False)
    async with app.run_test(headless=True) as pilot:
        assert pilot.app.query_one(InputBar) is not None


async def test_status_bar_visible():
    """StatusBar 组件存在于 DOM"""
    from sunday.tui.app import SundayApp
    from sunday.tui.widgets.status_bar import StatusBar

    app = SundayApp(gateway_url="ws://localhost:7899", auto_connect=False)
    async with app.run_test(headless=True) as pilot:
        assert pilot.app.query_one(StatusBar) is not None


async def test_chat_log_visible():
    """ChatLog 组件存在于 DOM"""
    from sunday.tui.app import SundayApp
    from sunday.tui.widgets.chat_log import ChatLog

    app = SundayApp(gateway_url="ws://localhost:7899", auto_connect=False)
    async with app.run_test(headless=True) as pilot:
        assert pilot.app.query_one(ChatLog) is not None


async def test_send_message_via_input():
    """InputBar 输入文字后回车，ChatLog 追加用户消息"""
    from sunday.tui.app import SundayApp
    from sunday.tui.widgets.chat_log import ChatLog
    from sunday.tui.widgets.input_bar import InputBar

    app = SundayApp(gateway_url="ws://localhost:7899", auto_connect=False)
    async with app.run_test(headless=True) as pilot:
        # 直接触发 InputBar.Submitted 消息模拟用户输入
        input_bar = pilot.app.query_one(InputBar)
        input_bar.post_message(InputBar.Submitted("你好世界"))
        await pilot.pause()
        chat_log = pilot.app.query_one(ChatLog)
        assert "你好世界" in chat_log.renderable_text


async def test_status_updates_on_event():
    """app.handle_gateway_event 收到 status 消息后 StatusBar 更新"""
    from sunday.gateway.protocol import EventType
    from sunday.tui.app import SundayApp
    from sunday.tui.widgets.status_bar import StatusBar

    app = SundayApp(gateway_url="ws://localhost:7899", auto_connect=False)
    async with app.run_test(headless=True) as pilot:
        await pilot.app.handle_gateway_event(
            {"type": EventType.STATUS.value, "data": {"state": "thinking"},
             "session_id": "s1", "ts": ""}
        )
        bar = pilot.app.query_one(StatusBar)
        assert "思考" in bar.status_text or "thinking" in bar.status_text.lower()
