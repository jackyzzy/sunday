"""T4-1 验证：ToolResultGuard 单元测试"""
from __future__ import annotations

from sunday.tools.guard import ToolResultGuard


def test_validate_short_output_unchanged():
    """短输出原样返回"""
    guard = ToolResultGuard(max_output_chars=4096)
    assert guard.validate("hello world") == "hello world"


def test_validate_truncates_long_output():
    """超过 max_output_chars 时被截断，包含截断提示"""
    guard = ToolResultGuard(max_output_chars=10)
    result = guard.validate("a" * 100)
    # 原始内容被截断，结果中包含截断提示
    assert "截断" in result or "[truncated]" in result
    # 原始 100 个字符的内容不应完整出现
    assert len(result) < 100


def test_validate_filters_api_key():
    """含 sk-ant- 前缀的 API key 被替换为 [REDACTED]"""
    guard = ToolResultGuard(max_output_chars=4096)
    text = "key is sk-ant-api03-abcdef1234567890"
    result = guard.validate(text)
    assert "sk-ant-" not in result
    assert "[REDACTED]" in result


def test_validate_filters_openai_key():
    """含 sk- 前缀的 OpenAI key 被过滤"""
    guard = ToolResultGuard(max_output_chars=4096)
    text = "Authorization: Bearer sk-proj-abcdef1234567890abcdef"
    result = guard.validate(text)
    assert "sk-proj-" not in result
    assert "[REDACTED]" in result


def test_validate_converts_non_string():
    """非字符串输入被转为字符串"""
    guard = ToolResultGuard(max_output_chars=4096)
    assert guard.validate({"key": "value"}) == "{'key': 'value'}"
    assert guard.validate(42) == "42"
    assert guard.validate(["a", "b"]) == "['a', 'b']"
