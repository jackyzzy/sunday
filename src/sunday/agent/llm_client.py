"""共享 LLM 调用客户端 — 通过 Provider 注册表分发，消除 provider if/else。"""
from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

from sunday.agent.providers.base import LLMResponse

if TYPE_CHECKING:
    from sunday.config import ModelConfig

# 模块级连接池：复用 AsyncClient，避免每次调用重建 TCP/TLS 连接
# 延迟初始化，便于测试通过 patch("sunday.agent.llm_client._get_http_client", ...) 替换
_HTTP_CLIENT: httpx.AsyncClient | None = None


def _get_http_client() -> httpx.AsyncClient:
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None:
        _HTTP_CLIENT = httpx.AsyncClient(
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            timeout=None,
        )
    return _HTTP_CLIENT


class LLMClient:
    """LLM 调用分发器，通过 Provider 注册表支持任意 provider。

    api_key 由 model_cfg.get_api_key() 内部获取，调用方无需手动传递。
    """

    @staticmethod
    async def call(
        model_cfg: "ModelConfig",
        messages: list[dict],
        *,
        system: str = "",
        tools: list[dict] | None = None,
        max_tokens: int | None = None,
        temperature: float = 0,
        thinking_budget: int = 0,
        timeout: float = 120,
    ) -> LLMResponse:
        """统一 LLM 调用接口，返回规范化 LLMResponse。

        LLMResponse 字段：
          text:         模型输出文本（已剥离 thinking 标签）
          thinking:     思考过程，可能为 None
          tool_call:    工具调用（ToolCall），可能为 None
          finish_reason: 结束原因
          raw_content:  provider 原始 content（供 build_tool_result_messages 使用）
        """
        import logging

        from sunday.agent.providers import get_provider

        _logger = logging.getLogger(__name__)
        provider = get_provider(model_cfg.provider)
        api_key = model_cfg.get_api_key()

        # thinking_budget 仅对声明支持的 provider 生效，其余静默置零
        effective_budget = thinking_budget if provider.supports_thinking() else 0

        request = provider.build_request(
            model_cfg, api_key, messages,
            system=system,
            tools=tools,
            max_tokens=max_tokens or model_cfg.max_tokens,
            temperature=temperature,
            thinking_budget=effective_budget,
        )

        resp = await _get_http_client().post(
            request.url, headers=request.headers, json=request.body, timeout=timeout
        )
        if not resp.is_success:
            _logger.error("LLM API %d 错误，响应体：%s", resp.status_code, resp.text[:500])
        resp.raise_for_status()
        return provider.parse_response(resp.json())

    @staticmethod
    async def call_text(
        model_cfg: "ModelConfig",
        prompt: str,
        *,
        max_tokens: int = 1024,
        temperature: float = 0,
        timeout: float = 60,
    ) -> str:
        """简化接口：单轮文本请求，直接返回字符串。"""
        result = await LLMClient.call(
            model_cfg,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout,
        )
        return result.text
