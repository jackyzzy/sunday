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


class ToolRegistry:
    """工具注册表：注册、过滤、确认、执行、Guard。

    执行管道（见 design.md §6.1）：
    1. 工具存在性检查
    2. allow_list / deny_list 过滤
    3. is_dangerous 确认
    4. asyncio.wait_for 超时执行
    5. ToolResultGuard 清洗输出
    """

    def __init__(
        self,
        config: "SundayConfig",
        confirmation_handler: ConfirmationHandler | None = None,
    ) -> None:
        cfg = config.tools
        self._allow_list: list[str] = cfg.allow_list
        self._deny_list: list[str] = cfg.deny_list
        self._default_timeout: int = cfg.default_timeout
        self._guard = ToolResultGuard(max_output_chars=cfg.max_output_chars)
        self._confirmation_handler = confirmation_handler
        self._tools: dict[str, tuple[ToolMeta, Callable]] = {}
        self._report_dir: "Path | None" = None

    def set_report_dir(self, d: "Path") -> None:
        """由 AgentLoop.run() 在每次任务开始时调用，路由 write_file 输出至 session 目录。"""
        self._report_dir = d

    def register(self, meta: ToolMeta, fn: Callable) -> None:
        """注册工具（allow/deny list 在执行时过滤，不在注册时过滤）。"""
        self._tools[meta.name] = (meta, fn)
        logger.debug("注册工具：%s（危险=%s）", meta.name, meta.is_dangerous)

    def get_schemas(self) -> list[dict]:
        """返回所有工具的 JSON Schema 列表（供 LLM 使用）。"""
        schemas = []
        for meta, _ in self._tools.values():
            schemas.append({
                "name": meta.name,
                "description": meta.description,
                "input_schema": meta.input_schema,
            })
        return schemas

    async def execute(self, tool_name: str, arguments: dict[str, Any], session_id: str) -> str:
        """执行工具调用，返回 Guard 处理后的字符串。"""
        # [1] 存在性检查
        if tool_name not in self._tools:
            return f"[工具错误] 未知工具：{tool_name}"

        meta, fn = self._tools[tool_name]

        # [2] allow_list / deny_list 过滤
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
        try:
            raw = await asyncio.wait_for(fn(**arguments), timeout=timeout)
        except asyncio.TimeoutError:
            return f"[工具超时] 工具 {tool_name} 超过 {timeout}s 未返回"
        except Exception as e:
            logger.warning("工具 %s 执行异常：%s", tool_name, e)
            return f"[工具错误] {tool_name} 执行失败：{e}"

        # [5] Guard 清洗
        return self._guard.validate(raw)
