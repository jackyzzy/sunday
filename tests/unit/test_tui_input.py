"""TUI 输入逻辑测试：InputHistory + PasteFolder。

InputHistory：相邻去重、maxlen 截断、load_from_turns 切 session。
PasteFolder：>threshold 行折叠为 [Pasted N lines #xxxxxx] 占位符，提交时还原。
"""
from __future__ import annotations

import re


# ──────────────────────────────────────────────────────────────────────────────
# InputHistory（纯逻辑）
# ──────────────────────────────────────────────────────────────────────────────


def test_input_history_append_basic():
    from sunday.tui.input_history import InputHistory

    h = InputHistory()
    h.append("alpha")
    h.append("beta")
    assert len(h) == 2
    assert h[0] == "alpha"
    assert h[1] == "beta"


def test_input_history_append_dedup_consecutive():
    """相邻重复不入栈（连续敲两次同样的 query 只存一份）。"""
    from sunday.tui.input_history import InputHistory

    h = InputHistory()
    h.append("hello")
    h.append("hello")  # 相邻重复
    h.append("hello")  # 再次
    h.append("world")
    h.append("hello")  # 非相邻：允许再次入栈
    assert list(h) == ["hello", "world", "hello"]


def test_input_history_maxlen_truncates_old():
    """超过 maxlen 后旧条目被 deque 自动丢弃。"""
    from sunday.tui.input_history import InputHistory

    h = InputHistory(maxlen=3)
    for q in ["a", "b", "c", "d", "e"]:
        h.append(q)
    assert len(h) == 3
    assert list(h) == ["c", "d", "e"]


def test_input_history_load_from_turns_extracts_user_input():
    from sunday.tui.input_history import InputHistory

    h = InputHistory()
    h.load_from_turns([
        {"turn_id": "t001", "turn_index": 1, "user_input": "alpha"},
        {"turn_id": "t002", "turn_index": 2, "user_input": "beta"},
    ])
    assert list(h) == ["alpha", "beta"]


def test_input_history_load_from_turns_clears_old():
    """从 session A 切到 B，A 的历史不污染 B。"""
    from sunday.tui.input_history import InputHistory

    h = InputHistory()
    h.load_from_turns([{"user_input": "from_A_1"}, {"user_input": "from_A_2"}])
    h.load_from_turns([{"user_input": "from_B_1"}])
    assert list(h) == ["from_B_1"]


def test_input_history_load_from_turns_dedup_consecutive():
    from sunday.tui.input_history import InputHistory

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
    from sunday.tui.input_history import InputHistory

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
    from sunday.tui.input_history import InputHistory

    h = InputHistory(maxlen=2)
    h.load_from_turns([
        {"user_input": "a"},
        {"user_input": "b"},
        {"user_input": "c"},
    ])
    assert list(h) == ["b", "c"]


def test_input_history_clear():
    from sunday.tui.input_history import InputHistory

    h = InputHistory()
    h.append("x")
    h.append("y")
    h.clear()
    assert len(h) == 0


def test_input_history_no_persistence_between_instances():
    """新建两个 InputHistory 实例彼此独立（验证不持久化）。"""
    from sunday.tui.input_history import InputHistory

    a = InputHistory()
    a.append("private")
    b = InputHistory()
    assert len(b) == 0


# ──────────────────────────────────────────────────────────────────────────────
# PasteFolder（纯逻辑，替代旧 PromptTextArea 粘贴折叠测试）
# ──────────────────────────────────────────────────────────────────────────────


def test_paste_folder_below_threshold_passes_through():
    """<=4 行原样返回，不折叠。"""
    from sunday.tui.cli import PasteFolder

    pf = PasteFolder(threshold=4)
    text = "a\nb\nc"
    assert pf.maybe_fold(text) == text


def test_paste_folder_above_threshold_creates_placeholder():
    """>4 行折叠为 [Pasted N lines #xxxxxx]，N 是原始行数。"""
    from sunday.tui.cli import PasteFolder

    pf = PasteFolder(threshold=4)
    text = "L1\nL2\nL3\nL4\nL5\nL6"  # 6 lines
    placeholder = pf.maybe_fold(text)
    m = re.fullmatch(r"\[Pasted 6 lines #([0-9a-f]{6})\]", placeholder)
    assert m is not None


def test_paste_folder_expand_restores_original():
    """expand 把占位符还原回原文。"""
    from sunday.tui.cli import PasteFolder

    pf = PasteFolder(threshold=4)
    original = "L1\nL2\nL3\nL4\nL5"
    placeholder = pf.maybe_fold(original)
    # 假设用户在占位符前后还输入了内容
    submitted = f"前缀 {placeholder} 后缀"
    expanded = pf.expand(submitted)
    assert expanded == f"前缀 {original} 后缀"


def test_paste_folder_expand_without_pending_is_identity():
    """没有 pending 占位符时 expand 不改字符串。"""
    from sunday.tui.cli import PasteFolder

    pf = PasteFolder(threshold=4)
    assert pf.expand("hello world") == "hello world"


def test_paste_folder_reset_clears_pending():
    """reset 后再 expand 找不到旧占位符（保留原占位符文本）。"""
    from sunday.tui.cli import PasteFolder

    pf = PasteFolder(threshold=4)
    placeholder = pf.maybe_fold("a\nb\nc\nd\ne")
    pf.reset()
    # 现在 expand 这个占位符应该保留原样（因为 pending 已清）
    assert pf.expand(placeholder) == placeholder


def test_paste_folder_unknown_placeholder_id_kept_intact():
    """expand 遇到不在 pending 中的占位符 ID 保留原样。"""
    from sunday.tui.cli import PasteFolder

    pf = PasteFolder(threshold=4)
    # 凭空伪造一个占位符（未经过 maybe_fold）
    fake = "[Pasted 10 lines #abcdef]"
    assert pf.expand(fake) == fake
