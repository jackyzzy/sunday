"""ToolRegistry × MCP 接入测试（fake manager，无子进程、不联网）。"""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import patch

import yaml

from sunday.tools.registry import ToolMeta, ToolRegistry


def _make_settings(tmp_path):
    from sunday.config import Settings
    config_file = tmp_path / "agent.yaml"
    config_file.write_text(yaml.dump({
        "model": {"provider": "anthropic", "id": "claude-test", "max_tokens": 4096},
        "tools": {"default_timeout": 30, "max_output_chars": 4096},
    }))
    with patch.dict(os.environ, {
        "ANTHROPIC_API_KEY": "sk-ant-fake",
        "SUNDAY_CONFIGS_DIR": str(tmp_path),
    }):
        s = Settings()
        _ = s.sunday
        return s


def _fake_tool(name, desc="搜索工具", schema=None):
    return SimpleNamespace(
        name=name, description=desc, inputSchema=schema or {"type": "object", "properties": {}}
    )


class _FakeManager:
    """伪 MCPClientManager：记录调用，返回固定工具与结果。"""

    def __init__(self, tools):
        self._tools = tools  # list[(server, tool)]
        self.initialize_calls = 0
        self.last_call = None

    async def initialize(self, servers):
        self.initialize_calls += 1

    def iter_tools(self):
        return list(self._tools)

    async def call_tool(self, server, name, arguments):
        self.last_call = (server, name, arguments)
        return f"result-of-{name}"

    async def close(self):
        pass


async def test_connect_mcp_registers_into_registry(tmp_path):
    """connect_mcp 后 MCP 工具出现在 get_schemas，且 execute 走 manager.call_tool。"""
    settings = _make_settings(tmp_path)
    registry = ToolRegistry(settings.sunday)
    mgr = _FakeManager([("tavily", _fake_tool("tavily_search"))])
    registry.attach_mcp(mgr, ["server"])

    await registry.connect_mcp()

    names = {s["name"] for s in registry.get_schemas()}
    assert "tavily_search" in names

    out = await registry.execute("tavily_search", {"query": "今天的新闻"}, "sess1")
    assert out == "result-of-tavily_search"
    assert mgr.last_call == ("tavily", "tavily_search", {"query": "今天的新闻"})


async def test_connect_mcp_idempotent(tmp_path):
    """连续两次 connect_mcp 只 initialize 一次、不重复注册。"""
    settings = _make_settings(tmp_path)
    registry = ToolRegistry(settings.sunday)
    mgr = _FakeManager([("tavily", _fake_tool("tavily_search"))])
    registry.attach_mcp(mgr, ["server"])

    await registry.connect_mcp()
    await registry.connect_mcp()

    assert mgr.initialize_calls == 1
    names = [s["name"] for s in registry.get_schemas()]
    assert names.count("tavily_search") == 1


async def test_name_collision_aliased(tmp_path):
    """MCP 工具与既有工具同名 → 退化为 {server}_{name}，原工具保留。"""
    settings = _make_settings(tmp_path)
    registry = ToolRegistry(settings.sunday)

    async def _existing(**_):
        return "builtin"

    registry.register(ToolMeta(name="fetch", description="内置"), _existing)
    mgr = _FakeManager([("fetch", _fake_tool("fetch"))])
    registry.attach_mcp(mgr, ["server"])

    await registry.connect_mcp()

    names = {s["name"] for s in registry.get_schemas()}
    assert "fetch" in names          # 内置仍在
    assert "fetch_fetch" in names    # MCP 工具被重命名


async def test_clone_shares_mcp_manager(tmp_path):
    """clone 后 MCP 工具仍可执行（共享 manager 引用 + 闭包捕获）。"""
    settings = _make_settings(tmp_path)
    registry = ToolRegistry(settings.sunday)
    mgr = _FakeManager([("tavily", _fake_tool("tavily_search"))])
    registry.attach_mcp(mgr, ["server"])
    await registry.connect_mcp()

    clone = registry.clone()
    out = await clone.execute("tavily_search", {"query": "x"}, "sess1")
    assert out == "result-of-tavily_search"


async def test_no_mcp_is_noop(tmp_path):
    """未 attach manager → connect_mcp no-op，get_schemas 无 MCP 工具。"""
    settings = _make_settings(tmp_path)
    registry = ToolRegistry(settings.sunday)
    await registry.connect_mcp()  # 不应抛
    await registry.close_mcp()
    assert registry.get_schemas() == []
