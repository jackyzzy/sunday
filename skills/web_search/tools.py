"""网络搜索技能工具（Phase 4 占位实现）

Phase 6 中接入真实搜索 API（Tavily / Brave Search）。
"""
from __future__ import annotations


async def web_search(query: str, max_results: int = 5) -> str:
    """搜索网络（Phase 4 占位）。"""
    return f"[占位] web_search 将在 Phase 6 接入真实搜索 API。查询：{query}"


async def fetch_url(url: str) -> str:
    """抓取 URL 内容（Phase 4 占位）。"""
    return f"[占位] fetch_url 将在 Phase 6 实现。URL：{url}"
