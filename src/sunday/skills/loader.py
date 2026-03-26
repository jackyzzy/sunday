"""Phase 4：SkillLoader — 技能包发现与懒加载"""
from __future__ import annotations

import logging
from pathlib import Path

import yaml
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class SkillMeta(BaseModel):
    """技能元数据（从 SKILL.md frontmatter 解析）。"""

    name: str
    description: str
    path: Path
    requires: list[str] = []

    model_config = {"arbitrary_types_allowed": True}


class SkillLoader:
    """技能包发现与懒加载器。

    发现路径优先级（高 → 低）：
    1. user_skills_dir（workspace/skills/）
    2. project_skills_dir（./skills/）

    同名技能用户版本覆盖项目版本。
    """

    def __init__(
        self,
        project_skills_dir: Path | None = None,
        user_skills_dir: Path | None = None,
        available_tools: set[str] | None = None,
    ) -> None:
        self._project_dir = project_skills_dir
        self._user_dir = user_skills_dir
        self._available_tools = available_tools  # None = 不过滤 requires
        self._skills: dict[str, SkillMeta] = {}   # name → meta
        self._cache: dict[str, str] = {}           # name → full content

    def discover(self) -> list[SkillMeta]:
        """扫描技能目录，解析 frontmatter，返回可用技能列表。

        先扫描 project_skills_dir（低优先级），再用 user_skills_dir 覆盖（高优先级）。
        """
        self._skills.clear()

        # 低优先级先加载
        for skills_dir in [self._project_dir, self._user_dir]:
            if skills_dir is None or not skills_dir.exists():
                continue
            for skill_dir in sorted(skills_dir.iterdir()):
                skill_md = skill_dir / "SKILL.md"
                if not skill_md.is_file():
                    continue
                meta = self._parse_skill(skill_md)
                if meta is None:
                    continue
                # requires 检查
                if self._available_tools is not None and meta.requires:
                    if not all(r in self._available_tools for r in meta.requires):
                        logger.debug("技能 %s 跳过（requires 不满足）", meta.name)
                        continue
                self._skills[meta.name] = meta

        logger.info("发现 %d 个技能", len(self._skills))
        return list(self._skills.values())

    def get_summary_list(self) -> str:
        """返回所有已发现技能的摘要（名称 + 描述），用于 L0 注入。"""
        if not self._skills:
            return ""
        lines = [f"- {meta.name}: {meta.description}" for meta in self._skills.values()]
        return "\n".join(lines)

    def load_full(self, name: str) -> str:
        """懒加载技能完整 SKILL.md 内容（带缓存）。"""
        if name not in self._skills:
            return f"[技能错误] 未知技能：{name}"
        if name in self._cache:
            return self._cache[name]
        content = self._skills[name].path.read_text(encoding="utf-8")
        self._cache[name] = content
        return content

    @staticmethod
    def _parse_skill(skill_md: Path) -> SkillMeta | None:
        """解析 SKILL.md frontmatter，返回 SkillMeta，失败返回 None。"""
        try:
            content = skill_md.read_text(encoding="utf-8")
            if not content.startswith("---"):
                return None
            end = content.index("---", 3)
            fm = yaml.safe_load(content[3:end])
            if not isinstance(fm, dict) or "name" not in fm:
                return None
            return SkillMeta(
                name=fm["name"],
                description=fm.get("description", ""),
                path=skill_md,
                requires=fm.get("requires", []) or [],
            )
        except Exception as e:
            logger.debug("解析 %s 失败：%s", skill_md, e)
            return None
