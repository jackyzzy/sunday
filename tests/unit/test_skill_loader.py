"""T4-4 验证：SkillLoader 单元测试（真实文件系统）"""
from __future__ import annotations

from pathlib import Path

import pytest

from sunday.skills.loader import SkillLoader


def _write_skill(skills_dir: Path, name: str, description: str, requires: list[str] | None = None):
    skill_dir = skills_dir / name
    skill_dir.mkdir(parents=True)
    requires_str = str(requires or [])
    fm = f"---\nname: {name}\ndescription: {description}\nrequires: {requires_str}\n---\n"
    (skill_dir / "SKILL.md").write_text(fm + f"\n# {name}\n内容。", encoding="utf-8")


# ── 发现 ──────────────────────────────────────────────────────────────────────

def test_discover_finds_skills(tmp_path):
    """./skills/ 下存在 SKILL.md 时被发现"""
    _write_skill(tmp_path / "skills", "web_search", "网络搜索")
    loader = SkillLoader(project_skills_dir=tmp_path / "skills")
    skills = loader.discover()
    assert any(s.name == "web_search" for s in skills)


def test_discover_user_skills_higher_priority(tmp_path):
    """workspace/skills/ 下的同名技能优先于 ./skills/"""
    _write_skill(tmp_path / "skills", "files", "项目版本")
    _write_skill(tmp_path / "workspace" / "skills", "files", "用户版本")
    loader = SkillLoader(
        project_skills_dir=tmp_path / "skills",
        user_skills_dir=tmp_path / "workspace" / "skills",
    )
    skills = loader.discover()
    files_skill = next(s for s in skills if s.name == "files")
    assert files_skill.description == "用户版本"


def test_discover_invalid_frontmatter_skipped(tmp_path):
    """没有 frontmatter 的文件被跳过"""
    skill_dir = tmp_path / "skills" / "bad_skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# 没有 frontmatter\n内容")
    loader = SkillLoader(project_skills_dir=tmp_path / "skills")
    skills = loader.discover()
    assert not any(s.name == "bad_skill" for s in skills)


def test_discover_empty_dir_returns_empty(tmp_path):
    """空目录返回空列表"""
    loader = SkillLoader(project_skills_dir=tmp_path / "no_such_dir")
    assert loader.discover() == []


# ── 摘要 ──────────────────────────────────────────────────────────────────────

def test_get_summary_list(tmp_path):
    """返回格式为 '- name: description' 的摘要"""
    _write_skill(tmp_path / "skills", "web_search", "网络搜索工具")
    loader = SkillLoader(project_skills_dir=tmp_path / "skills")
    loader.discover()
    summary = loader.get_summary_list()
    assert "web_search" in summary
    assert "网络搜索工具" in summary


# ── 懒加载 ────────────────────────────────────────────────────────────────────

def test_load_full_returns_content(tmp_path):
    """懒加载返回完整 SKILL.md 内容"""
    _write_skill(tmp_path / "skills", "code", "代码辅助")
    loader = SkillLoader(project_skills_dir=tmp_path / "skills")
    loader.discover()
    content = loader.load_full("code")
    assert "代码辅助" in content or "code" in content


def test_load_full_cached(tmp_path):
    """同一名称不重复读文件（通过 path.read_text 调用次数验证）"""
    _write_skill(tmp_path / "skills", "cached_skill", "缓存测试")
    loader = SkillLoader(project_skills_dir=tmp_path / "skills")
    loader.discover()

    read_calls = 0
    original_read = Path.read_text

    def counting_read(self, *args, **kwargs):
        nonlocal read_calls
        read_calls += 1
        return original_read(self, *args, **kwargs)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(Path, "read_text", counting_read)
        loader.load_full("cached_skill")
        loader.load_full("cached_skill")

    assert read_calls == 1  # 第二次从缓存读，不再调用 read_text


def test_load_full_unknown_returns_error(tmp_path):
    """未知技能返回错误字符串"""
    loader = SkillLoader(project_skills_dir=tmp_path / "skills")
    loader.discover()
    result = loader.load_full("no_such_skill")
    assert "unknown" in result.lower() or "未知" in result or "not found" in result.lower()


# ── requires 过滤 ─────────────────────────────────────────────────────────────

def test_requires_unsatisfied_skipped(tmp_path):
    """requires 不满足时技能被跳过"""
    _write_skill(tmp_path / "skills", "gmail", "邮件工具", requires=["gmail_mcp"])
    loader = SkillLoader(project_skills_dir=tmp_path / "skills", available_tools=set())
    skills = loader.discover()
    assert not any(s.name == "gmail" for s in skills)


def test_requires_satisfied_included(tmp_path):
    """requires 满足时技能被包含"""
    _write_skill(tmp_path / "skills", "gmail", "邮件工具", requires=["gmail_mcp"])
    loader = SkillLoader(
        project_skills_dir=tmp_path / "skills",
        available_tools={"gmail_mcp"},
    )
    skills = loader.discover()
    assert any(s.name == "gmail" for s in skills)
