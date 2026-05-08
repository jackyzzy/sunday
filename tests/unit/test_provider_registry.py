"""Provider registry + LLMAPIError 错误提取单测。"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from sunday.agent.providers import (
    get_provider,
    get_provider_entry,
    register_provider,
)
from sunday.agent.providers.anthropic import AnthropicProvider
from sunday.agent.providers.base import LLMAPIError
from sunday.agent.providers.openai_compat import OpenAICompatProvider

# ── Registry 结构 ────────────────────────────────────────────────────────────

def test_anthropic_entry_has_correct_instance():
    entry = get_provider_entry("anthropic")
    assert isinstance(entry.instance, AnthropicProvider)


def test_openai_entry_has_correct_instance():
    entry = get_provider_entry("openai")
    assert isinstance(entry.instance, OpenAICompatProvider)


def test_anthropic_entry_default_api_key_env():
    entry = get_provider_entry("anthropic")
    assert entry.default_api_key_env == "ANTHROPIC_API_KEY"


def test_openai_entry_default_api_key_env():
    entry = get_provider_entry("openai")
    assert entry.default_api_key_env == "OPENAI_API_KEY"


def test_get_provider_returns_instance():
    """get_provider() 向后兼容接口：返回 LLMProvider 实例。"""
    assert isinstance(get_provider("anthropic"), AnthropicProvider)
    assert isinstance(get_provider("openai"), OpenAICompatProvider)


def test_unknown_provider_raises_value_error():
    with pytest.raises(ValueError, match="不支持的 provider"):
        get_provider_entry("bogus_provider_xyz")


def test_unknown_provider_error_lists_known_providers():
    with pytest.raises(ValueError, match="anthropic"):
        get_provider_entry("nonexistent")


def test_register_custom_provider():
    """register_provider() 可扩展 registry，且不影响内置条目。"""
    from sunday.agent.providers.openai_compat import OpenAICompatProvider
    custom = OpenAICompatProvider()
    register_provider("test_custom_xyz", custom, default_api_key_env="TEST_KEY")
    entry = get_provider_entry("test_custom_xyz")
    assert entry.instance is custom
    assert entry.default_api_key_env == "TEST_KEY"
    # 内置条目不受影响
    assert isinstance(get_provider("anthropic"), AnthropicProvider)
    # 清理
    from sunday.agent.providers import _REGISTRY
    _REGISTRY.pop("test_custom_xyz", None)


def test_provider_entry_is_frozen():
    """ProviderEntry frozen=True：不可在注册后修改字段。"""
    entry = get_provider_entry("anthropic")
    with pytest.raises((AttributeError, TypeError)):
        entry.default_api_key_env = "CHANGED"  # type: ignore[misc]


# ── LLMAPIError 提取逻辑 ─────────────────────────────────────────────────────

def _make_mock_resp(status_code: int, body: dict | str) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.is_success = False
    resp.text = json.dumps(body) if isinstance(body, dict) else body
    return resp


def test_raise_api_error_extracts_openai_format():
    """OpenAI / DeepSeek 格式：{"error": {"message": "..."}}"""
    from sunday.agent.llm_client import _raise_api_error
    resp = _make_mock_resp(400, {"error": {"message": "reasoning_content must be passed back"}})
    with pytest.raises(LLMAPIError, match="reasoning_content must be passed back"):
        _raise_api_error("openai", resp)


def test_raise_api_error_includes_provider_and_status():
    from sunday.agent.llm_client import _raise_api_error
    resp = _make_mock_resp(401, {"error": {"message": "Invalid API key"}})
    with pytest.raises(LLMAPIError, match=r"\[openai\] HTTP 401"):
        _raise_api_error("openai", resp)


def test_raise_api_error_falls_back_on_non_json():
    """非 JSON 响应体时截断原文返回。"""
    from sunday.agent.llm_client import _raise_api_error
    resp = _make_mock_resp(503, "Service Unavailable")
    with pytest.raises(LLMAPIError, match="Service Unavailable"):
        _raise_api_error("openai", resp)


def test_raise_api_error_handles_missing_message_field():
    """error 字段存在但无 message 子字段，回退到 str(body)。"""
    from sunday.agent.llm_client import _raise_api_error
    resp = _make_mock_resp(422, {"error": {"code": "invalid_request"}})
    with pytest.raises(LLMAPIError, match="invalid_request"):
        _raise_api_error("openai", resp)


# ── config.py _lookup_api_key 简化验证 ───────────────────────────────────────

def test_lookup_api_key_uses_env_var_override(monkeypatch):
    """api_key_env 覆盖时，使用指定的环境变量名。"""
    monkeypatch.setenv("MY_CUSTOM_KEY", "sk-test")
    from sunday.config import _lookup_api_key
    assert _lookup_api_key("openai", "MY_CUSTOM_KEY") == "sk-test"


def test_lookup_api_key_falls_back_to_provider_upper(monkeypatch):
    """无 api_key_env 时，fallback 为 {PROVIDER}_API_KEY。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek")
    from sunday.config import _lookup_api_key
    assert _lookup_api_key("deepseek", None) == "sk-deepseek"


def test_lookup_api_key_raises_when_missing(monkeypatch):
    monkeypatch.delenv("MISSING_PROVIDER_API_KEY", raising=False)
    from sunday.config import _lookup_api_key
    with pytest.raises(ValueError, match="MISSING_PROVIDER_API_KEY"):
        _lookup_api_key("missing_provider", None)
