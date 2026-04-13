"""LLM Provider 注册表。

内置：
  anthropic    → AnthropicProvider
  openai       → OpenAICompatProvider

扩展（在应用启动入口调用）：
  from sunday.agent.providers import register_provider
  register_provider("myprovider", MyProvider())
"""
from __future__ import annotations

from sunday.agent.providers.anthropic import AnthropicProvider
from sunday.agent.providers.base import LLMProvider, LLMRequest, LLMResponse, ToolCall
from sunday.agent.providers.openai_compat import OpenAICompatProvider

_REGISTRY: dict[str, LLMProvider] = {
    "anthropic": AnthropicProvider(),
    "openai": OpenAICompatProvider(),
}


def get_provider(name: str) -> LLMProvider:
    """按 provider 名称返回实现实例，未注册时抛出 ValueError。"""
    if name not in _REGISTRY:
        raise ValueError(
            f"不支持的 provider: {name!r}。"
            f"已注册: {list(_REGISTRY)}。"
            f"可通过 register_provider() 注册自定义 provider。"
        )
    return _REGISTRY[name]


def register_provider(name: str, provider: LLMProvider) -> None:
    """注册自定义 provider，可覆盖内置实现。"""
    _REGISTRY[name] = provider


__all__ = ["LLMProvider", "LLMRequest", "LLMResponse", "ToolCall", "get_provider", "register_provider"]
