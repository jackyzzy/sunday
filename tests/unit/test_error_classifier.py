"""ErrorClassifier 单元测试"""
from __future__ import annotations

import pytest

from sunday.tools.error_classifier import classify, should_classify
from sunday.tools.health_store import ErrorType


@pytest.mark.parametrize("error_text, expected_type", [
    ("[工具错误] HTTP 401: Unauthorized", ErrorType.AUTH_ERROR),
    ("[工具错误] invalid api key provided", ErrorType.AUTH_ERROR),
    ("[工具错误] authentication failed", ErrorType.AUTH_ERROR),
    ("[工具错误] HTTP 429: Too Many Requests", ErrorType.RATE_LIMIT),
    ("[工具错误] rate limit exceeded", ErrorType.RATE_LIMIT),
    ("[工具错误] quota_exceeded for this month", ErrorType.RATE_LIMIT),
    ("[工具错误] requests per day limit reached", ErrorType.RATE_LIMIT),
    ("[工具错误] Connection refused", ErrorType.NETWORK_ERROR),
    ("[工具错误] DNS resolution failed", ErrorType.NETWORK_ERROR),
    ("[工具错误] network error occurred", ErrorType.NETWORK_ERROR),
    ("[工具超时] 工具 web_search 超过 30s 未返回", ErrorType.TIMEOUT),
    ("[工具错误] ReadTimeout: timed out waiting", ErrorType.TIMEOUT),
    ("[工具错误] HTTP 404: not found", ErrorType.NOT_FOUND),
    ("[工具错误] endpoint not found", ErrorType.NOT_FOUND),
    ("[工具错误] 未知奇怪错误", ErrorType.UNKNOWN),
])
def test_classify(error_text, expected_type):
    error_type, _ = classify(error_text)
    assert error_type == expected_type


def test_rejected_prefix_not_classified():
    error_type, suggestion = classify("[工具拒绝] 工具 web_search 在禁止列表中")
    assert error_type == ErrorType.UNKNOWN
    assert suggestion == ""


def test_non_error_prefix_not_classified():
    # 正常结果不触发分类
    error_type, suggestion = classify("搜索结果：1. 标题...")
    assert error_type == ErrorType.UNKNOWN


def test_should_classify_error_prefix():
    assert should_classify("[工具错误] something went wrong")
    assert should_classify("[工具超时] took too long")


def test_should_classify_rejected_false():
    assert not should_classify("[工具拒绝] not allowed")


def test_should_classify_normal_result_false():
    assert not should_classify("正常输出内容")


def test_classify_returns_suggestion():
    _, suggestion = classify("[工具错误] HTTP 429: Too Many Requests")
    assert suggestion  # 非空建议
    assert "替代" in suggestion or "重试" in suggestion


def test_classify_auth_error_suggestion():
    _, suggestion = classify("[工具错误] invalid api key")
    assert "API Key" in suggestion or "api" in suggestion.lower()
