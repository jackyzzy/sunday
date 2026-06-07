"""MCPClientManager 单元测试（不启动真实 MCP 进程、不联网）。

Phase 5：扩展为真实 stdio 实现的测试，全程 mock stdio_client / ClientSession。
"""
from __future__ import annotations

import logging
import os
from types import SimpleNamespace
from unittest.mock import patch

from sunday.tools.mcp_client import MCPClientManager, _resolve_env

# ── Phase 4 契约保留 ──────────────────────────────────────────────────────────

async def test_initialize_no_servers_ok():
    """空服务器列表初始化不报错"""
    mgr = MCPClientManager()
    await mgr.initialize([])
    assert mgr.get_tools("any") == []


async def test_initialize_failed_server_logs_warning(caplog):
    """连接失败记录 warning，不抛异常"""
    from sunday.config import MCPServerConfig
    server = MCPServerConfig(name="bad", command="nonexistent_cmd_xyz", args=[], enabled=True)
    mgr = MCPClientManager()
    with caplog.at_level(logging.WARNING):
        await mgr.initialize([server])
    assert mgr.get_tools("bad") == []
    await mgr.close()


async def test_get_tools_unconnected_returns_empty():
    """未连接的服务器返回空工具列表"""
    mgr = MCPClientManager()
    assert mgr.get_tools("nonexistent_server") == []


async def test_close_noop_ok():
    """多次 close 不报错"""
    mgr = MCPClientManager()
    await mgr.close()
    await mgr.close()


# ── Phase 5 新增 ──────────────────────────────────────────────────────────────

def test_resolve_env_expands_vars():
    """${VAR} / $VAR 从 os.environ 展开；未定义保留原样。"""
    with patch.dict(os.environ, {"TAVILY_API_KEY": "secret123"}, clear=False):
        out = _resolve_env({
            "A": "${TAVILY_API_KEY}",
            "B": "$TAVILY_API_KEY",
            "C": "prefix-${TAVILY_API_KEY}-suffix",
            "D": "${UNDEFINED_VAR_XYZ}",
            "E": "no-vars",
        })
    assert out["A"] == "secret123"
    assert out["B"] == "secret123"
    assert out["C"] == "prefix-secret123-suffix"
    assert out["D"] == "${UNDEFINED_VAR_XYZ}"  # 未定义保留原样
    assert out["E"] == "no-vars"


class _FakeCM:
    """简易 async context manager，__aenter__ 返回固定值。"""

    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    """伪 ClientSession：既是 async CM，也提供 initialize/list_tools/call_tool。"""

    def __init__(self, read, write, *, tools=None, call_result=None):
        self._tools = tools or []
        self._call_result = call_result

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def initialize(self):
        return None

    async def list_tools(self):
        return SimpleNamespace(tools=self._tools)

    async def call_tool(self, name, arguments):
        return self._call_result


def _fake_tool(name, desc="desc", schema=None):
    return SimpleNamespace(
        name=name, description=desc, inputSchema=schema or {"type": "object", "properties": {}}
    )


def _patch_mcp(tools=None, call_result=None):
    """patch stdio_client + ClientSession，返回提供给 mgr.initialize 的伪实现。"""
    import mcp
    import mcp.client.stdio as stdio_mod

    def _session_factory(read, write):
        return _FakeSession(read, write, tools=tools, call_result=call_result)

    return [
        patch.object(stdio_mod, "stdio_client", lambda params: _FakeCM(("r", "w"))),
        patch.object(mcp, "ClientSession", _session_factory),
    ]


async def test_initialize_registers_tools():
    """成功连接后 iter_tools / get_tools 返回发现的工具。"""
    from sunday.config import MCPServerConfig
    tools = [_fake_tool("tavily_search"), _fake_tool("tavily_extract")]
    server = MCPServerConfig(name="tavily", command="npx", args=["-y", "x"], enabled=True)
    mgr = MCPClientManager()
    patches = _patch_mcp(tools=tools)
    for p in patches:
        p.start()
    try:
        await mgr.initialize([server])
        names = [t.name for _, t in mgr.iter_tools()]
        assert names == ["tavily_search", "tavily_extract"]
        schemas = mgr.get_tools("tavily")
        assert {s["name"] for s in schemas} == {"tavily_search", "tavily_extract"}
        assert schemas[0]["input_schema"] == {"type": "object", "properties": {}}
    finally:
        for p in patches:
            p.stop()
        await mgr.close()


async def test_call_tool_flattens_text_content():
    """call_tool 把多个 TextContent 文本拼接为字符串。"""
    from mcp.types import CallToolResult, TextContent

    from sunday.config import MCPServerConfig
    result = CallToolResult(
        content=[TextContent(type="text", text="第一段"), TextContent(type="text", text="第二段")],
        isError=False,
    )
    server = MCPServerConfig(name="tavily", command="npx", args=[], enabled=True)
    mgr = MCPClientManager()
    patches = _patch_mcp(tools=[_fake_tool("tavily_search")], call_result=result)
    for p in patches:
        p.start()
    try:
        await mgr.initialize([server])
        out = await mgr.call_tool("tavily", "tavily_search", {"query": "x"})
        assert out == "第一段\n第二段"
    finally:
        for p in patches:
            p.stop()
        await mgr.close()


async def test_call_tool_error_prefixed():
    """isError=True 时结果加 [MCP错误] 前缀。"""
    from mcp.types import CallToolResult, TextContent

    from sunday.config import MCPServerConfig
    result = CallToolResult(content=[TextContent(type="text", text="boom")], isError=True)
    server = MCPServerConfig(name="fetch", command="uvx", args=[], enabled=True)
    mgr = MCPClientManager()
    patches = _patch_mcp(tools=[_fake_tool("fetch")], call_result=result)
    for p in patches:
        p.start()
    try:
        await mgr.initialize([server])
        out = await mgr.call_tool("fetch", "fetch", {"url": "x"})
        assert out.startswith("[MCP错误]")
        assert "boom" in out
    finally:
        for p in patches:
            p.stop()
        await mgr.close()


async def test_call_tool_unconnected_server():
    """对未连接服务器调用返回错误字符串，不抛。"""
    mgr = MCPClientManager()
    out = await mgr.call_tool("ghost", "x", {})
    assert out.startswith("[MCP错误]")


async def test_sdk_missing_skips_gracefully(caplog):
    """MCP SDK 缺失（ImportError）时 warning 跳过、不抛、initialize no-op。"""
    from sunday.config import MCPServerConfig
    server = MCPServerConfig(name="tavily", command="npx", args=[], enabled=True)
    mgr = MCPClientManager()

    real_import = __import__

    def _fake_import(name, *args, **kwargs):
        if name == "mcp" or name.startswith("mcp."):
            raise ImportError("simulated missing mcp")
        return real_import(name, *args, **kwargs)

    with caplog.at_level(logging.WARNING), patch("builtins.__import__", _fake_import):
        await mgr.initialize([server])

    assert mgr.get_tools("tavily") == []
    assert any("MCP SDK" in r.message for r in caplog.records)
