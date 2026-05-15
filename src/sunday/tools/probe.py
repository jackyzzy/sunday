"""工具探测接口与内置实现。"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from sunday.tools.health_store import ErrorType


@dataclass
class ProbeResult:
    success: bool
    error_type: Optional[ErrorType] = None
    detail: str = ""
    suggestion: str = ""


# probe 函数签名：无参数，返回 ProbeResult
ProbeFunc = Callable[[], Awaitable[ProbeResult]]


# ── Tavily probe ──────────────────────────────────────────────────────────────

async def probe_tavily() -> ProbeResult:
    """向 Tavily API 发起最小代价探测（max_results=1, search_depth=basic）。

    探测目标：确认 API Key 有效 + 服务网络可达。
    使用固定 query="test" 降低 credit 消耗。
    """
    from sunday.tools.error_classifier import classify

    api_key = os.environ.get("TAVILY_API_KEY", "")
    if not api_key:
        return ProbeResult(
            success=False,
            error_type=ErrorType.AUTH_ERROR,
            detail="TAVILY_API_KEY 未配置",
            suggestion="请在 .env 中添加 TAVILY_API_KEY=<your-key>",
        )

    try:
        import httpx

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": api_key,
                    "query": "test",
                    "max_results": 1,
                    "search_depth": "basic",
                },
            )

        if resp.status_code == 200:
            return ProbeResult(success=True, detail="Tavily API 可用")

        error_text = f"HTTP {resp.status_code}: {resp.text[:300]}"
        error_type, suggestion = classify(f"[工具错误] {error_text}")
        if error_type == ErrorType.UNKNOWN:
            error_type = ErrorType.AUTH_ERROR
            suggestion = "请检查 API Key 是否有效"
        return ProbeResult(
            success=False,
            error_type=error_type,
            detail=error_text,
            suggestion=suggestion,
        )

    except Exception as e:
        from sunday.tools.error_classifier import classify

        error_text = str(e)
        error_type, suggestion = classify(f"[工具错误] {error_text}")
        if error_type == ErrorType.UNKNOWN:
            error_type = ErrorType.NETWORK_ERROR
            suggestion = "请检查网络连接或代理配置"
        return ProbeResult(
            success=False,
            error_type=error_type,
            detail=error_text,
            suggestion=suggestion,
        )


# ── DuckDuckGo probe ──────────────────────────────────────────────────────────

async def probe_duckduckgo() -> ProbeResult:
    """探测 DuckDuckGo HTML 端点可用性（无 auth，零 quota）。

    用最便宜的 query="test" 抓首页，只关心 HTTP 200。
    """
    from sunday.tools.error_classifier import classify

    try:
        import httpx

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) "
                "Gecko/20100101 Firefox/128.0"
            )
        }
        async with httpx.AsyncClient(
            timeout=10, follow_redirects=True, headers=headers
        ) as client:
            resp = await client.get(
                "https://html.duckduckgo.com/html/", params={"q": "test"}
            )

        if resp.status_code == 200:
            return ProbeResult(success=True, detail="DuckDuckGo HTML 端点可用")

        error_text = f"HTTP {resp.status_code}"
        error_type, suggestion = classify(f"[工具错误] {error_text}")
        return ProbeResult(
            success=False,
            error_type=error_type if error_type != ErrorType.UNKNOWN else ErrorType.NETWORK_ERROR,
            detail=error_text,
            suggestion=suggestion or "DuckDuckGo 可能被限流或暂时不可用",
        )

    except Exception as e:
        error_type, suggestion = classify(f"[工具错误] {e}")
        return ProbeResult(
            success=False,
            error_type=error_type if error_type != ErrorType.UNKNOWN else ErrorType.NETWORK_ERROR,
            detail=str(e),
            suggestion=suggestion or "请检查网络连接或代理配置",
        )


# ── 复合 probe：Tavily 优先，DDG 兜底 ────────────────────────────────────────

async def probe_web_search() -> ProbeResult:
    """探测 web_search 工具可用性。

    与 web_search 内部 fallback 链对齐：Tavily 任一可用即视为 web_search 可用。
    只有 Tavily + DDG 双路都失败才报错。返回的 detail 会注明实际可用的 backend，
    供调试与健康面板展示。
    """
    tavily_result = await probe_tavily()
    if tavily_result.success:
        return ProbeResult(success=True, detail=f"backend=tavily ({tavily_result.detail})")

    ddg_result = await probe_duckduckgo()
    if ddg_result.success:
        return ProbeResult(
            success=True,
            detail=f"backend=duckduckgo (Tavily 不可用：{tavily_result.detail})",
        )

    # 双路都不行 — 暴露更具体的失败原因（优先 DDG 错误，因 DDG 是最终兜底）
    return ProbeResult(
        success=False,
        error_type=ddg_result.error_type,
        detail=f"Tavily: {tavily_result.detail}; DuckDuckGo: {ddg_result.detail}",
        suggestion=ddg_result.suggestion or tavily_result.suggestion,
    )


# ── 内置 probe 映射表 ─────────────────────────────────────────────────────────

# 工具名 → probe 函数。优先级低于 ToolMeta.probe 字段（ToolMeta 有则用 ToolMeta 的）。
BUILTIN_PROBES: dict[str, ProbeFunc] = {
    "web_search": probe_web_search,
}
