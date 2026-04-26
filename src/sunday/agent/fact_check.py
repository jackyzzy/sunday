"""规划阶段 FACT_CHECK 子阶段 — 用白名单工具核实"可能不确定"的实体属性。

动机：L1/L2 跨会话记忆偶尔会把其他会话的实体属性污染进当前 plan prompt，导致 Planner
对任务主体判断错误（如把"自变量"误判为"AI 算力产业链公司"，实际是"具身智能公司"）。
本模块允许 Planner 在真正出 Plan 之前，针对几条可疑主张跑一次白名单工具调用（默认
web_search / read_file），把核实到的事实作为 verified_facts 注入 plan prompt。

开关位于 config.quality.fact_check.enabled；预算由 max_tool_calls / timeout_seconds 控制。
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from sunday.config import SundayConfig

logger = logging.getLogger(__name__)


class ToolRegistryLike(Protocol):
    """fact_check 依赖的 ToolRegistry 最小接口。"""

    async def execute(
        self, tool_name: str, arguments: dict, session_id: str
    ) -> str: ...

    def get_schemas(self) -> list[dict]: ...


@dataclass
class VerifiedFact:
    """一次事实核查的结果。"""

    claim: str        # 被核实的主张，例如 "自变量 是 什么行业的公司"
    finding: str      # 工具返回的关键事实摘要（已截断）
    tool: str = ""    # 实际调用的工具名称


async def maybe_fact_check(
    claims: list[str],
    config: "SundayConfig",
    tool_registry: ToolRegistryLike | None,
    session_id: str = "",
) -> list[VerifiedFact]:
    """针对每条 claim 调用一次白名单工具，按预算/超时收敛。

    - 开关关闭 / 无 tool_registry / 无 claims → 直接返回 []
    - 白名单工具均不可用 → 返回 []
    - 单次工具调用失败 → 跳过该条，继续下一条
    - 预算耗尽或整体超时 → 提前退出并返回已收集的事实
    """
    fc_cfg = config.quality.fact_check
    if not fc_cfg.enabled or not claims or tool_registry is None:
        return []

    available = {s["name"] for s in tool_registry.get_schemas()}
    allowed = [t for t in fc_cfg.allowed_tools if t in available]
    if not allowed:
        logger.info("fact_check: 白名单工具均不可用，跳过")
        return []

    facts: list[VerifiedFact] = []
    budget = fc_cfg.max_tool_calls

    async def _run() -> None:
        nonlocal budget
        for claim in claims:
            if budget <= 0:
                break
            tool_name = _choose_tool(claim, allowed)
            if tool_name is None:
                continue
            args = _build_args(claim, tool_name)
            try:
                output = await tool_registry.execute(tool_name, args, session_id)
            except Exception as e:
                logger.warning("fact_check: 工具 %s 调用异常：%s", tool_name, e)
                continue
            budget -= 1
            facts.append(VerifiedFact(
                claim=claim,
                finding=output[:800],
                tool=tool_name,
            ))

    try:
        await asyncio.wait_for(_run(), timeout=fc_cfg.timeout_seconds)
    except asyncio.TimeoutError:
        logger.info(
            "fact_check: 超时退出（%ds），已核实 %d 条",
            fc_cfg.timeout_seconds, len(facts),
        )
    return facts


def _choose_tool(claim: str, allowed: list[str]) -> str | None:
    """按简单启发式选工具：像文件路径用 read_file，否则用 web_search。"""
    text = claim.strip()
    looks_like_path = (
        "/" in text
        and not text.lower().startswith(("http://", "https://"))
        and " " not in text
    )
    if looks_like_path and "read_file" in allowed:
        return "read_file"
    if "web_search" in allowed:
        return "web_search"
    return allowed[0] if allowed else None


def _build_args(claim: str, tool_name: str) -> dict:
    if tool_name == "web_search":
        return {"query": claim, "max_results": 3}
    if tool_name == "read_file":
        return {"path": claim}
    return {"query": claim}
