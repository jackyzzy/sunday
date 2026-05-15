"""InputHistory — TUI 输入历史回溯（per-session，从 turns payload 派生）。

核心思想：history 不引入新存储，而是从 Service 通过 `/history` slash 命令返回的
turns 数组中提取 user_input 字段重建。新提交的 query 同步进入内存栈，agent loop
完成后会把该 turn 通过 `sessions.write_turn` 落盘，下次切换 session 再加载时
即包含此条 —— 形成自然闭环。

不变量：
- 纯内存（deque），无文件 IO、无序列化
- 不直接访问 ~/.sunday/sessions/，遵循 CLAUDE.md "agent 层零模板 IO"
- 切换 session 时 load_from_turns() 替换全部内容（不污染）
- 相邻重复去重（连续 ↑ 不会跳过相同条目）
"""
from __future__ import annotations

from collections import deque
from typing import Iterator


class InputHistory:
    """per-session 输入历史，从 Service 返回的 turns payload 派生。"""

    def __init__(self, maxlen: int = 200) -> None:
        self._buf: deque[str] = deque(maxlen=maxlen)

    def __len__(self) -> int:
        return len(self._buf)

    def __getitem__(self, idx: int) -> str:
        return self._buf[idx]

    def __iter__(self) -> Iterator[str]:
        return iter(self._buf)

    def append(self, text: str) -> None:
        """新提交时同步加入内存栈，相邻重复跳过。"""
        if not text:
            return
        if self._buf and self._buf[-1] == text:
            return
        self._buf.append(text)

    def clear(self) -> None:
        self._buf.clear()

    def load_from_turns(self, turns: list[dict]) -> None:
        """切到目标 session 时调用：清空内存栈 → 从 turns 重建。"""
        self._buf.clear()
        for t in turns:
            text = t.get("user_input") if isinstance(t, dict) else None
            if not text:
                continue
            if self._buf and self._buf[-1] == text:
                continue
            self._buf.append(text)
