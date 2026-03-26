"""网络搜索技能工具 — Phase 6 实现版"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


async def web_search(query: str, max_results: int = 5) -> str:
    """使用 Tavily API 搜索关键词，返回标题 + 摘要列表。

    需要环境变量 TAVILY_API_KEY。未配置时返回友好错误提示。
    """
    try:
        import os

        api_key = os.environ.get("TAVILY_API_KEY", "")
        if not api_key:
            return (
                "[错误] 未配置 TAVILY_API_KEY，无法执行网络搜索。"
                "请在 .env 文件中添加 TAVILY_API_KEY=<your-key>"
            )

        import httpx

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": api_key,
                    "query": query,
                    "max_results": max_results,
                    "search_depth": "basic",
                },
            )
            resp.raise_for_status()
            data = resp.json()

        results = data.get("results", [])
        if not results:
            return f"未找到关于 '{query}' 的搜索结果"

        lines = []
        for i, r in enumerate(results[:max_results], 1):
            title = r.get("title", "（无标题）")
            url = r.get("url", "")
            snippet = r.get("content", "")[:200].replace("\n", " ")
            lines.append(f"{i}. **{title}**\n   {url}\n   {snippet}")

        return "\n\n".join(lines)

    except Exception as e:
        return f"[错误] 网络搜索失败：{e}"


async def fetch_url(url: str, max_chars: int = 4096) -> str:
    """抓取 URL 的页面内容，提取纯文本（去除 HTML 标签）。"""
    try:
        import httpx

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (compatible; SundayAgent/1.0; +https://github.com/sunday)"
            )
        }
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            html = resp.text

        # 去除 script/style 块
        html = re.sub(
            r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE
        )
        # 去除所有 HTML 标签
        text = re.sub(r"<[^>]+>", " ", html)
        # 合并空白
        text = re.sub(r"\s+", " ", text).strip()

        if len(text) > max_chars:
            text = text[:max_chars] + f"\n\n[内容已截断，共 {len(text)} 字符]"

        return text if text else "（页面内容为空）"

    except Exception as e:
        return f"[错误] 抓取页面失败：{e}"
