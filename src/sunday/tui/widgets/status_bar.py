"""StatusBar — 运行状态指示栏 + 固定快捷键提示。"""
from __future__ import annotations

from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Label


class StatusBar(Widget):
    """两行状态栏：上半运行状态（思考/执行/就绪…），下半固定快捷键提示。"""

    DEFAULT_CSS = """
    StatusBar {
        height: 2;
        background: $surface;
        color: $text;
        padding: 0 1;
        layout: vertical;
    }
    StatusBar > #status-label {
        height: 1;
    }
    StatusBar > #status-hint {
        height: 1;
        color: $text-muted;
    }
    """

    status_text: reactive[str] = reactive("● 就绪")
    hint_text: reactive[str] = reactive("")

    def compose(self):
        yield Label(self.status_text, id="status-label")
        yield Label(self.hint_text, id="status-hint")

    def watch_status_text(self, value: str) -> None:
        try:
            self.query_one("#status-label", Label).update(value)
        except Exception:
            pass

    def watch_hint_text(self, value: str) -> None:
        try:
            self.query_one("#status-hint", Label).update(value)
        except Exception:
            pass

    def set_hint(self, hint: str) -> None:
        """设置底部固定快捷键提示行。"""
        self.hint_text = hint

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
