"""HealthStore 单元测试"""
from __future__ import annotations

from sunday.tools.health_store import ErrorType, HealthStore, ToolStatus


def test_initial_state_healthy():
    store = HealthStore()
    h = store.get("some_tool")
    assert h.status == ToolStatus.HEALTHY
    assert h.error_type is None


def test_probe_failure_immediately_unavailable():
    store = HealthStore()
    store.record_probe_failure("web_search", ErrorType.AUTH_ERROR, "401 Unauthorized", "检查 key")
    h = store.get("web_search")
    assert h.status == ToolStatus.UNAVAILABLE
    assert h.error_type == ErrorType.AUTH_ERROR
    assert h.suggestion == "检查 key"


def test_probe_success_sets_healthy():
    store = HealthStore()
    store.record_probe_failure("web_search", ErrorType.NETWORK_ERROR, "timeout", "")
    store.record_probe_success("web_search")
    h = store.get("web_search")
    assert h.status == ToolStatus.HEALTHY
    assert h.error_type is None
    assert h.consecutive_failures == 0


def test_execution_errors_threshold_degraded_then_unavailable():
    store = HealthStore()
    # 第 1 次失败 → DEGRADED
    store.record_error("web_search", ErrorType.RATE_LIMIT, "429", "稍后重试")
    assert store.get("web_search").status == ToolStatus.DEGRADED
    assert store.get("web_search").consecutive_failures == 1

    # 第 2 次失败 → UNAVAILABLE
    store.record_error("web_search", ErrorType.RATE_LIMIT, "429", "稍后重试")
    assert store.get("web_search").status == ToolStatus.UNAVAILABLE
    assert store.get("web_search").consecutive_failures == 2


def test_success_resets_degraded():
    store = HealthStore()
    store.record_error("web_search", ErrorType.TIMEOUT, "timeout", "")
    assert store.get("web_search").status == ToolStatus.DEGRADED

    store.record_success("web_search")
    h = store.get("web_search")
    assert h.status == ToolStatus.HEALTHY
    assert h.consecutive_failures == 0


def test_success_does_not_reset_unavailable():
    store = HealthStore()
    store.record_probe_failure("web_search", ErrorType.AUTH_ERROR, "401", "")
    assert store.get("web_search").status == ToolStatus.UNAVAILABLE

    # 执行成功不应解除 UNAVAILABLE
    store.record_success("web_search")
    assert store.get("web_search").status == ToolStatus.UNAVAILABLE


def test_is_blocked_only_for_unavailable():
    store = HealthStore()
    assert not store.is_blocked("web_search")  # 默认 HEALTHY

    store.record_error("web_search", ErrorType.RATE_LIMIT, "429", "")
    assert not store.is_blocked("web_search")  # DEGRADED 不拦截

    store.record_error("web_search", ErrorType.RATE_LIMIT, "429", "")
    assert store.is_blocked("web_search")  # UNAVAILABLE 拦截


def test_annotate_description_healthy_unchanged():
    store = HealthStore()
    desc = "搜索工具"
    assert store.annotate_description("web_search", desc) == desc


def test_annotate_description_unavailable_injects_status():
    store = HealthStore()
    store.record_probe_failure(
        "web_search", ErrorType.RATE_LIMIT, "429", "使用 fetch_url 替代"
    )
    annotated = store.annotate_description("web_search", "搜索工具")
    assert "[状态: rate_limit_exceeded" in annotated
    assert "使用 fetch_url 替代" in annotated


def test_annotate_description_suggestion_truncated():
    store = HealthStore()
    long_suggestion = "A" * 200
    store.record_probe_failure("web_search", ErrorType.AUTH_ERROR, "401", long_suggestion)
    annotated = store.annotate_description("web_search", "desc")
    # suggestion 截断到 80 字符
    assert "A" * 80 in annotated
    assert "A" * 81 not in annotated


def test_summary_all_healthy():
    store = HealthStore()
    assert store.summary() == "所有工具状态正常"


def test_summary_shows_unhealthy():
    store = HealthStore()
    store.record_probe_failure("web_search", ErrorType.AUTH_ERROR, "401", "检查 key")
    summary = store.summary()
    assert "web_search" in summary
    assert "unavailable" in summary
    assert "auth_error" in summary
