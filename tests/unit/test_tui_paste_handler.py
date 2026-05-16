"""验证 BracketedPaste 绑定真的能命中、且 \\r 规范化生效。

测试不启动完整 TUI，只构造 KeyBindings + fake event 直接调用 handler。
"""
from __future__ import annotations

from unittest.mock import MagicMock

from prompt_toolkit.buffer import Buffer
from prompt_toolkit.key_binding import KeyPress
from prompt_toolkit.keys import Keys

from sunday.tui.cli import PasteFolder, _build_keybindings


def _find_binding(kb, key):
    """从 KeyBindings 中找到匹配 key 的 binding。"""
    for b in kb.bindings:
        if len(b.keys) == 1 and b.keys[0] == key:
            return b
    return None


def test_bracketed_paste_binding_registered():
    """_build_keybindings 必须注册 Keys.BracketedPaste handler。"""
    pf = PasteFolder(threshold=4)
    kb = _build_keybindings(
        send_abort=MagicMock(),
        paste_folder=pf,
        is_busy=lambda: False,
    )
    binding = _find_binding(kb, Keys.BracketedPaste)
    assert binding is not None, "Keys.BracketedPaste 绑定未注册"
    # eager=True 是兜底防御，确保覆盖默认 emacs binding
    assert binding.eager() is True, "BracketedPaste binding 必须 eager=True"


def test_paste_handler_folds_multiline_lf():
    """LF 风格 (\\n) 多行粘贴：>4 行折叠为占位符。"""
    pf = PasteFolder(threshold=4)
    kb = _build_keybindings(MagicMock(), pf, lambda: False)
    binding = _find_binding(kb, Keys.BracketedPaste)

    buf = Buffer()
    event = MagicMock()
    event.data = "L1\nL2\nL3\nL4\nL5\nL6"  # 6 行
    event.current_buffer = buf

    binding.handler(event)

    # buffer 拿到的是占位符，不是原文
    assert buf.text != event.data
    assert buf.text.startswith("[Pasted 6 lines #")


def test_paste_handler_normalizes_crlf():
    """CRLF 风格 (\\r\\n) 粘贴必须被规范化，否则 count('\\n') 算少。"""
    pf = PasteFolder(threshold=4)
    kb = _build_keybindings(MagicMock(), pf, lambda: False)
    binding = _find_binding(kb, Keys.BracketedPaste)

    buf = Buffer()
    event = MagicMock()
    event.data = "L1\r\nL2\r\nL3\r\nL4\r\nL5\r\nL6"  # Windows 风格 6 行
    event.current_buffer = buf

    binding.handler(event)

    # 规范化后应识别为 6 行，触发折叠
    assert buf.text.startswith("[Pasted 6 lines #"), (
        f"CRLF 未规范化，行数计算错误：buf.text={buf.text!r}"
    )


def test_paste_handler_normalizes_lone_cr():
    """老 Mac 风格 (\\r) 粘贴也要规范化。"""
    pf = PasteFolder(threshold=4)
    kb = _build_keybindings(MagicMock(), pf, lambda: False)
    binding = _find_binding(kb, Keys.BracketedPaste)

    buf = Buffer()
    event = MagicMock()
    event.data = "L1\rL2\rL3\rL4\rL5\rL6"  # 老 Mac 风格 6 行
    event.current_buffer = buf

    binding.handler(event)

    assert buf.text.startswith("[Pasted 6 lines #"), (
        f"\\r 未规范化：buf.text={buf.text!r}"
    )


def test_paste_handler_short_paste_passthrough():
    """≤threshold 行的粘贴直接透传，不折叠。"""
    pf = PasteFolder(threshold=4)
    kb = _build_keybindings(MagicMock(), pf, lambda: False)
    binding = _find_binding(kb, Keys.BracketedPaste)

    buf = Buffer()
    event = MagicMock()
    event.data = "L1\nL2\nL3"  # 3 行（≤4）
    event.current_buffer = buf

    binding.handler(event)

    # 短粘贴原样插入
    assert buf.text == "L1\nL2\nL3"


def test_paste_folder_can_expand_after_handler_folds():
    """handler 折叠后，paste_folder.expand 能还原原文。"""
    pf = PasteFolder(threshold=4)
    kb = _build_keybindings(MagicMock(), pf, lambda: False)
    binding = _find_binding(kb, Keys.BracketedPaste)

    original = "A\nB\nC\nD\nE\nF"
    buf = Buffer()
    event = MagicMock()
    event.data = original
    event.current_buffer = buf

    binding.handler(event)

    placeholder = buf.text
    expanded = pf.expand(placeholder)
    assert expanded == original
