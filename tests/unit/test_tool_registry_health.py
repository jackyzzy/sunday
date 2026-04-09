"""ToolRegistry 健康检查集成单元测试"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest
import yaml

from sunday.tools.health_store import ErrorType, HealthStore, ToolStatus
from sunday.tools.registry import ToolMeta, ToolRegistry


def _make_registry(tmp_path, health_store=None):
    from sunday.config import Settings

    config_file = tmp_path / "agent.yaml"
    config_file.write_text(yaml.dump({
        "model": {"provider": "anthropic", "id": "claude-test", "max_tokens": 4096},
        "tools": {
            "default_timeout": 30,
            "max_output_chars": 4096,
            "allow_list": [],
            "deny_list": [],
        },
    }))
    with patch.dict(os.environ, {
        "ANTHROPIC_API_KEY": "sk-ant-fake",
        "SUNDAY_CONFIGS_DIR": str(tmp_path),
    }):
        s = Settings()
        _ = s.sunday
        cfg = s.sunday
    return ToolRegistry(cfg, health_store=health_store)


@pytest.mark.asyncio
async def test_get_schemas_annotates_unhealthy(tmp_path):
    store = HealthStore()
    registry = _make_registry(tmp_path, health_store=store)

    async def dummy() -> str:
        return "ok"

    registry.register(ToolMeta(name="web_search", description="搜索工具"), dummy)

    store.record_probe_failure("web_search", ErrorType.RATE_LIMIT, "429", "使用替代工具")
    schemas = registry.get_schemas()
    ws_schema = next(s for s in schemas if s["name"] == "web_search")
    assert "rate_limit_exceeded" in ws_schema["description"]
    assert "使用替代工具" in ws_schema["description"]


@pytest.mark.asyncio
async def test_execute_blocked_tool_returns_unavailable_message(tmp_path):
    store = HealthStore()
    registry = _make_registry(tmp_path, health_store=store)

    async def dummy() -> str:
        return "should not reach here"

    registry.register(ToolMeta(name="web_search", description="搜索"), dummy)
    store.record_probe_failure("web_search", ErrorType.AUTH_ERROR, "401", "检查 key")

    result = await registry.execute("web_search", {}, "sess-1")
    assert "[工具不可用]" in result
    assert "auth_error" in result


@pytest.mark.asyncio
async def test_execute_error_updates_health_store(tmp_path):
    store = HealthStore()
    registry = _make_registry(tmp_path, health_store=store)

    async def failing_tool() -> str:
        raise Exception("HTTP 429: Too Many Requests rate limit exceeded")

    registry.register(ToolMeta(name="web_search", description="搜索"), failing_tool)
    result = await registry.execute("web_search", {}, "sess-1")

    assert "[工具错误]" in result
    h = store.get("web_search")
    assert h.status == ToolStatus.DEGRADED
    assert h.error_type == ErrorType.RATE_LIMIT


@pytest.mark.asyncio
async def test_execute_success_records_success(tmp_path):
    store = HealthStore()
    registry = _make_registry(tmp_path, health_store=store)

    # 先模拟一次 DEGRADED
    store.record_error("web_search", ErrorType.TIMEOUT, "timeout", "")
    assert store.get("web_search").status == ToolStatus.DEGRADED

    async def success_tool() -> str:
        return "搜索结果"

    registry.register(ToolMeta(name="web_search", description="搜索"), success_tool)
    await registry.execute("web_search", {}, "sess-1")

    assert store.get("web_search").status == ToolStatus.HEALTHY


def test_clone_shares_health_store(tmp_path):
    store = HealthStore()
    registry = _make_registry(tmp_path, health_store=store)
    cloned = registry.clone()

    # 两者共享同一个 HealthStore 实例
    assert registry._health_store is cloned._health_store

    # 在 cloned 上更新状态，原 registry 也能看到
    cloned._health_store.record_probe_failure(
        "web_search", ErrorType.AUTH_ERROR, "401", ""
    )
    assert registry._health_store.is_blocked("web_search")


@pytest.mark.asyncio
async def test_check_tool_health_exempted_from_level_0(tmp_path):
    """check_tool_health 自身即使 UNAVAILABLE 也不被第 0 级拦截。"""
    store = HealthStore()
    registry = _make_registry(tmp_path, health_store=store)

    # 强制标记 check_tool_health 为 UNAVAILABLE（极端情况）
    store.record_probe_failure("check_tool_health", ErrorType.AUTH_ERROR, "err", "")
    assert store.is_blocked("check_tool_health")

    async def fake_health() -> str:
        return "所有工具状态正常"

    registry.register(
        ToolMeta(name="check_tool_health", description="健康检查"), fake_health
    )
    result = await registry.execute("check_tool_health", {}, "sess-1")
    # 不应被拦截，应正常执行
    assert "[工具不可用]" not in result


@pytest.mark.asyncio
async def test_probe_all_updates_health_store(tmp_path):
    store = HealthStore()
    registry = _make_registry(tmp_path, health_store=store)

    probe_called = []

    async def mock_probe():
        from sunday.tools.probe import ProbeResult
        probe_called.append(True)
        return ProbeResult(success=False, error_type=ErrorType.AUTH_ERROR,
                           detail="401", suggestion="检查 key")

    registry.register(
        ToolMeta(name="web_search", description="搜索", probe=mock_probe),
        AsyncMock(return_value="ok"),
    )
    await registry.probe_all()

    assert probe_called  # probe 被调用
    assert store.is_blocked("web_search")
    assert store.get("web_search").error_type == ErrorType.AUTH_ERROR

    # 二次调用 probe_all 不重复执行（_pending_probes 已清空）
    probe_called.clear()
    await registry.probe_all()
    assert not probe_called
