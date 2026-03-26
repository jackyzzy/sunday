"""Phase 4：ToolResultGuard — 工具输出验证与清洗"""
from __future__ import annotations

import re

# 匹配常见 API key 格式
_API_KEY_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9\-_]{10,}"),  # Anthropic
    re.compile(r"sk-[A-Za-z0-9\-_]{20,}"),       # OpenAI / 通用
    re.compile(r"AIza[A-Za-z0-9\-_]{35}"),        # Google
]


class ToolResultGuard:
    """工具输出验证器：截断、过滤敏感信息、类型转换。"""

    def __init__(self, max_output_chars: int = 4096) -> None:
        self.max_output_chars = max_output_chars

    def validate(self, result: object) -> str:
        """验证并清洗工具输出，返回安全的字符串。"""
        # 1. 转字符串
        text = result if isinstance(result, str) else str(result)

        # 2. 过滤 API key
        for pattern in _API_KEY_PATTERNS:
            text = pattern.sub("[REDACTED]", text)

        # 3. 截断
        if len(text) > self.max_output_chars:
            text = text[: self.max_output_chars] + f"\n...[截断，原始输出 {len(text)} 字符]"

        return text
