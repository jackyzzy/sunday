"""InputBar — 用户输入框"""
from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.message import Message as TMsg
from textual.widget import Widget
from textual.widgets import Input


class InputBar(Widget):
    """单行输入框，支持 / 前缀 Slash 命令和普通消息。"""

    DEFAULT_CSS = """
    InputBar {
        height: 3;
        border-top: solid $accent;
    }
    """

    COMPONENT_CLASSES = {"sunday-input-bar"}

    class Submitted(TMsg):
        """用户提交了消息。"""
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    def compose(self) -> ComposeResult:
        yield Input(placeholder="> 输入消息，/ 开头使用 Slash 命令", id="main-input")

    @on(Input.Submitted)
    def _on_submit(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if text:
            self.post_message(self.Submitted(text))
            event.input.clear()
