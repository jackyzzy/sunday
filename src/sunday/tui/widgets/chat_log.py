"""ChatLog — 聊天消息渲染组件"""
from __future__ import annotations

from textual import events
from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import RichLog


class SelectableRichLog(RichLog):
    """支持鼠标拖拽选行的 RichLog 子类（行级别粒度）。"""

    _sel_start: int | None = None
    _sel_end: int | None = None
    _selecting: bool = False

    def on_mouse_down(self, event: events.MouseDown) -> None:
        line_idx = max(0, int(round(event.y + self.scroll_y)))
        self._sel_start = line_idx
        self._sel_end = None
        self._selecting = True

    def on_mouse_move(self, event: events.MouseMove) -> None:
        if self._selecting:
            self._sel_end = max(0, int(round(event.y + self.scroll_y)))

    def on_mouse_up(self, event: events.MouseUp) -> None:
        if self._selecting:
            self._sel_end = max(0, int(round(event.y + self.scroll_y)))
            self._selecting = False

    def on_leave(self, event: events.Leave) -> None:
        self._selecting = False

    def get_selected_text(self) -> str | None:
        """返回选中行的纯文本；单击（start==end）或无选区返回 None。"""
        if self._sel_start is None or self._sel_end is None:
            return None
        from rich.text import Text
        start = min(self._sel_start, self._sel_end)
        end = max(self._sel_start, self._sel_end)
        if start == end:
            return None
        lines_text = []
        for i, line in enumerate(self.lines):
            if start <= i <= end:
                lines_text.append(line.plain if isinstance(line, Text) else str(line))
        return "\n".join(lines_text) if lines_text else None

    def selected_line_count(self) -> int:
        """返回选中的视觉行数，无选区时返回 0。"""
        if self._sel_start is None or self._sel_end is None:
            return 0
        return abs(self._sel_end - self._sel_start) + 1

    def clear_selection(self) -> None:
        self._sel_start = None
        self._sel_end = None
        self._selecting = False


class ChatLog(Widget):
    """聊天日志：渲染消息、工具卡片、流式追加。"""

    DEFAULT_CSS = """
    ChatLog {
        height: 1fr;
        border: none;
    }
    """

    def compose(self) -> ComposeResult:
        yield SelectableRichLog(id="chat-rich-log", wrap=True, markup=True, highlight=False)

    @property
    def _log(self) -> SelectableRichLog:
        return self.query_one(SelectableRichLog)

    def get_selected_text(self) -> str | None:
        """返回鼠标选中的纯文本，无选区时返回 None。"""
        return self._log.get_selected_text()

    def clear_selection(self) -> None:
        """清除鼠标选区状态。"""
        self._log.clear_selection()

    @property
    def renderable_text(self) -> str:
        """返回所有已渲染文本的拼接（用于测试断言）。"""
        return "\n".join(str(line) for line in self._log.lines)

    def get_plain_text(self) -> str:
        """返回去掉 Rich markup 的纯文本（用于复制到剪贴板）。"""
        from rich.text import Text
        lines = []
        for line in self._log.lines:
            if isinstance(line, Text):
                lines.append(line.plain)
            else:
                lines.append(str(line))
        return "\n".join(lines)

    def add_user_message(self, content: str) -> None:
        self._log.write(f"[bold cyan][用户][/bold cyan] {content}")

    def add_assistant_message(self, content: str) -> None:
        self._log.write(f"[bold green][Sunday][/bold green] {content}")

    def add_system_message(self, content: str) -> None:
        self._log.write(f"[dim]{content}[/dim]")

    def add_error_message(self, content: str) -> None:
        self._log.write(f"[bold red][错误][/bold red] {content}")

    def add_plan(self, goal: str, steps: list[dict]) -> None:
        self._log.write(f"[bold yellow][规划][/bold yellow] {goal}")
        for s in steps:
            self._log.write(f"  [dim][ ][/dim] {s.get('id', '?')}: {s.get('intent', '')}")

    def mark_step(self, step_id: str, status: str, verified: bool | None = None) -> None:
        """步骤完成时追加状态行（RichLog append-only，不修改已有行）。"""
        icons = {
            "done": "[green][✓][/green]",
            "failed": "[red][✗][/red]",
            "skipped": "[dim][↷][/dim]",
        }
        icon = icons.get(status)
        if not icon:
            return
        suffix = " [yellow](验证未通过)[/yellow]" if status == "done" and verified is False else ""
        self._log.write(f"  {icon} {step_id}{suffix}")

    def append_stream(self, delta: str) -> None:
        """追加流式 token（合并到最后一行）。"""
        self._log.write(delta, end="")

    def add_confirm_request(self, tool: str, message: str) -> None:
        self._log.write(f"[bold red]⚠ 确认请求[/bold red] 工具：{tool}")
        self._log.write(f"  {message}")
        self._log.write("  请回复 [bold]y[/bold] 确认或 [bold]n[/bold] 取消")
