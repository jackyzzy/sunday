"""网络搜索技能工具 — Tavily 优先，DDG HTML 代码兜底。

Bug #4 设计：LLM 只看到单一 web_search 工具，内部按以下顺序自动选 backend：

    [1] 若 TAVILY_API_KEY 配置 → 尝试 Tavily API
            成功 → 返回，末尾标 source: tavily
            失败（auth/quota/网络/HTTP 错误）→ 进入 [2]
    [2] DuckDuckGo HTML 抓取（无 auth、无 quota）
            成功 → 返回，末尾标 source: duckduckgo（Tavily 不可用时代码兜底）
            失败 → 返回 [错误] 聚合两路错误原因

Tavily 失败的所有情况都尝试 DDG fallback —— 错误细节由 LLM 通过 observation 看到。
"""
from __future__ import annotations

import html as _html
import logging
import os
import re
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

logger = logging.getLogger(__name__)

_TAVILY_URL = "https://api.tavily.com/search"
_DDG_HTML_URL = "https://html.duckduckgo.com/html/"
_DDG_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
)


class _BackendError(Exception):
    """内部使用：标记一路 backend 失败，附带原因供上层聚合。"""


# ── Tavily backend ────────────────────────────────────────────────────────────

async def _search_tavily(query: str, max_results: int) -> str:
    """调用 Tavily API。失败抛 _BackendError，成功返回格式化 markdown。"""
    api_key = os.environ.get("TAVILY_API_KEY", "")
    if not api_key:
        raise _BackendError("TAVILY_API_KEY 未配置")

    import httpx

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                _TAVILY_URL,
                json={
                    "api_key": api_key,
                    "query": query,
                    "max_results": max_results,
                    "search_depth": "basic",
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as e:
        raise _BackendError(f"HTTP {e.response.status_code}") from e
    except httpx.HTTPError as e:
        raise _BackendError(f"网络异常：{e}") from e
    except Exception as e:
        raise _BackendError(f"未知异常：{e}") from e

    results = data.get("results", []) or []
    if not results:
        raise _BackendError("Tavily 未返回任何结果")

    return _format_results(_normalize_tavily(results, max_results))


def _normalize_tavily(results: list[dict], max_results: int) -> list[dict[str, Any]]:
    """Tavily 响应 → 内部统一 schema {title, url, snippet, date}。"""
    items = []
    for r in results[:max_results]:
        raw_date = r.get("published_date") or ""
        date_obj = None
        try:
            date_obj = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            pass
        items.append({
            "title": r.get("title", "（无标题）"),
            "url": r.get("url", ""),
            "snippet": r.get("content", "")[:200].replace("\n", " "),
            "date": date_obj,
        })
    # 按日期降序（无日期排末尾）
    items.sort(key=lambda x: (x["date"] is None, -(x["date"].timestamp() if x["date"] else 0)))
    return items


# ── DuckDuckGo backend ────────────────────────────────────────────────────────

async def _search_duckduckgo(query: str, max_results: int) -> str:
    """抓 DuckDuckGo HTML 端点，解析结果。失败抛 _BackendError。"""
    import httpx

    headers = {"User-Agent": _DDG_USER_AGENT}
    try:
        async with httpx.AsyncClient(
            timeout=30, follow_redirects=True, headers=headers
        ) as client:
            resp = await client.get(_DDG_HTML_URL, params={"q": query})
            resp.raise_for_status()
            html_text = resp.text
    except httpx.HTTPStatusError as e:
        raise _BackendError(f"HTTP {e.response.status_code}") from e
    except httpx.HTTPError as e:
        raise _BackendError(f"网络异常：{e}") from e
    except Exception as e:
        raise _BackendError(f"未知异常：{e}") from e

    items = _parse_ddg_html(html_text)
    if not items:
        raise _BackendError("DuckDuckGo 未返回任何结果")
    items = items[:max_results]
    return _format_results(items)


def _parse_ddg_html(html_text: str) -> list[dict[str, Any]]:
    """解析 DDG `/html/` 端点 HTML，提取每条结果的 title/url/snippet。

    DDG 的链接是跳转 link `//duckduckgo.com/l/?uddg=<urlencoded-real-url>`，
    需要 unwrap 出真实 URL。

    解析策略：先全局找所有 result__a 锚点（每条结果一个），然后从该位置往后
    在窗口内找最近的 result__snippet。这种切片方式比按 div 块匹配更稳健，
    不依赖完整的 HTML 树结构（DDG 实际页面里 div 嵌套层级会变）。
    """
    title_pattern = re.compile(
        r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
        re.DOTALL,
    )
    snippet_pattern = re.compile(
        r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>',
        re.DOTALL,
    )

    title_matches = list(title_pattern.finditer(html_text))
    items: list[dict[str, Any]] = []

    for i, m in enumerate(title_matches):
        raw_href = m.group(1)
        title_html = m.group(2)

        # 在当前 title 与下个 title 之间找 snippet（最近原则，避免跨条目）
        window_end = title_matches[i + 1].start() if i + 1 < len(title_matches) else len(html_text)
        snippet_match = snippet_pattern.search(html_text, m.end(), window_end)
        snippet_html = snippet_match.group(1) if snippet_match else ""

        url = _unwrap_ddg_url(raw_href)
        title = _clean_html_text(title_html)
        snippet = _clean_html_text(snippet_html)[:200]

        if not title:
            continue
        items.append({
            "title": title,
            "url": url,
            "snippet": snippet,
            "date": None,  # DDG HTML 端点不提供发布日期
        })
    return items


def _unwrap_ddg_url(raw: str) -> str:
    """DDG 跳转 link `//duckduckgo.com/l/?uddg=<encoded>` → 真实 URL。

    若解析失败，返回原始字符串（不抛异常，保证可降级显示）。
    """
    candidate = raw
    if candidate.startswith("//"):
        candidate = "https:" + candidate
    try:
        parsed = urlparse(candidate)
        if "duckduckgo.com" in parsed.netloc:
            qs = parse_qs(parsed.query)
            uddg = qs.get("uddg", [None])[0]
            if uddg:
                return unquote(uddg)
    except Exception:
        pass
    return raw


def _clean_html_text(text: str) -> str:
    """去掉 HTML 标签并 unescape entity，压缩空白。"""
    no_tags = re.sub(r"<[^>]+>", " ", text)
    unescaped = _html.unescape(no_tags)
    return re.sub(r"\s+", " ", unescaped).strip()


# ── Unified output formatting ─────────────────────────────────────────────────

def _format_results(items: list[dict[str, Any]]) -> str:
    """Tavily/DDG 共用的格式化逻辑：i. **title**[ 发布：date]\\n   url\\n   snippet"""
    lines = []
    for i, item in enumerate(items, 1):
        title = item.get("title") or "（无标题）"
        url = item.get("url") or ""
        snippet = item.get("snippet") or ""
        date = item.get("date")
        date_label = f"  发布：{date.strftime('%Y-%m-%d')}" if date else ""
        lines.append(f"{i}. **{title}**{date_label}\n   {url}\n   {snippet}")
    return "\n\n".join(lines)


# ── Public API ────────────────────────────────────────────────────────────────

async def web_search(query: str, max_results: int = 5) -> str:
    """搜索网络，按发布日期降序返回标题+摘要列表。

    Backend 链路：
    - 若 TAVILY_API_KEY 配置且 Tavily 可用 → 用 Tavily
    - 否则（缺 KEY、auth 失败、quota 用尽、网络异常等）→ 自动 fallback 到 DuckDuckGo HTML
    - 两路均失败 → 返回 `[错误] ...`

    返回字符串末尾会标 `_source: tavily/duckduckgo_`，供调试与日志归因。
    """
    tavily_err: str | None = None

    if os.environ.get("TAVILY_API_KEY"):
        try:
            result = await _search_tavily(query, max_results)
            return f"{result}\n\n_source: tavily_"
        except _BackendError as e:
            tavily_err = str(e)
            logger.info("Tavily 失败（%s），fallback 到 DuckDuckGo", tavily_err)
    else:
        tavily_err = "TAVILY_API_KEY 未配置"

    # Fallback 路径
    try:
        result = await _search_duckduckgo(query, max_results)
        return f"{result}\n\n_source: duckduckgo（Tavily 不可用时的代码兜底）_"
    except _BackendError as ddg_err:
        return (
            f"[错误] 所有搜索后端均失败。"
            f"Tavily: {tavily_err}; "
            f"DuckDuckGo: {ddg_err}"
        )


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
            html_body = resp.text

        html_body = re.sub(
            r"<(script|style)[^>]*>.*?</\1>", "", html_body, flags=re.DOTALL | re.IGNORECASE
        )
        text = re.sub(r"<[^>]+>", " ", html_body)
        text = re.sub(r"\s+", " ", text).strip()

        if len(text) > max_chars:
            text = text[:max_chars] + f"\n\n[内容已截断，共 {len(text)} 字符]"

        return text if text else "（页面内容为空）"

    except Exception as e:
        return f"[错误] 抓取页面失败：{e}"


from sunday.tools.probe import probe_web_search  # noqa: E402
from sunday.tools.registry import ToolMeta  # noqa: E402

TOOLS = [
    (ToolMeta(
        name="web_search",
        description=(
            "搜索网络，按发布日期降序返回标题+摘要列表。"
            "优先使用 Tavily API（需 TAVILY_API_KEY），自动 fallback 到 DuckDuckGo（无需认证）。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"},
                "max_results": {"type": "integer", "description": "最多返回条数，默认5"},
            },
            "required": ["query"],
        },
        timeout=30,
        probe=probe_web_search,
    ), web_search),
    (ToolMeta(
        name="fetch_url",
        description="抓取指定 URL 的页面内容，提取纯文本正文。",
        input_schema={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "目标 URL"},
            },
            "required": ["url"],
        },
        timeout=30,
    ), fetch_url),
]
