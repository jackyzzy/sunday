"""工具健康探测端到端集成测试（mock httpx，无真实 API 调用）"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from sunday.tools.health_store import ToolStatus


def _make_registry(tmp_path):
    from sunday.config import Settings
    from sunday.tools.registry import ToolRegistry

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
    return ToolRegistry(cfg)


@pytest.mark.asyncio
async def test_probe_failure_on_register_injects_annotation(tmp_path):
    """注册时探测失败 → get_schemas() 含状态标注。"""
    mock_resp = MagicMock()
    mock_resp.status_code = 401
    mock_resp.text = "Unauthorized: invalid api key"

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_resp)

    from sunday.tools.registry import ToolMeta

    registry = _make_registry(tmp_path)

    async def fake_search(query: str) -> str:
        return "结果"

    from sunday.tools.probe import probe_tavily
    registry.register(
        ToolMeta(name="web_search", description="Tavily 搜索工具", probe=probe_tavily),
        fake_search,
    )

    with patch.dict(os.environ, {"TAVILY_API_KEY": "sk-wrong-key"}):
        with patch("httpx.AsyncClient", return_value=mock_client):
            await registry.probe_all()

    schemas = registry.get_schemas()
    ws = next(s for s in schemas if s["name"] == "web_search")
    assert "[状态:" in ws["description"]
    assert "auth_error" in ws["description"]


@pytest.mark.asyncio
async def test_health_recovery_via_check_tool(tmp_path):
    """先探测失败 → UNAVAILABLE，再用 check_tool_health 恢复 → HEALTHY。"""
    from sunday.tools.registry import ToolMeta

    registry = _make_registry(tmp_path)

    async def fake_search(query: str) -> str:
        return "结果"

    # 401 响应 mock
    mock_resp_fail = MagicMock()
    mock_resp_fail.status_code = 401
    mock_resp_fail.text = "Unauthorized"
    mock_client_fail = AsyncMock()
    mock_client_fail.__aenter__ = AsyncMock(return_value=mock_client_fail)
    mock_client_fail.__aexit__ = AsyncMock(return_value=False)
    mock_client_fail.post = AsyncMock(return_value=mock_resp_fail)

    from sunday.tools.probe import probe_tavily
    registry.register(
        ToolMeta(name="web_search", description="Tavily 搜索工具", probe=probe_tavily),
        fake_search,
    )

    # 第一次探测失败
    with patch.dict(os.environ, {"TAVILY_API_KEY": "sk-wrong-key"}):
        with patch("httpx.AsyncClient", return_value=mock_client_fail):
            await registry.probe_all()

    assert registry._health_store.is_blocked("web_search")

    # 注册 check_tool_health
    from sunday.tools.cli_tool import _register_health_tool
    _register_health_tool(registry)

    # 200 响应 mock（恢复）
    mock_resp_ok = MagicMock()
    mock_resp_ok.status_code = 200
    mock_resp_ok.text = '{"results": []}'
    mock_client_ok = AsyncMock()
    mock_client_ok.__aenter__ = AsyncMock(return_value=mock_client_ok)
    mock_client_ok.__aexit__ = AsyncMock(return_value=False)
    mock_client_ok.post = AsyncMock(return_value=mock_resp_ok)

    with patch.dict(os.environ, {"TAVILY_API_KEY": "sk-valid-key"}):
        with patch("httpx.AsyncClient", return_value=mock_client_ok):
            result = await registry.execute(
                "check_tool_health", {"tool_name": "web_search"}, "sess-1"
            )

    assert "成功" in result
    assert registry._health_store.get("web_search").status == ToolStatus.HEALTHY
    assert not registry._health_store.is_blocked("web_search")
