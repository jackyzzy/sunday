"""T4-5 验证：内置技能包静态文件验证"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

SKILLS_ROOT = Path(__file__).parent.parent.parent / "skills"

BUILTIN_SKILLS = ["files", "web_search", "code"]


@pytest.mark.parametrize("skill", BUILTIN_SKILLS)
def test_skill_md_exists(skill):
    """skills/<name>/SKILL.md 存在"""
    assert (SKILLS_ROOT / skill / "SKILL.md").exists(), f"skills/{skill}/SKILL.md 不存在"


@pytest.mark.parametrize("skill", BUILTIN_SKILLS)
def test_skill_tools_py_exists(skill):
    """skills/<name>/tools.py 存在"""
    assert (SKILLS_ROOT / skill / "tools.py").exists(), f"skills/{skill}/tools.py 不存在"


@pytest.mark.parametrize("skill", BUILTIN_SKILLS)
def test_skill_frontmatter_valid(skill):
    """SKILL.md frontmatter 能被 yaml.safe_load 解析且包含 name/description"""
    path = SKILLS_ROOT / skill / "SKILL.md"
    content = path.read_text(encoding="utf-8")
    assert content.startswith("---"), f"skills/{skill}/SKILL.md 缺少 frontmatter"
    end = content.index("---", 3)
    fm = yaml.safe_load(content[3:end])
    assert "name" in fm
    assert "description" in fm
    assert fm["description"].strip()


@pytest.mark.parametrize("skill", BUILTIN_SKILLS)
def test_skill_content_nonempty(skill):
    """SKILL.md 正文内容非空"""
    path = SKILLS_ROOT / skill / "SKILL.md"
    content = path.read_text(encoding="utf-8")
    # 正文在 frontmatter 之后
    end = content.index("---", 3) + 3
    body = content[end:].strip()
    assert body, f"skills/{skill}/SKILL.md 正文为空"
