"""工具健康状态存储 — 会话级，无持久化。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional


class ToolStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"        # 失败未达阈值，仍可尝试
    UNAVAILABLE = "unavailable"  # 确认不可用，LLM 应降级


class ErrorType(str, Enum):
    RATE_LIMIT = "rate_limit_exceeded"
    AUTH_ERROR = "auth_error"
    NETWORK_ERROR = "network_error"
    TIMEOUT = "timeout"
    NOT_FOUND = "not_found"
    UNKNOWN = "unknown_error"


@dataclass
class ToolHealth:
    tool_name: str
    status: ToolStatus = ToolStatus.HEALTHY
    error_type: Optional[ErrorType] = None
    error_detail: str = ""
    consecutive_failures: int = 0
    last_checked_at: Optional[datetime] = None
    suggestion: str = ""


class HealthStore:
    """会话级工具健康状态存储。

    - 注册时探测失败 → 直接 UNAVAILABLE
    - 执行时错误 → 累计，达 FAILURE_THRESHOLD 才升级为 UNAVAILABLE
    - 执行成功 → 重置 DEGRADED；不自动解除 UNAVAILABLE（需显式 probe）
    - clone() 共享同一 HealthStore 引用，跨节点状态全局可见
    """

    FAILURE_THRESHOLD = 2

    def __init__(self) -> None:
        self._store: dict[str, ToolHealth] = {}

    def get(self, tool_name: str) -> ToolHealth:
        """获取工具健康记录，未记录时返回默认 HEALTHY。"""
        return self._store.get(tool_name, ToolHealth(tool_name=tool_name))

    def record_probe_success(self, tool_name: str) -> None:
        """注册时探测成功 → HEALTHY，重置计数。"""
        h = self._get_or_create(tool_name)
        h.status = ToolStatus.HEALTHY
        h.consecutive_failures = 0
        h.error_type = None
        h.error_detail = ""
        h.suggestion = ""
        h.last_checked_at = datetime.now()

    def record_probe_failure(
        self,
        tool_name: str,
        error_type: ErrorType,
        detail: str,
        suggestion: str,
    ) -> None:
        """注册时探测失败 → 直接 UNAVAILABLE。"""
        h = self._get_or_create(tool_name)
        h.status = ToolStatus.UNAVAILABLE
        h.error_type = error_type
        h.error_detail = detail
        h.suggestion = suggestion
        h.last_checked_at = datetime.now()

    def record_error(
        self,
        tool_name: str,
        error_type: ErrorType,
        detail: str,
        suggestion: str,
    ) -> None:
        """执行时错误 → 累计失败，达阈值升级为 UNAVAILABLE。"""
        h = self._get_or_create(tool_name)
        h.consecutive_failures += 1
        h.error_type = error_type
        h.error_detail = detail
        h.suggestion = suggestion
        h.last_checked_at = datetime.now()
        if h.consecutive_failures >= self.FAILURE_THRESHOLD:
            h.status = ToolStatus.UNAVAILABLE
        else:
            h.status = ToolStatus.DEGRADED

    def record_success(self, tool_name: str) -> None:
        """执行成功 → 重置 DEGRADED；不自动解除 UNAVAILABLE。"""
        h = self._get_or_create(tool_name)
        if h.status != ToolStatus.UNAVAILABLE:
            h.consecutive_failures = 0
            h.status = ToolStatus.HEALTHY
            h.error_type = None
            h.error_detail = ""
            h.suggestion = ""

    def is_blocked(self, tool_name: str) -> bool:
        """是否应在执行管道第 0 级拦截（仅 UNAVAILABLE）。"""
        return self.get(tool_name).status == ToolStatus.UNAVAILABLE

    def annotate_description(self, tool_name: str, description: str) -> str:
        """为 get_schemas() 注入健康状态标注（HEALTHY 时不修改）。"""
        h = self.get(tool_name)
        if h.status == ToolStatus.HEALTHY:
            return description
        status_str = h.error_type.value if h.error_type else h.status.value
        suffix = f"\n[状态: {status_str}"
        if h.suggestion:
            # 截断过长的建议，避免撑大 token 用量
            suggestion = h.suggestion[:80]
            suffix += f" | 建议: {suggestion}"
        suffix += "]"
        return description + suffix

    def summary(self) -> str:
        """返回所有非 HEALTHY 工具的摘要，无异常时返回提示。"""
        lines = []
        for name, h in self._store.items():
            if h.status != ToolStatus.HEALTHY:
                line = f"- {name}: {h.status.value}"
                if h.error_type:
                    line += f" ({h.error_type.value})"
                if h.suggestion:
                    line += f" | {h.suggestion}"
                lines.append(line)
        return "\n".join(lines) if lines else "所有工具状态正常"

    def _get_or_create(self, tool_name: str) -> ToolHealth:
        if tool_name not in self._store:
            self._store[tool_name] = ToolHealth(tool_name=tool_name)
        return self._store[tool_name]
