"""ChatLog — 聊天消息渲染组件"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import RichLog


class ChatLog(Widget):
    """聊天日志：渲染消息、工具卡片、流式追加。"""

    DEFAULT_CSS = """
    ChatLog {
        height: 1fr;
        border: none;
    }
    """

    def compose(self) -> ComposeResult:
        yield RichLog(id="chat-rich-log", wrap=True, markup=True, highlight=False)

    @property
    def _log(self) -> RichLog:
        return self.query_one(RichLog)

    @property
    def renderable_text(self) -> str:
        """返回所有已渲染文本的拼接（用于测试断言）。"""
        return "\n".join(str(line) for line in self._log.lines)

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
            self._log.write(f"  · {s.get('id', '?')}: {s.get('intent', '')}")

    def append_stream(self, delta: str) -> None:
        """追加流式 token（合并到最后一行）。"""
        self._log.write(delta, end="")

    def add_confirm_request(self, tool: str, message: str) -> None:
        self._log.write(f"[bold red]⚠ 确认请求[/bold red] 工具：{tool}")
        self._log.write(f"  {message}")
        self._log.write("  请回复 [bold]y[/bold] 确认或 [bold]n[/bold] 取消")
