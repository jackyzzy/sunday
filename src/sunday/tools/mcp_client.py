"""Phase 5：MCPClientManager — 真实 MCP stdio 客户端管理。

生命周期收敛到单次 `ReactAgent.run()` 内（同一 asyncio task 连接→使用→关闭），
规避 stdio AsyncExitStack 跨-task cancel-scope 问题。

- `initialize(servers)`：逐个启动 enabled server 的 stdio 子进程，list_tools 缓存。
  单 server 失败仅 warning 不抛；MCP SDK 未安装时整体降级跳过。
- `iter_tools()` / `get_tools(server)`：枚举已连接工具。
- `call_tool(server, name, args)`：转发调用并扁平化为字符串。
- `close()`：幂等关闭所有连接。
"""
from __future__ import annotations

import logging
import os
import re
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mcp.types import Tool as McpTool

    from sunday.config import MCPServerConfig

logger = logging.getLogger(__name__)

# 匹配 ${NAME} 或 $NAME（NAME 为字母/数字/下划线，首字符非数字）
_ENV_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")


def _resolve_env(env: dict[str, str]) -> dict[str, str]:
    """展开 env 值中的 ${VAR} / $VAR（取自 os.environ）；未定义的变量保留原样。"""

    def _sub(m: "re.Match[str]") -> str:
        name = m.group(1) or m.group(2)
        return os.environ.get(name, m.group(0))

    return {k: _ENV_VAR_RE.sub(_sub, v) for k, v in env.items()}


class MCPClientManager:
    """管理 MCP 服务器连接（stdio 子进程）。"""

    def __init__(self) -> None:
        self._stack = AsyncExitStack()
        self._sessions: dict[str, Any] = {}  # server_name -> ClientSession
        self._tools: dict[str, list["McpTool"]] = {}  # server_name -> tools

    async def initialize(self, servers: "list[MCPServerConfig]") -> None:
        """初始化所有 enabled MCP 服务器连接。单点失败仅 warning，不抛。"""
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError as e:
            logger.warning("MCP SDK 未安装，跳过 MCP 工具加载：%s", e)
            return

        for server in servers:
            if not server.enabled:
                continue
            try:
                params = StdioServerParameters(
                    command=server.command,
                    args=server.args,
                    # 叠加在 os.environ 之上：保证子进程能找到 PATH（npx/uvx）并拿到展开后的 KEY
                    env={**os.environ, **_resolve_env(server.env)},
                )
                read, write = await self._stack.enter_async_context(stdio_client(params))
                session = await self._stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
                tools_result = await session.list_tools()
                self._sessions[server.name] = session
                self._tools[server.name] = list(tools_result.tools)
                logger.info(
                    "MCP 服务器 %s 已连接，发现 %d 个工具",
                    server.name,
                    len(self._tools[server.name]),
                )
            except Exception as e:
                logger.warning("MCP 服务器 %s 连接失败（忽略）：%s", server.name, e)

    def iter_tools(self) -> list[tuple[str, "McpTool"]]:
        """返回 [(server_name, tool), ...]，展开所有已连接服务器的工具。"""
        return [(name, tool) for name, tools in self._tools.items() for tool in tools]

    def get_tools(self, server_name: str) -> list[dict]:
        """返回指定服务器的工具 schema 列表；未连接返回空列表。"""
        return [
            {
                "name": tool.name,
                "description": tool.description or "",
                "input_schema": tool.inputSchema or {},
            }
            for tool in self._tools.get(server_name, [])
        ]

    async def call_tool(self, server_name: str, tool_name: str, arguments: dict) -> str:
        """调用 MCP 工具，扁平化结果为字符串。isError → [MCP错误] 前缀。"""
        session = self._sessions.get(server_name)
        if session is None:
            return f"[MCP错误] 服务器 {server_name} 未连接"

        result = await session.call_tool(tool_name, arguments)
        text = self._flatten_content(result.content)
        if result.isError:
            return f"[MCP错误] {tool_name}：{text}"
        return text

    @staticmethod
    def _flatten_content(content: list) -> str:
        """提取 content 块中的文本（TextContent.text），其余类型以占位标注。"""
        parts: list[str] = []
        for block in content:
            text = getattr(block, "text", None)
            if text is not None:
                parts.append(text)
            else:
                parts.append(f"[非文本内容：{getattr(block, 'type', 'unknown')}]")
        return "\n".join(parts)

    async def close(self) -> None:
        """关闭所有 MCP 连接（幂等）。"""
        await self._stack.aclose()
        self._sessions.clear()
        self._tools.clear()
