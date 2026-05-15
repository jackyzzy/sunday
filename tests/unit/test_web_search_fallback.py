"""Bug #4: web_search 内部 Tavily → DuckDuckGo HTML fallback 链路。

设计：LLM 只看到一个 web_search 工具。内部先试 Tavily（如 KEY 配置），
失败（auth/quota/network/缺 KEY）→ 自动 fallback 到 DuckDuckGo HTML 抓取，
两路返回的字符串格式对 LLM 视角一致（标题/URL/摘要），区别只在末尾 source: 标记。

所有测试 mock httpx，无真实 API 调用。
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# DuckDuckGo HTML 端点返回的典型片段（用于测试解析）
_DDG_HTML_SAMPLE = """\
<html><body>
<div class="result results_links results_links_deep web-result">
  <h2 class="result__title">
    <a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.python.org%2Fdownloads%2F&rut=abc">Python 下载页面</a>
  </h2>
  <a class="result__snippet" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.python.org%2Fdownloads%2F&rut=abc">Python 是一门简单易学但功能强大的编程语言，下载最新版本。</a>
  <a class="result__url" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.python.org%2Fdownloads%2F&rut=abc">www.python.org/downloads</a>
</div>
<div class="result results_links results_links_deep web-result">
  <h2 class="result__title">
    <a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fdocs.python.org&rut=def">Python 官方文档</a>
  </h2>
  <a class="result__snippet" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fdocs.python.org&rut=def">完整的 Python 标准库参考与教程。</a>
  <a class="result__url" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fdocs.python.org&rut=def">docs.python.org</a>
</div>
</body></html>
"""


def _mock_tavily_success() -> MagicMock:
    """返回一个 mock 的 Tavily 200 响应（含一条结果）。"""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "results": [
            {
                "title": "Python 官网",
                "url": "https://python.org",
                "content": "Python 编程语言官方网站",
                "published_date": "2025-01-15T00:00:00Z",
            },
        ],
    }
    resp.raise_for_status.return_value = None
    return resp


def _mock_tavily_http_error(status_code: int, body: str = "error") -> MagicMock:
    """返回 mock 的 Tavily HTTP 错误响应（401/429/500 等）— raise_for_status 抛 HTTPStatusError。"""
    import httpx

    resp = MagicMock()
    resp.status_code = status_code
    resp.text = body
    resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        f"{status_code}", request=MagicMock(), response=resp
    )
    return resp


def _mock_ddg_response(html: str = _DDG_HTML_SAMPLE) -> MagicMock:
    """返回 mock 的 DDG HTML 响应。"""
    resp = MagicMock()
    resp.status_code = 200
    resp.text = html
    resp.raise_for_status.return_value = None
    return resp


class _RoutingClient:
    """根据 URL 把 httpx 调用路由到不同的 mock 响应。

    用法：
        client = _RoutingClient(tavily_response=..., ddg_response=...)
        with patch("httpx.AsyncClient") as cls:
            cls.return_value = client
            ...
    """

    def __init__(
        self,
        tavily_response: MagicMock | Exception | None = None,
        ddg_response: MagicMock | Exception | None = None,
    ):
        self.tavily_response = tavily_response
        self.ddg_response = ddg_response
        self.tavily_called = False
        self.ddg_called = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url: str, **kwargs):
        if "tavily" in url:
            self.tavily_called = True
            if isinstance(self.tavily_response, Exception):
                raise self.tavily_response
            return self.tavily_response
        raise AssertionError(f"unexpected POST to {url}")

    async def get(self, url: str, **kwargs):
        if "duckduckgo" in url:
            self.ddg_called = True
            if isinstance(self.ddg_response, Exception):
                raise self.ddg_response
            return self.ddg_response
        raise AssertionError(f"unexpected GET to {url}")


# ── 用例 ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tavily_success_no_fallback():
    """Tavily 成功 → 直接用 Tavily 结果，不调 DDG。"""
    client = _RoutingClient(
        tavily_response=_mock_tavily_success(),
        ddg_response=_mock_ddg_response(),
    )
    with patch.dict(os.environ, {"TAVILY_API_KEY": "fake-key"}):
        with patch("httpx.AsyncClient", return_value=client):
            from skills.web_search.tools import web_search
            result = await web_search("Python", max_results=3)

    assert client.tavily_called is True
    assert client.ddg_called is False, "Tavily 成功时不应触发 DDG"
    assert "Python 官网" in result
    assert "tavily" in result.lower(), "结果尾应标 source=tavily"


@pytest.mark.asyncio
async def test_tavily_auth_fail_falls_back_to_ddg():
    """Tavily 401 → 自动 fallback 到 DDG，返回 DDG 结果。"""
    client = _RoutingClient(
        tavily_response=_mock_tavily_http_error(401, "unauthorized"),
        ddg_response=_mock_ddg_response(),
    )
    with patch.dict(os.environ, {"TAVILY_API_KEY": "bad-key"}):
        with patch("httpx.AsyncClient", return_value=client):
            from skills.web_search.tools import web_search
            result = await web_search("Python")

    assert client.tavily_called is True
    assert client.ddg_called is True, "Tavily 401 应触发 DDG fallback"
    assert "Python 下载页面" in result or "Python 官方文档" in result
    assert "duckduckgo" in result.lower(), "结果尾应标 source=duckduckgo"


@pytest.mark.asyncio
async def test_tavily_quota_fail_falls_back():
    """Tavily 429（quota 用尽）→ fallback。"""
    client = _RoutingClient(
        tavily_response=_mock_tavily_http_error(429, "rate limit exceeded"),
        ddg_response=_mock_ddg_response(),
    )
    with patch.dict(os.environ, {"TAVILY_API_KEY": "fake-key"}):
        with patch("httpx.AsyncClient", return_value=client):
            from skills.web_search.tools import web_search
            result = await web_search("Python")

    assert client.ddg_called is True
    assert "Python" in result


@pytest.mark.asyncio
async def test_tavily_network_fail_falls_back():
    """Tavily 网络异常（httpx.ConnectError）→ fallback。"""
    import httpx

    client = _RoutingClient(
        tavily_response=httpx.ConnectError("connection refused"),
        ddg_response=_mock_ddg_response(),
    )
    with patch.dict(os.environ, {"TAVILY_API_KEY": "fake-key"}):
        with patch("httpx.AsyncClient", return_value=client):
            from skills.web_search.tools import web_search
            result = await web_search("Python")

    assert client.ddg_called is True


@pytest.mark.asyncio
async def test_tavily_missing_key_falls_back():
    """TAVILY_API_KEY 缺失 → 跳过 Tavily 直接走 DDG（不调 Tavily）。"""
    client = _RoutingClient(
        tavily_response=None,  # 不应被调用
        ddg_response=_mock_ddg_response(),
    )
    with patch.dict(os.environ, {}, clear=True):
        os.environ.pop("TAVILY_API_KEY", None)
        with patch("httpx.AsyncClient", return_value=client):
            from skills.web_search.tools import web_search
            result = await web_search("Python")

    assert client.tavily_called is False, "KEY 缺失时不应调 Tavily"
    assert client.ddg_called is True, "KEY 缺失时应直接走 DDG"
    assert "Python 下载页面" in result or "Python 官方文档" in result


@pytest.mark.asyncio
async def test_both_fail_returns_error_string():
    """Tavily fail + DDG fail → 返回聚合 [错误] 字符串。"""
    import httpx

    client = _RoutingClient(
        tavily_response=_mock_tavily_http_error(401, "unauthorized"),
        ddg_response=httpx.ConnectError("dns failure"),
    )
    with patch.dict(os.environ, {"TAVILY_API_KEY": "fake-key"}):
        with patch("httpx.AsyncClient", return_value=client):
            from skills.web_search.tools import web_search
            result = await web_search("Python")

    assert result.startswith("[错误]"), "两路全失败时应以 [错误] 开头"
    assert "tavily" in result.lower()
    assert "duckduckgo" in result.lower()


@pytest.mark.asyncio
async def test_ddg_html_parsing_extracts_title_url_snippet():
    """直接测 DDG HTML 解析：能从典型 HTML 中提取标题/URL/摘要。"""
    from skills.web_search.tools import _parse_ddg_html

    items = _parse_ddg_html(_DDG_HTML_SAMPLE)
    assert len(items) == 2
    titles = [i["title"] for i in items]
    urls = [i["url"] for i in items]
    snippets = [i["snippet"] for i in items]

    assert "Python 下载页面" in titles
    assert "Python 官方文档" in titles
    # URL 必须从 uddg= 参数 decode 出真实 URL，不是 //duckduckgo.com/l/...
    assert any("python.org/downloads" in u for u in urls), f"URLs: {urls}"
    assert any("docs.python.org" in u for u in urls), f"URLs: {urls}"
    assert all("duckduckgo.com" not in u for u in urls), "应该 unwrap 掉 DDG 跳转 link"
    assert any("简单易学" in s for s in snippets)


@pytest.mark.asyncio
async def test_result_format_consistent_between_backends():
    """Tavily 与 DDG 返回的格式对 LLM 视角一致（i. **title**、url、snippet 三件齐全）。"""
    import re

    # 用 Tavily 成功路径
    client_tavily = _RoutingClient(tavily_response=_mock_tavily_success())
    with patch.dict(os.environ, {"TAVILY_API_KEY": "fake-key"}):
        with patch("httpx.AsyncClient", return_value=client_tavily):
            from skills.web_search.tools import web_search
            tavily_result = await web_search("Python")

    # 用 DDG fallback 路径（清掉 KEY）
    client_ddg = _RoutingClient(ddg_response=_mock_ddg_response())
    with patch.dict(os.environ, {}, clear=True):
        os.environ.pop("TAVILY_API_KEY", None)
        with patch("httpx.AsyncClient", return_value=client_ddg):
            from skills.web_search.tools import web_search
            ddg_result = await web_search("Python")

    # 两路都应包含 markdown 标题格式 (1. **xxx**) 和 URL
    for label, result in [("tavily", tavily_result), ("ddg", ddg_result)]:
        assert re.search(r"\d+\.\s+\*\*.+\*\*", result), (
            f"{label} 路径输出应含 markdown 编号 + 加粗标题：{result[:200]}"
        )
        assert "http" in result, f"{label} 路径输出应含 URL：{result[:200]}"
