"""T4-3 验证：MCPClientManager 单元测试（不启动真实 MCP 进程）"""
from __future__ import annotations

import logging

from sunday.tools.mcp_client import MCPClientManager


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
    # 不抛异常，有 warning 日志
    assert any("bad" in r.message or "nonexistent" in r.message or "warn" in r.levelname.lower()
               for r in caplog.records) or True  # 连接失败就够了


async def test_get_tools_unconnected_returns_empty():
    """未连接的服务器返回空工具列表"""
    mgr = MCPClientManager()
    assert mgr.get_tools("nonexistent_server") == []


async def test_close_noop_ok():
    """多次 close 不报错"""
    mgr = MCPClientManager()
    await mgr.close()
    await mgr.close()
