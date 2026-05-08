"""PromptTextArea — Sunday TUI 的多行输入组件。

行为契约（详见 plan「Sunday TUI 复制粘贴 / 多行输入 / 历史回溯 彻底改造方案」）：

- **Enter** 提交（高频路径）；行尾 `\\` + Enter 反斜杠续行（删 `\\` 后插入换行）
- **Ctrl+Enter / Shift+Enter / Alt+Enter** 插入换行（跨终端 fallback）
- **↑** 在首行触发历史回溯；非首行走 TextArea 默认光标上移
- **↓** 在末行触发历史前进；非末行走默认光标下移
- **粘贴** 超过 `paste_fold_threshold` 行折叠为 `[Pasted N lines #xxxxxx]` 占位符；
  Submit 时还原为原始多行文本
"""
from __future__ import annotations

import re
import secrets

from textual import events
from textual.binding import Binding
from textual.message import Message
from textual.widgets import TextArea

from sunday.tui.widgets.input_history import InputHistory

_PLACEHOLDER_RE = re.compile(r"\[Pasted \d+ lines #([0-9a-f]{6})\]")


class PromptTextArea(TextArea):
    """多行输入 + 提交 + 历史 + 粘贴折叠。"""

    # priority=True 确保覆盖 TextArea 默认 _on_key 中对 Enter 插入换行的处理
    BINDINGS = [
        Binding("enter", "submit_or_continue", show=False, priority=True),
        Binding("ctrl+enter", "newline", show=False, priority=True),
        Binding("shift+enter", "newline", show=False, priority=True),
        Binding("alt+enter", "newline", show=False, priority=True),
    ]

    class Submitted(Message):
        """用户按 Enter 提交了一条 query（占位符已展开）。"""

        def __init__(self, value: str) -> None:
            super().__init__()
            self.value = value

    def __init__(
        self,
        *,
        history: InputHistory,
        paste_fold_threshold: int = 4,
        **kwargs,
    ) -> None:
        kwargs.setdefault("language", None)
        kwargs.setdefault("show_line_numbers", False)
        super().__init__(**kwargs)
        self._history = history
        self._paste_fold_threshold = paste_fold_threshold
        self._history_cursor: int | None = None
        self._draft_before_history: str = ""
        self._pending_pastes: dict[str, str] = {}

    # ── Enter / 换行 / 续行 ────────────────────────────────────────────────

    def action_submit_or_continue(self) -> None:
        """裸 Enter：行尾 `\\` 续行，否则提交（占位符还原）。"""
        raw = self.text
        if raw.endswith("\\"):
            # 反斜杠续行：删末尾 \\ 并换行（不提交）
            self.text = raw[:-1]
            self.move_cursor(self.document.end)
            self.insert("\n")
            return

        final = self._expand_placeholders(raw)
        if not final.strip():
            return
        self._history.append(final)
        self.post_message(self.Submitted(final))
        self.clear()
        self._pending_pastes.clear()
        self._history_cursor = None
        self._draft_before_history = ""

    def action_newline(self) -> None:
        """Ctrl+Enter / Shift+Enter / Alt+Enter：插入换行。"""
        self.insert("\n")

    # ── 历史回溯（覆盖 TextArea 的 cursor_up/down action）──────────────────

    def action_cursor_up(self, select: bool = False) -> None:
        row, _ = self.cursor_location
        if row == 0 and not select:
            self._history_prev()
            return
        super().action_cursor_up(select)

    def action_cursor_down(self, select: bool = False) -> None:
        row, _ = self.cursor_location
        last_row = max(0, self.document.line_count - 1)
        if row == last_row and not select:
            self._history_next()
            return
        super().action_cursor_down(select)

    def _history_prev(self) -> None:
        if len(self._history) == 0:
            return
        if self._history_cursor is None:
            # 第一次按 ↑：保存当前草稿，跳到最后一条
            self._draft_before_history = self.text
            self._history_cursor = len(self._history) - 1
        elif self._history_cursor > 0:
            self._history_cursor -= 1
        else:
            return  # 已到最早一条
        self._load_history_entry(self._history[self._history_cursor])

    def _history_next(self) -> None:
        if self._history_cursor is None:
            return  # 不在历史模式中
        if self._history_cursor < len(self._history) - 1:
            self._history_cursor += 1
            self._load_history_entry(self._history[self._history_cursor])
        else:
            # 回到草稿（结束历史模式）
            self._history_cursor = None
            self._load_history_entry(self._draft_before_history)

    def _load_history_entry(self, text: str) -> None:
        self.load_text(text)
        # 光标移到末尾，便于继续编辑
        self.move_cursor(self.document.end)

    # ── 粘贴折叠 ──────────────────────────────────────────────────────────

    async def _on_paste(self, event: events.Paste) -> None:
        # 关键：Textual 沿 MRO 调用每个类的 _on_paste，必须 prevent_default()
        # 阻止 TextArea._on_paste 再次 insert 完整文本（否则双重插入）
        event.stop()
        event.prevent_default()
        text = event.text or ""
        if not text:
            return
        line_count = text.count("\n") + 1
        if line_count > self._paste_fold_threshold:
            placeholder_id = secrets.token_hex(3)  # 6 hex chars
            placeholder = f"[Pasted {line_count} lines #{placeholder_id}]"
            self._pending_pastes[placeholder_id] = text
            self.insert(placeholder)
            return
        # 阈值内：原样插入
        self.insert(text)

    def _expand_placeholders(self, text: str) -> str:
        """提交前把 [Pasted N lines #xxxxxx] 替换回完整原文。"""
        if not self._pending_pastes:
            return text

        def _sub(m: re.Match[str]) -> str:
            return self._pending_pastes.get(m.group(1), m.group(0))

        return _PLACEHOLDER_RE.sub(_sub, text)
