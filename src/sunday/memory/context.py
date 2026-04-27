"""ContextBuilder — 记忆分层上下文组装

注入顺序（L0 → L1 → L2 → L3 当前 session → task）：
    L0  workspace/SOUL.md、AGENTS.md、TOOLS.md    — 用户静态配置
    L1  memory/MEMORY.md、memory/USER.md           — AI 长期摘要
    L2  memory/daily/YYYY-MM-DD.md（昨日/今日）  — 每日摘要
    L3  当前 session 历史轮次                       — 已由 _extract_conversation() 注入
        跨 session 检索（TODO，预留接口，当前返回空字符串）

读写均走 MemoryClient.workspace + MemoryClient.knowledge，不直接读文件。
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING, Protocol

from pydantic import BaseModel

if TYPE_CHECKING:
    from sunday.config import SundayConfig
    from sunday.memory.client import MemoryClient


class SkillLoaderProtocol(Protocol):
    """ContextBuilder 依赖的技能加载器接口（避免循环 import）"""

    def get_summary_list(self) -> str: ...


class Context(BaseModel):
    """组装后的上下文"""

    system_prompt: str
    token_estimate: int


class ContextBuilder:
    """按层级顺序组装系统提示（L0 → L1 → L2）。

    各节以 "---" 分隔；文件不存在时静默跳过。
    """

    def __init__(
        self,
        client: "MemoryClient",
        skill_loader: SkillLoaderProtocol | None = None,
        config: "SundayConfig | None" = None,
        l0_max_lines: int = 100,
    ) -> None:
        self._client = client
        self._skill_loader = skill_loader
        self._l0_max_lines = config.memory.l0_max_lines if config else l0_max_lines

    async def build(self, session_id: str = "") -> Context:  # noqa: ARG002 (session_id reserved for L3)
        """组装 L0-L2 系统提示，返回 Context。

        L3 当前 session 历史由调用方（Planner）通过 _extract_conversation() 注入。
        """
        parts: list[str] = []

        # ── L0：用户静态配置 ────────────────────────────────────────────
        soul = await self._client.workspace.read("SOUL")
        if soul.strip():
            parts.append(soul)
        agents = await self._client.workspace.read("AGENTS")
        if agents.strip():
            parts.append(agents)

        # ── L1 + L2：AI 长期摘要 + 每日摘要 ────────────────────────────
        cross_session_parts: list[str] = []

        memory = await self._client.knowledge.read_layer("MEMORY")
        if memory.strip():
            lines = memory.splitlines()
            if len(lines) > self._l0_max_lines:
                memory = "\n".join(lines[-self._l0_max_lines:])
            cross_session_parts.append(memory)

        user = await self._client.knowledge.read_layer("USER")
        if user.strip():
            cross_session_parts.append(user)

        yesterday_text = await self._client.knowledge.read_daily(
            date.today() - timedelta(days=1)
        )
        if yesterday_text.strip():
            cross_session_parts.append(yesterday_text)

        today = date.today()
        today_text = await self._client.knowledge.read_daily(today)
        if today_text.strip():
            cross_session_parts.append(today_text)

        if cross_session_parts:
            parts.append("# 跨会话长期背景（仅供参考，非当前会话历史）")
            parts.extend(cross_session_parts)

        # ── 其他 ────────────────────────────────────────────────────────
        if self._skill_loader is not None:
            summary = self._skill_loader.get_summary_list()
            if summary.strip():
                parts.append(f"## 可用技能\n{summary}")
        tools = await self._client.workspace.read("TOOLS")
        if tools.strip():
            parts.append(tools)
        parts.append(f"当前日期：{today.isoformat()}")

        system_prompt = "\n\n---\n\n".join(p for p in parts if p.strip())
        return Context(
            system_prompt=system_prompt,
            token_estimate=len(system_prompt) // 4,
        )
