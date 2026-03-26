"""StatusBar — 运行状态指示栏"""
from __future__ import annotations

from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Label


class StatusBar(Widget):
    """显示当前运行状态：就绪 / 思考中 / 执行中 / 已中止 / 错误。"""

    DEFAULT_CSS = """
    StatusBar {
        height: 1;
        background: $surface;
        color: $text;
        padding: 0 1;
    }
    """

    status_text: reactive[str] = reactive("● 就绪")

    def compose(self):
        yield Label(self.status_text, id="status-label")

    def watch_status_text(self, value: str) -> None:
        try:
            self.query_one("#status-label", Label).update(value)
        except Exception:
            pass

    def set_thinking(self) -> None:
        self.status_text = "● 思考中..."

    def set_executing(self, step_id: str = "") -> None:
        if step_id:
            self.status_text = f"● 执行中 {step_id}"
        else:
            self.status_text = "● 执行中"

    def set_idle(self) -> None:
        self.status_text = "● 就绪"

    def set_aborted(self) -> None:
        self.status_text = "● 已中止"

    def set_error(self, msg: str = "") -> None:
        self.status_text = f"● 错误 {msg}".strip()
