"""LLM Provider 注册表。

内置：
  anthropic    → AnthropicProvider
  openai       → OpenAICompatProvider

扩展（在应用启动入口调用）：
  from sunday.agent.providers import register_provider
  register_provider("myprovider", MyProvider())
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sunday.agent.providers.anthropic import AnthropicProvider
from sunday.agent.providers.base import LLMAPIError, LLMProvider, LLMRequest, LLMResponse, ToolCall
from sunday.agent.providers.openai_compat import OpenAICompatProvider


@dataclass(frozen=True)
class ProviderEntry:
    """Provider 注册条目：协议实现实例 + 默认元数据。"""
    instance: LLMProvider
    default_api_key_env: str = field(default="")


_REGISTRY: dict[str, ProviderEntry] = {
    "anthropic": ProviderEntry(AnthropicProvider(), default_api_key_env="ANTHROPIC_API_KEY"),
    "openai":    ProviderEntry(OpenAICompatProvider(), default_api_key_env="OPENAI_API_KEY"),
}


def get_provider_entry(name: str) -> ProviderEntry:
    """按 provider 名称返回 ProviderEntry，未注册时抛出 ValueError。"""
    if name not in _REGISTRY:
        raise ValueError(
            f"不支持的 provider: {name!r}。"
            f"已注册: {sorted(_REGISTRY)}。"
            f"可通过 register_provider() 注册自定义 provider。"
        )
    return _REGISTRY[name]


def get_provider(name: str) -> LLMProvider:
    """按 provider 名称返回实现实例（向后兼容接口）。"""
    return get_provider_entry(name).instance


def register_provider(name: str, provider: LLMProvider, *,
                      default_api_key_env: str = "") -> None:
    """注册自定义 provider，可覆盖内置实现。"""
    _REGISTRY[name] = ProviderEntry(provider, default_api_key_env=default_api_key_env)


__all__ = [
    "LLMAPIError", "LLMProvider", "LLMRequest", "LLMResponse", "ToolCall",
    "ProviderEntry", "get_provider_entry", "get_provider", "register_provider",
]
