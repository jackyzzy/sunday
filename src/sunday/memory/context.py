"""ContextBuilder — 记忆分层上下文组装

注入顺序（L0 → L1 → L2 → L3 当前 session → task）：
    L0  workspace/SOUL.md、AGENTS.md、TOOLS.md    — 用户静态配置
    L1  memory/MEMORY.md、memory/USER.md           — AI 长期摘要
    L2  memory/daily/YYYY-MM-DD.md（昨日/今日）  — 每日摘要
    L3  当前 session 历史轮次                       — 已由 _extract_conversation() 注入
        跨 session 检索（TODO，预留接口，当前返回空字符串）
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from pydantic import BaseModel

if TYPE_CHECKING:
    from sunday.config import SundayConfig


class SkillLoaderProtocol(Protocol):
    """ContextBuilder 依赖的技能加载器接口（避免循环 import）"""

    def get_summary_list(self) -> str: ...


class Context(BaseModel):
    """组装后的上下文"""

    system_prompt: str
    token_estimate: int  # len(system_prompt) // 4 粗估


class ContextBuilder:
    """按层级顺序组装系统提示（L0 → L1 → L2）。

    各节以 "---" 分隔；文件不存在时静默跳过。

    路径约定：
        workspace_dir/SOUL.md        L0
        workspace_dir/AGENTS.md      L0
        workspace_dir/TOOLS.md       L0
        memory_dir/MEMORY.md         L1
        memory_dir/USER.md           L1
        memory_dir/daily/YYYY-MM-DD.md  L2
    """

    def __init__(
        self,
        workspace_dir: Path,
        skill_loader: SkillLoaderProtocol | None = None,
        config: "SundayConfig | None" = None,
        l0_max_lines: int = 100,
        memory_dir: Path | None = None,
    ) -> None:
        self.workspace_dir = workspace_dir
        # memory_dir 独立于 workspace_dir（L1+L2）
        # 若未指定，回退到旧的 workspace_dir/memory 路径（向后兼容）
        if memory_dir is not None:
            self.memory_dir = memory_dir
            self._daily_dir = memory_dir / "daily"
        else:
            self.memory_dir = workspace_dir  # L1 文件在 workspace_dir 根目录（旧路径）
            self._daily_dir = workspace_dir / "memory"
        self.skill_loader = skill_loader
        self.l0_max_lines = config.memory.l0_max_lines if config else l0_max_lines

    def build(self, session_id: str = "") -> Context:
        """组装 L0-L2 系统提示，返回 Context。

        L3 当前 session 历史由调用方（Planner）通过 _extract_conversation() 注入。
        """
        parts: list[str] = []

        # ── L0：用户静态配置 ────────────────────────────────────────────
        # 1. SOUL.md
        self._add_file(parts, self.workspace_dir / "SOUL.md")
        # 2. AGENTS.md
        self._add_file(parts, self.workspace_dir / "AGENTS.md")

        # ── L1 + L2：AI 长期摘要 + 每日摘要 ────────────────────────────
        # 使用临时列表累积 L1/L2 内容，若有任何内容则在前面加一段节标题，
        # 让 Planner 从结构上识别"这是跨会话背景，不是当前会话的延续"。
        cross_session_parts: list[str] = []

        # 3. MEMORY.md（末尾 l0_max_lines 行）
        memory_path = self.memory_dir / "MEMORY.md"
        if memory_path.exists():
            content = memory_path.read_text(encoding="utf-8")
            lines = content.splitlines()
            if len(lines) > self.l0_max_lines:
                content = "\n".join(lines[-self.l0_max_lines:])
            if content.strip():
                cross_session_parts.append(content)
        # 4. USER.md
        self._add_file(cross_session_parts, self.memory_dir / "USER.md")

        # 5. 昨日日志
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        self._add_file(cross_session_parts, self._daily_dir / f"{yesterday}.md")
        # 6. 今日日志
        today = date.today().isoformat()
        self._add_file(cross_session_parts, self._daily_dir / f"{today}.md")

        if cross_session_parts:
            parts.append("# 跨会话长期背景（仅供参考，非当前会话历史）")
            parts.extend(cross_session_parts)

        # ── 其他 ────────────────────────────────────────────────────────
        # 7. 技能摘要（可选）
        if self.skill_loader is not None:
            summary = self.skill_loader.get_summary_list()
            if summary.strip():
                parts.append(f"## 可用技能\n{summary}")
        # 8. TOOLS.md（L0）
        self._add_file(parts, self.workspace_dir / "TOOLS.md")
        # 9. 当前日期
        parts.append(f"当前日期：{today}")

        system_prompt = "\n\n---\n\n".join(p for p in parts if p.strip())
        return Context(
            system_prompt=system_prompt,
            token_estimate=len(system_prompt) // 4,
        )

    # TODO(memory-l3): L3 跨会话检索注入
    # 场景：继续上次任务、同类问题历史处理方式
    # 实现时需要 SessionManager.search_turns(query) 接口（关键词或语义搜索）
    async def _build_l3_context(self, task: str, session_id: str) -> str:  # noqa: ARG002
        return ""  # 未实现，预留接口

    @staticmethod
    def _add_file(parts: list[str], path: Path) -> None:
        """读取文件内容并加入 parts，不存在时静默跳过。"""
        if path.exists():
            content = path.read_text(encoding="utf-8")
            if content.strip():
                parts.append(content)
