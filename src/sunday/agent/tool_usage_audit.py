"""工具使用审计 — Verifier 的第三道闸门。

对 `Step.requires_realtime_data=True` 的步骤，校验它实际上是否成功调用过联网工具。
若既未联网也未带"未联网"标签，判 failed → 触发 replan。

设计原则（参照 subject_consistency.py）：
- Protocol 形式：方便未来替换为不同实现（小模型 / 启发式规则）
- 工厂方法 `build_tool_usage_auditor` 由 config 决定返回真实 checker 还是 _AlwaysPass 桩
- 关闭某能力时 caller 无需 if 判断，直接调用 .check()
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from sunday.agent.models import ReactIteration, Step

if TYPE_CHECKING:
    from sunday.config import SundayConfig


_SEARCH_TOOLS = ("web_search", "fetch_url")
_OFFLINE_LABEL_PREFIX = "> ⚠ 本节未联网验证"


@dataclass
class ToolAuditResult:
    """审计结论。"""

    passed: bool
    reason: str


class ToolUsageAuditChecker(Protocol):
    """对 Step 的工具使用情况做事后审计。"""

    async def check(
        self,
        step: Step,
        iterations: list[ReactIteration],
        output: str,
    ) -> ToolAuditResult: ...


class DefaultToolUsageAuditor:
    """默认实现：检查 realtime 步骤是否实际联网或带兜底标签。"""

    async def check(
        self,
        step: Step,
        iterations: list[ReactIteration],
        output: str,
    ) -> ToolAuditResult:
        if not step.requires_realtime_data:
            return ToolAuditResult(passed=True, reason="非实时步骤，跳过审计")
        if any(_is_search_success(it) for it in iterations):
            return ToolAuditResult(passed=True, reason="联网工具已成功调用")
        if output.lstrip().startswith(_OFFLINE_LABEL_PREFIX):
            return ToolAuditResult(
                passed=True,
                reason="未联网但已含未联网标签，容忍通过",
            )
        return ToolAuditResult(
            passed=False,
            reason="该步骤要求实时数据但未成功调联网工具，且输出缺少未联网标签",
        )


class _AlwaysPassAuditor:
    """禁用桩：所有步骤一律通过。"""

    async def check(
        self,
        step: Step,
        iterations: list[ReactIteration],
        output: str,
    ) -> ToolAuditResult:
        return ToolAuditResult(passed=True, reason="tool_usage_audit 已禁用")


def build_tool_usage_auditor(config: "SundayConfig") -> ToolUsageAuditChecker:
    """根据 `config.quality.tool_usage_audit.enabled` 选择实现。"""
    if config.quality.tool_usage_audit.enabled:
        return DefaultToolUsageAuditor()
    return _AlwaysPassAuditor()


def _is_search_success(it: ReactIteration) -> bool:
    if it.tool_name not in _SEARCH_TOOLS:
        return False
    obs = it.observation or ""
    return not obs.lstrip().startswith("[错误]")
