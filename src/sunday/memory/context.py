"""Phase 3：ContextBuilder — L0 上下文组装"""
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
    """按 L0 顺序组装系统提示。

    组装顺序（design.md §5.2）：
    1. SOUL.md
    2. AGENTS.md
    3. MEMORY.md（末尾 l0_max_lines 行）
    4. USER.md
    5. 昨日日志
    6. 今日日志
    7. 技能摘要（可选）
    8. TOOLS.md
    9. 当前日期

    各节以 "---" 分隔；文件不存在时静默跳过。
    """

    def __init__(
        self,
        workspace_dir: Path,
        skill_loader: SkillLoaderProtocol | None = None,
        config: "SundayConfig | None" = None,
        l0_max_lines: int = 100,
    ) -> None:
        self.workspace_dir = workspace_dir
        self.memory_dir = workspace_dir / "memory"
        self.skill_loader = skill_loader
        self.l0_max_lines = config.memory.l0_max_lines if config else l0_max_lines

    def build(self, session_id: str = "") -> Context:
        """组装 L0 系统提示，返回 Context。"""
        parts: list[str] = []

        # 1. SOUL.md
        self._add_file(parts, self.workspace_dir / "SOUL.md")

        # 2. AGENTS.md
        self._add_file(parts, self.workspace_dir / "AGENTS.md")

        # 3. MEMORY.md（末尾 l0_max_lines 行）
        memory_path = self.workspace_dir / "MEMORY.md"
        if memory_path.exists():
            content = memory_path.read_text(encoding="utf-8")
            lines = content.splitlines()
            if len(lines) > self.l0_max_lines:
                content = "\n".join(lines[-self.l0_max_lines:])
            if content.strip():
                parts.append(content)

        # 4. USER.md
        self._add_file(parts, self.workspace_dir / "USER.md")

        # 5. 昨日日志
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        self._add_file(parts, self.memory_dir / f"{yesterday}.md")

        # 6. 今日日志
        today = date.today().isoformat()
        self._add_file(parts, self.memory_dir / f"{today}.md")

        # 7. 技能摘要（可选）
        if self.skill_loader is not None:
            summary = self.skill_loader.get_summary_list()
            if summary.strip():
                parts.append(f"## 可用技能\n{summary}")

        # 8. TOOLS.md
        self._add_file(parts, self.workspace_dir / "TOOLS.md")

        # 9. 当前日期
        parts.append(f"当前日期：{today}")

        system_prompt = "\n\n---\n\n".join(p for p in parts if p.strip())
        return Context(
            system_prompt=system_prompt,
            token_estimate=len(system_prompt) // 4,
        )

    @staticmethod
    def _add_file(parts: list[str], path: Path) -> None:
        """读取文件内容并加入 parts，不存在时静默跳过。"""
        if path.exists():
            content = path.read_text(encoding="utf-8")
            if content.strip():
                parts.append(content)
