"""ToolCard — 工具调用折叠卡片"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Collapsible, Label


class ToolCard(Widget):
    """可折叠的工具调用卡片，显示输入/输出。"""

    DEFAULT_CSS = """
    ToolCard {
        height: auto;
        border: solid $accent;
        margin: 0 1;
    }
    """

    def __init__(self, tool_name: str, tool_input: dict, **kwargs) -> None:
        super().__init__(**kwargs)
        self._tool_name = tool_name
        self._tool_input = tool_input
        self._output: str | None = None

    def compose(self) -> ComposeResult:
        title = f"🔧 {self._tool_name}"
        with Collapsible(title=title, collapsed=False):
            yield Label(f"IN:  {self._tool_input}", id="tool-input")
            yield Label("OUT: ...", id="tool-output")

    def set_output(self, output: str) -> None:
        """更新工具输出并折叠卡片。"""
        self._output = output
        try:
            self.query_one("#tool-output", Label).update(f"OUT: {output[:200]}")
            self.query_one(Collapsible).collapsed = True
        except Exception:
            pass
