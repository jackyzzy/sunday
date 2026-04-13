"""providers 包内部共用工具函数。

放在此处而非 llm_client.py，避免 providers ↔ llm_client 循环依赖。
"""
from __future__ import annotations


def split_thinking(raw: str) -> tuple[str | None, str]:
    """剥离 thinking 标签，返回 (thinking, rest)。

    支持：
      - Anthropic extended thinking: <thinking>...</thinking>
      - DeepSeek / 通用 chain-of-thought: <think>...</think>
    """
    for open_tag, close_tag in [("<thinking>", "</thinking>"), ("<think>", "</think>")]:
        if open_tag in raw and close_tag in raw:
            start = raw.index(open_tag) + len(open_tag)
            end = raw.index(close_tag)
            thinking = raw[start:end].strip()
            rest = raw[raw.index(close_tag) + len(close_tag):].strip()
            return thinking, rest
    return None, raw
