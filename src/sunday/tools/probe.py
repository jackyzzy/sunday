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


# ── 内置 probe 映射表 ─────────────────────────────────────────────────────────

# 工具名 → probe 函数。优先级低于 ToolMeta.probe 字段（ToolMeta 有则用 ToolMeta 的）。
BUILTIN_PROBES: dict[str, ProbeFunc] = {
    "web_search": probe_tavily,
}
