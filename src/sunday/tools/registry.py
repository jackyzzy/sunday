"""Phase 4：ToolRegistry — 工具注册、路由、安全执行"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from pydantic import BaseModel

from sunday.tools.guard import ToolResultGuard

if TYPE_CHECKING:
    from pathlib import Path

    from sunday.config import SundayConfig
    from sunday.tools.health_store import HealthStore
    from sunday.tools.probe import ProbeFunc

logger = logging.getLogger(__name__)

# confirmation_handler(tool_name, arguments, session_id) -> bool
ConfirmationHandler = Callable[[str, dict, str], Awaitable[bool]]


class ToolMeta(BaseModel):
    """工具元数据。"""

    name: str
    description: str
    input_schema: dict = {}
    is_dangerous: bool = False
    timeout: int = 30  # 秒
    probe: Any = None  # ProbeFunc | None，不参与 get_schemas() 输出

    model_config = {"arbitrary_types_allowed": True}


class ToolRegistry:
    """工具注册表：注册、路由、过滤、确认、执行、Guard。

    执行管道：
    0. 健康状态检查（新增）
    1. 工具存在性检查
    2. allow_list / deny_list 过滤
    3. is_dangerous 确认
    4. asyncio.wait_for 超时执行
    5. ToolResultGuard 清洗输出 + 健康状态更新
    """

    def __init__(
        self,
        config: "SundayConfig",
        confirmation_handler: ConfirmationHandler | None = None,
        health_store: "HealthStore | None" = None,
    ) -> None:
        from sunday.tools.health_store import HealthStore as _HealthStore

        cfg = config.tools
        self._allow_list: list[str] = cfg.allow_list
        self._deny_list: list[str] = cfg.deny_list
        self._default_timeout: int = cfg.default_timeout
        self._guard = ToolResultGuard(max_output_chars=cfg.max_output_chars)
        self._confirmation_handler = confirmation_handler
        self._tools: dict[str, tuple[ToolMeta, Callable]] = {}
        self._report_dir: "Path | None" = None
        self._agent_written_files: list[str] = []
        self._health_store: _HealthStore = health_store or _HealthStore()
        self._pending_probes: dict[str, "ProbeFunc"] = {}
        self._mcp_manager: Any = None  # MCPClientManager | None
        self._mcp_servers: list = []
        self._mcp_connected: bool = False

    def clone(self) -> "ToolRegistry":
        """返回当前注册表的独立副本（工具函数引用共享，_tools 字典独立）。

        用于 ReactAgent._create_node()：每个 Team/SimpleNode 获得自己的副本，
        可在基础工具集之上按需叠加专属工具，而不影响其他节点或基础注册表。
        HealthStore 共享引用，跨节点状态更新全局可见，累计阈值正确计算。
        """
        new = ToolRegistry.__new__(ToolRegistry)
        new._tools = dict(self._tools)  # 浅拷贝，函数引用共享
        new._allow_list = self._allow_list
        new._deny_list = self._deny_list
        new._default_timeout = self._default_timeout
        new._guard = self._guard
        new._confirmation_handler = self._confirmation_handler
        new._report_dir = self._report_dir
        new._agent_written_files = self._agent_written_files  # 共享引用，所有克隆体写同一列表
        new._health_store = self._health_store  # 共享引用
        new._pending_probes = {}  # clone 不继承待探测队列
        new._mcp_manager = self._mcp_manager  # 共享 manager 引用
        new._mcp_servers = self._mcp_servers
        new._mcp_connected = self._mcp_connected
        return new

    def set_report_dir(self, d: "Path") -> None:
        """由 AgentLoop.run() 在每次任务开始时调用，路由 write_file 输出至 session 目录。"""
        self._report_dir = d

    def add_written_file(self, name: str) -> None:
        """记录 Agent 通过 write_file 写入的文件名（去重，保留写入顺序）。"""
        if name not in self._agent_written_files:
            self._agent_written_files.append(name)

    @property
    def agent_written_files(self) -> list[str]:
        """返回当前任务中 Agent 写入的文件名列表（有序）。"""
        return list(self._agent_written_files)

    def register(self, meta: ToolMeta, fn: Callable) -> None:
        """注册工具，并收集 probe 函数供 probe_all() 批量执行。"""
        self._tools[meta.name] = (meta, fn)
        logger.debug("注册工具：%s（危险=%s）", meta.name, meta.is_dangerous)

        # 确定 probe 函数：优先 ToolMeta.probe，其次 BUILTIN_PROBES
        probe_fn = meta.probe
        if probe_fn is None:
            from sunday.tools.probe import BUILTIN_PROBES
            probe_fn = BUILTIN_PROBES.get(meta.name)
        if probe_fn is not None:
            self._pending_probes[meta.name] = probe_fn

    async def probe_all(self) -> None:
        """并发执行所有待探测工具的 probe，初始化 HealthStore（B 场景）。

        在 ReactAgent.run() 入口调用一次，之后 _pending_probes 清空不再重复执行。
        每个 probe 独立设置 15s 超时，不阻塞其他工具的探测。
        """
        from sunday.tools.health_store import ErrorType
        from sunday.tools.probe import ProbeResult

        if not self._pending_probes:
            return

        async def _probe_one(tool_name: str, probe_fn: "ProbeFunc") -> None:
            try:
                result: ProbeResult = await asyncio.wait_for(probe_fn(), timeout=15)
            except Exception as e:
                result = ProbeResult(
                    success=False,
                    error_type=ErrorType.NETWORK_ERROR,
                    detail=str(e),
                    suggestion="探测超时或网络异常",
                )
            if result.success:
                self._health_store.record_probe_success(tool_name)
                logger.info("工具探测成功：%s", tool_name)
            else:
                self._health_store.record_probe_failure(
                    tool_name,
                    result.error_type or ErrorType.UNKNOWN,
                    result.detail,
                    result.suggestion,
                )
                logger.warning("工具探测失败：%s — %s", tool_name, result.detail)

        await asyncio.gather(*[
            _probe_one(name, fn) for name, fn in self._pending_probes.items()
        ])
        self._pending_probes.clear()

    def attach_mcp(self, manager: Any, servers: list) -> None:
        """注入 MCPClientManager 与待连接的 server 列表（build 期调用，不连接）。"""
        self._mcp_manager = manager
        self._mcp_servers = servers

    async def connect_mcp(self) -> None:
        """连接 MCP 服务器并把发现的工具注册进本注册表（幂等）。

        在 ReactAgent.run() 入口、任何 clone() 之前调用一次，使各 Team/SimpleNode
        的克隆体天然继承 MCP 工具。命名用 MCP 原始工具名；与现有工具冲突时退化为
        f"{server}_{name}" 并 warning，避免静默覆盖。
        """
        if self._mcp_manager is None or self._mcp_connected:
            return
        self._mcp_connected = True  # 先置位，避免并发/重入重复连接
        await self._mcp_manager.initialize(self._mcp_servers)

        for server_name, tool in self._mcp_manager.iter_tools():
            name = tool.name
            if name in self._tools:
                aliased = f"{server_name}_{name}"
                logger.warning("MCP 工具名冲突：%s 已存在，重命名为 %s", name, aliased)
                name = aliased
            meta = ToolMeta(
                name=name,
                description=tool.description or f"MCP 工具（来自 {server_name}）",
                input_schema=tool.inputSchema or {},
                timeout=self._default_timeout,
            )

            def _make_fn(srv: str, tname: str) -> Callable:
                async def _call(**arguments: Any) -> str:
                    return await self._mcp_manager.call_tool(srv, tname, arguments)
                return _call

            self.register(meta, _make_fn(server_name, tool.name))

    async def close_mcp(self) -> None:
        """关闭 MCP 连接（随任务结束回收子进程）。"""
        if self._mcp_manager is not None and self._mcp_connected:
            await self._mcp_manager.close()
            self._mcp_connected = False

    def get_schemas(self) -> list[dict]:
        """返回所有工具的 JSON Schema 列表（不可用工具注入状态标注）。"""
        schemas = []
        for meta, _ in self._tools.values():
            description = self._health_store.annotate_description(meta.name, meta.description)
            schemas.append({
                "name": meta.name,
                "description": description,
                "input_schema": meta.input_schema,
            })
        return schemas

    async def execute(self, tool_name: str, arguments: dict[str, Any], session_id: str) -> str:
        """执行工具调用，返回 Guard 处理后的字符串。"""
        # [0] 存在性检查
        if tool_name not in self._tools:
            return f"[工具错误] 未知工具：{tool_name}"

        # [1] 健康状态检查（check_tool_health 自身豁免，避免递归拦截）
        if tool_name != "check_tool_health" and self._health_store.is_blocked(tool_name):
            h = self._health_store.get(tool_name)
            status_str = h.error_type.value if h.error_type else "unavailable"
            msg = f"[工具不可用] {tool_name} 当前状态: {status_str}"
            if h.suggestion:
                msg += f"，建议: {h.suggestion}"
            return msg

        meta, fn = self._tools[tool_name]

        # [2] allow_list / deny_list 过滤（已确认工具存在）
        if self._allow_list and tool_name not in self._allow_list:
            return f"[工具拒绝] 工具 {tool_name} 不在允许列表中"
        if tool_name in self._deny_list:
            return f"[工具拒绝] 工具 {tool_name} 在禁止列表中"

        # [3] 危险工具确认
        if meta.is_dangerous:
            if self._confirmation_handler is None:
                return f"[工具取消] 工具 {tool_name} 需要确认，但未配置确认处理器"
            confirmed = await self._confirmation_handler(tool_name, arguments, session_id)
            if not confirmed:
                return f"[工具取消] 用户拒绝执行工具：{tool_name}"

        # [4] 超时执行
        timeout = meta.timeout or self._default_timeout
        error_result: str | None = None
        try:
            raw = await asyncio.wait_for(fn(**arguments), timeout=timeout)
        except asyncio.TimeoutError:
            error_result = f"[工具超时] 工具 {tool_name} 超过 {timeout}s 未返回"
        except Exception as e:
            logger.warning("工具 %s 执行异常：%s", tool_name, e)
            error_result = f"[工具错误] {tool_name} 执行失败：{e}"

        # [5] 成功路径：Guard 清洗 + 记录成功
        if error_result is None:
            self._health_store.record_success(tool_name)
            return self._guard.validate(raw)

        # [5'] 错误路径：分类 + 动态更新 HealthStore（C 场景）
        from sunday.tools.error_classifier import classify, should_classify
        if should_classify(error_result):
            error_type, suggestion = classify(error_result)
            self._health_store.record_error(tool_name, error_type, error_result, suggestion)

        return error_result
