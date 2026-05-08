"""InputBar — 用户输入容器，承载 PromptTextArea。

替换原先的单行 `Input` 为多行 `PromptTextArea`：
- Enter 提交、Ctrl+Enter 换行、↑↓ 历史回溯
- 多行粘贴折叠为占位符，提交时展开还原
"""
from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.message import Message as TMsg
from textual.widget import Widget

from sunday.tui.widgets.input_history import InputHistory
from sunday.tui.widgets.prompt_textarea import PromptTextArea


class InputBar(Widget):
    """多行输入框，支持 / 前缀 Slash 命令和普通消息。"""

    DEFAULT_CSS = """
    InputBar {
        height: 6;
        border-top: solid $accent;
    }
    InputBar PromptTextArea {
        height: 1fr;
    }
    """

    class Submitted(TMsg):
        """用户提交了消息（占位符已展开为完整文本）。"""

        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    def __init__(
        self,
        history: InputHistory | None = None,
        paste_fold_threshold: int = 4,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._history = history if history is not None else InputHistory()
        self._paste_fold_threshold = paste_fold_threshold

    @property
    def history(self) -> InputHistory:
        return self._history

    def compose(self) -> ComposeResult:
        yield PromptTextArea(
            history=self._history,
            paste_fold_threshold=self._paste_fold_threshold,
            id="main-input",
        )

    @on(PromptTextArea.Submitted)
    def _on_submit(self, event: PromptTextArea.Submitted) -> None:
        text = event.value.strip()
        if text:
            self.post_message(self.Submitted(text))
