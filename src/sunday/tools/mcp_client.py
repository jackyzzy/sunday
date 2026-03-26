"""Phase 4：MCPClientManager — MCP 客户端管理（轻量占位实现）

当前阶段不引入真实 MCP 子进程，提供接口占位。
Phase 5 Gateway 实装时替换为完整实现。
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sunday.config import MCPServerConfig

logger = logging.getLogger(__name__)


class MCPClientManager:
    """管理 MCP 服务器连接。

    Phase 4 占位实现：
    - initialize() 尝试连接，失败时记录 warning 不抛异常
    - get_tools() 返回空列表（真实工具由 Phase 5 接入）
    - close() 清理连接
    """

    def __init__(self) -> None:
        self._connected: dict[str, bool] = {}

    async def initialize(self, servers: "list[MCPServerConfig]") -> None:
        """初始化所有 MCP 服务器连接。失败时仅记录 warning。"""
        for server in servers:
            if not server.enabled:
                continue
            try:
                # Phase 4 占位：不实际启动子进程，仅记录
                logger.debug("MCP 服务器 %s 注册（Phase 5 实装）", server.name)
                self._connected[server.name] = False
            except Exception as e:
                logger.warning("MCP 服务器 %s 连接失败（忽略）：%s", server.name, e)

    def get_tools(self, server_name: str) -> list[dict]:
        """返回已连接服务器的工具列表。Phase 4 始终返回空列表。"""
        return []

    async def close(self) -> None:
        """关闭所有 MCP 连接。"""
        self._connected.clear()
