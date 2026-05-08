"""TUI 输入组件测试：InputHistory + PromptTextArea。

覆盖 plan「Sunday TUI 复制粘贴 / 多行输入 / 历史回溯 彻底改造方案」
中的核心契约：
- InputHistory：相邻去重、maxlen 截断、load_from_turns 切 session
- PromptTextArea：Enter=提交、Ctrl+Enter=换行、反斜杠续行、>4 行粘贴折叠为占位符
"""
from __future__ import annotations

import pytest

# ──────────────────────────────────────────────────────────────────────────────
# InputHistory（纯逻辑，无 Textual 依赖）
# ──────────────────────────────────────────────────────────────────────────────


def test_input_history_append_basic():
    from sunday.tui.widgets.input_history import InputHistory

    h = InputHistory()
    h.append("alpha")
    h.append("beta")
    assert len(h) == 2
    assert h[0] == "alpha"
    assert h[1] == "beta"


def test_input_history_append_dedup_consecutive():
    """相邻重复不入栈（连续敲两次同样的 query 只存一份）。"""
    from sunday.tui.widgets.input_history import InputHistory

    h = InputHistory()
    h.append("hello")
    h.append("hello")  # 相邻重复
    h.append("hello")  # 再次
    h.append("world")
    h.append("hello")  # 非相邻：允许再次入栈
    assert list(h) == ["hello", "world", "hello"]


def test_input_history_maxlen_truncates_old():
    """超过 maxlen 后旧条目被 deque 自动丢弃。"""
    from sunday.tui.widgets.input_history import InputHistory

    h = InputHistory(maxlen=3)
    for q in ["a", "b", "c", "d", "e"]:
        h.append(q)
    assert len(h) == 3
    assert list(h) == ["c", "d", "e"]


def test_input_history_load_from_turns_extracts_user_input():
    from sunday.tui.widgets.input_history import InputHistory

    h = InputHistory()
    h.load_from_turns([
        {"turn_id": "t001", "turn_index": 1, "user_input": "alpha"},
        {"turn_id": "t002", "turn_index": 2, "user_input": "beta"},
    ])
    assert list(h) == ["alpha", "beta"]


def test_input_history_load_from_turns_clears_old():
    """从 session A 切到 B，A 的历史不污染 B。"""
    from sunday.tui.widgets.input_history import InputHistory

    h = InputHistory()
    h.load_from_turns([{"user_input": "from_A_1"}, {"user_input": "from_A_2"}])
    h.load_from_turns([{"user_input": "from_B_1"}])
    assert list(h) == ["from_B_1"]


def test_input_history_load_from_turns_dedup_consecutive():
    from sunday.tui.widgets.input_history import InputHistory

    h = InputHistory()
    h.load_from_turns([
        {"user_input": "x"},
        {"user_input": "x"},  # 相邻重复，跳过
        {"user_input": "y"},
        {"user_input": "x"},  # 非相邻，保留
    ])
    assert list(h) == ["x", "y", "x"]


def test_input_history_load_from_turns_skips_empty():
    """空字符串、None、缺失字段都跳过。"""
    from sunday.tui.widgets.input_history import InputHistory

    h = InputHistory()
    h.load_from_turns([
        {"user_input": ""},
        {"user_input": None},
        {},  # 无 user_input 字段
        {"user_input": "real"},
    ])
    assert list(h) == ["real"]


def test_input_history_load_from_turns_respects_maxlen():
    """加载超过 maxlen 的 turns 时只保留最近 maxlen 条。"""
    from sunday.tui.widgets.input_history import InputHistory

    h = InputHistory(maxlen=2)
    h.load_from_turns([
        {"user_input": "a"},
        {"user_input": "b"},
        {"user_input": "c"},
    ])
    assert list(h) == ["b", "c"]


def test_input_history_clear():
    from sunday.tui.widgets.input_history import InputHistory

    h = InputHistory()
    h.append("x")
    h.append("y")
    h.clear()
    assert len(h) == 0


def test_input_history_no_persistence_between_instances():
    """新建两个 InputHistory 实例彼此独立（验证不持久化）。"""
    from sunday.tui.widgets.input_history import InputHistory

    a = InputHistory()
    a.append("private")
    b = InputHistory()
    assert len(b) == 0


# ──────────────────────────────────────────────────────────────────────────────
# PromptTextArea（依赖 Textual Pilot 模拟键盘）
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def make_app():
    """构造一个最小 Textual App 把 PromptTextArea 挂进去用。"""
    from textual.app import App, ComposeResult

    from sunday.tui.widgets.input_history import InputHistory
    from sunday.tui.widgets.prompt_textarea import PromptTextArea

    class _Host(App):
        def __init__(self, history=None, fold_threshold=4):
            super().__init__()
            self.history = history or InputHistory()
            self.fold_threshold = fold_threshold
            self.submitted: list[str] = []

        def compose(self) -> ComposeResult:
            yield PromptTextArea(
                history=self.history,
                paste_fold_threshold=self.fold_threshold,
                id="pta",
            )

        def on_prompt_text_area_submitted(self, event) -> None:
            self.submitted.append(event.value)

    return _Host


async def test_enter_submits(make_app):
    """裸 Enter 应触发提交，输入框清空。"""
    app = make_app()
    async with app.run_test(headless=True) as pilot:
        ta = pilot.app.query_one("#pta")
        ta.text = "你好"
        ta.focus()
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert pilot.app.submitted == ["你好"]
        assert ta.text == ""


async def test_ctrl_enter_inserts_newline(make_app):
    """Ctrl+Enter 不提交，插入换行。"""
    app = make_app()
    async with app.run_test(headless=True) as pilot:
        ta = pilot.app.query_one("#pta")
        ta.text = "hello"
        ta.focus()
        # 把光标移到末尾
        ta.move_cursor(ta.document.end)
        await pilot.pause()
        await pilot.press("ctrl+enter")
        await pilot.pause()
        # 不提交
        assert pilot.app.submitted == []
        # 文本变两行
        assert "\n" in ta.text


async def test_backslash_continuation_creates_newline(make_app):
    r"""行尾 `\` + Enter：删 \ 并插入换行，不提交（Claude Code 反斜杠续行）。"""
    app = make_app()
    async with app.run_test(headless=True) as pilot:
        ta = pilot.app.query_one("#pta")
        ta.text = "line1\\"
        ta.focus()
        ta.move_cursor(ta.document.end)
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        # 不提交
        assert pilot.app.submitted == []
        # \\ 已被删除并换行
        assert ta.text == "line1\n"


async def test_paste_fold_threshold_above(make_app):
    """>4 行粘贴折叠为 [Pasted N lines #xxxxxx] 占位符。"""
    from textual import events

    app = make_app(fold_threshold=4)
    async with app.run_test(headless=True) as pilot:
        ta = pilot.app.query_one("#pta")
        ta.focus()
        await pilot.pause()
        big = "L1\nL2\nL3\nL4\nL5\nL6"  # 6 lines, >4
        ta.post_message(events.Paste(big))
        await pilot.pause()
        # 输入框中显示占位符而非完整文本
        assert "[Pasted 6 lines" in ta.text
        # 占位符 ID 是 6 位 hex
        import re
        m = re.search(r"\[Pasted 6 lines #([0-9a-f]{6})\]", ta.text)
        assert m is not None


async def test_paste_below_threshold_inserts_inline(make_app):
    """<=4 行粘贴原样插入。"""
    from textual import events

    app = make_app(fold_threshold=4)
    async with app.run_test(headless=True) as pilot:
        ta = pilot.app.query_one("#pta")
        ta.focus()
        await pilot.pause()
        small = "a\nb\nc"  # 3 lines, ≤ 4
        ta.post_message(events.Paste(small))
        await pilot.pause()
        # 不应有占位符
        assert "Pasted" not in ta.text
        assert "a\nb\nc" in ta.text


async def test_paste_expanded_on_submit(make_app):
    """Submit 时占位符还原为原始多行内容。"""
    from textual import events

    app = make_app(fold_threshold=4)
    async with app.run_test(headless=True) as pilot:
        ta = pilot.app.query_one("#pta")
        ta.focus()
        await pilot.pause()
        big = "L1\nL2\nL3\nL4\nL5"  # 5 lines, >4
        ta.post_message(events.Paste(big))
        await pilot.pause()
        # 提交
        await pilot.press("enter")
        await pilot.pause()
        # 提交内容是完整原文，非占位符
        assert pilot.app.submitted == [big]


async def test_history_prev_at_first_line(make_app):
    """光标在首行时按 ↑ 触发历史回溯；非首行不触发。"""
    from sunday.tui.widgets.input_history import InputHistory

    history = InputHistory()
    history.append("alpha")
    history.append("beta")
    app = make_app(history=history)
    async with app.run_test(headless=True) as pilot:
        ta = pilot.app.query_one("#pta")
        ta.focus()
        await pilot.pause()
        # 光标在 (0, 0) → 首行 → ↑ 触发历史
        await pilot.press("up")
        await pilot.pause()
        assert ta.text == "beta"
        # 再 ↑ → alpha
        await pilot.press("up")
        await pilot.pause()
        assert ta.text == "alpha"


async def test_history_next_returns_to_draft(make_app):
    """↓ 一路向后回到原始草稿（空）。"""
    from sunday.tui.widgets.input_history import InputHistory

    history = InputHistory()
    history.append("alpha")
    app = make_app(history=history)
    async with app.run_test(headless=True) as pilot:
        ta = pilot.app.query_one("#pta")
        ta.focus()
        await pilot.pause()
        await pilot.press("up")    # → "alpha"
        await pilot.pause()
        assert ta.text == "alpha"
        await pilot.press("down")  # → 回到草稿（空）
        await pilot.pause()
        assert ta.text == ""
