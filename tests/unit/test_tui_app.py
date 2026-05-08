"""TUI 应用测试（Textual Pilot，mock WebSocket）。

注意：自 v0.2 改造起：
- 移除全部 copy_mode 相关测试（功能已彻底删除，由 mouse=False + 终端原生选区取代）
- InputBar 不再使用 textual.widgets.Input，改用自定义 PromptTextArea
"""
from __future__ import annotations


async def test_app_mounts_without_error():
    """TUI 启动不报错，基本 DOM 可挂载"""
    from sunday.tui.app import SundayApp

    app = SundayApp(service_url="ws://localhost:7899", auto_connect=False)
    async with app.run_test(headless=True) as pilot:
        assert pilot.app is not None


async def test_input_bar_visible():
    """InputBar 组件存在于 DOM"""
    from sunday.tui.app import SundayApp
    from sunday.tui.widgets.input_bar import InputBar

    app = SundayApp(service_url="ws://localhost:7899", auto_connect=False)
    async with app.run_test(headless=True) as pilot:
        assert pilot.app.query_one(InputBar) is not None


async def test_status_bar_visible():
    """StatusBar 组件存在于 DOM"""
    from sunday.tui.app import SundayApp
    from sunday.tui.widgets.status_bar import StatusBar

    app = SundayApp(service_url="ws://localhost:7899", auto_connect=False)
    async with app.run_test(headless=True) as pilot:
        assert pilot.app.query_one(StatusBar) is not None


async def test_chat_log_visible():
    """ChatLog 组件存在于 DOM"""
    from sunday.tui.app import SundayApp
    from sunday.tui.widgets.chat_log import ChatLog

    app = SundayApp(service_url="ws://localhost:7899", auto_connect=False)
    async with app.run_test(headless=True) as pilot:
        assert pilot.app.query_one(ChatLog) is not None


async def test_send_message_via_input():
    """InputBar 输入文字后回车，ChatLog 追加用户消息"""
    from sunday.tui.app import SundayApp
    from sunday.tui.widgets.chat_log import ChatLog
    from sunday.tui.widgets.input_bar import InputBar

    app = SundayApp(service_url="ws://localhost:7899", auto_connect=False)
    async with app.run_test(headless=True) as pilot:
        # 直接触发 InputBar.Submitted 消息模拟用户输入
        input_bar = pilot.app.query_one(InputBar)
        input_bar.post_message(InputBar.Submitted("你好世界"))
        await pilot.pause()
        chat_log = pilot.app.query_one(ChatLog)
        assert "你好世界" in chat_log.renderable_text


async def test_status_updates_on_event():
    """app.handle_gateway_event 收到 status 消息后 StatusBar 更新"""
    from sunday.service.protocol import EventType
    from sunday.tui.app import SundayApp
    from sunday.tui.widgets.status_bar import StatusBar

    app = SundayApp(service_url="ws://localhost:7899", auto_connect=False)
    async with app.run_test(headless=True) as pilot:
        await pilot.app.handle_gateway_event(
            {"type": EventType.STATUS.value, "data": {"status": "thinking"},
             "session_id": "s1", "ts": ""}
        )
        bar = pilot.app.query_one(StatusBar)
        assert "思考" in bar.status_text or "thinking" in bar.status_text.lower()


async def test_status_hint_set_on_mount():
    """启动后状态栏底部固定行包含核心快捷键提示"""
    from sunday.tui.app import SundayApp
    from sunday.tui.widgets.status_bar import StatusBar

    app = SundayApp(service_url="ws://localhost:7899", auto_connect=False)
    async with app.run_test(headless=True) as pilot:
        bar = pilot.app.query_one(StatusBar)
        assert "Enter 发送" in bar.hint_text
        assert "Ctrl+Enter" in bar.hint_text
        assert "鼠标拖拽" in bar.hint_text


async def test_history_loaded_from_slash_history_payload():
    """SLASH_RESULT cmd=history payload 应该用 turns 重建 InputHistory"""
    from sunday.service.protocol import EventType
    from sunday.tui.app import SundayApp

    app = SundayApp(service_url="ws://localhost:7899", auto_connect=False)
    async with app.run_test(headless=True) as pilot:
        # 模拟 Service 返回 /history 响应
        await pilot.app.handle_gateway_event(
            {
                "type": EventType.SLASH_RESULT.value,
                "data": {
                    "command": "history",
                    "session_id": "s1",
                    "session_thread": {},
                    "turns": [
                        {"turn_index": 1, "user_input": "first query"},
                        {"turn_index": 2, "user_input": "second query"},
                    ],
                },
                "session_id": "s1",
                "ts": "",
            }
        )
        history = pilot.app._input_history
        assert list(history) == ["first query", "second query"]


async def test_history_cleared_on_new_session():
    """SLASH_RESULT cmd=new 应清空 InputHistory（避免污染新会话）"""
    from sunday.service.protocol import EventType
    from sunday.tui.app import SundayApp

    app = SundayApp(service_url="ws://localhost:7899", auto_connect=False)
    async with app.run_test(headless=True) as pilot:
        # 先填一些历史
        pilot.app._input_history.append("old query 1")
        pilot.app._input_history.append("old query 2")
        # 模拟 Service 创建新会话
        await pilot.app.handle_gateway_event(
            {
                "type": EventType.SLASH_RESULT.value,
                "data": {"command": "new", "new_session_id": "newid"},
                "session_id": "newid",
                "ts": "",
            }
        )
        assert len(pilot.app._input_history) == 0


async def test_no_copy_mode_state_remains():
    """改造后 SundayApp 应不再持有 _copy_mode 状态"""
    from sunday.tui.app import SundayApp

    app = SundayApp(service_url="ws://localhost:7899", auto_connect=False)
    async with app.run_test(headless=True) as pilot:
        assert not hasattr(pilot.app, "_copy_mode")
        assert not hasattr(pilot.app, "action_toggle_copy_mode")
        assert not hasattr(pilot.app, "_set_terminal_mouse")
        assert not hasattr(pilot.app, "_promote_selection_to_clipboard")
