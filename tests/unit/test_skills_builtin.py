"""T4-5 + T6-1/T6-2 验证：内置技能包静态文件验证和功能测试"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

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


# ── T6-1 文件技能增强测试 ─────────────────────────────────────────────────────


async def test_files_skill_content_search(tmp_path):
    """content_search 返回包含关键词的文件和行号"""
    # 创建测试文件
    (tmp_path / "hello.py").write_text("def hello_world():\n    print('hello')\n")
    (tmp_path / "other.py").write_text("x = 42\n")

    from skills.files.tools import content_search

    result = await content_search("hello", str(tmp_path))
    assert "hello.py" in result
    assert "other.py" not in result


async def test_files_skill_content_search_case_insensitive(tmp_path):
    """content_search 默认大小写不敏感"""
    (tmp_path / "test.txt").write_text("Hello World\n")

    from skills.files.tools import content_search

    result = await content_search("HELLO", str(tmp_path), case_sensitive=False)
    assert "test.txt" in result


async def test_files_skill_content_search_no_match(tmp_path):
    """content_search 无匹配时返回友好提示"""
    (tmp_path / "file.txt").write_text("nothing here\n")

    from skills.files.tools import content_search

    result = await content_search("xyz_no_match_xyz", str(tmp_path))
    assert "未找到" in result


async def test_files_skill_batch_rename_dry_run(tmp_path):
    """batch_rename dry_run=True 时预览重命名但不执行"""
    (tmp_path / "foo_old.txt").write_text("")
    (tmp_path / "bar_old.txt").write_text("")

    from skills.files.tools import batch_rename

    result = await batch_rename(str(tmp_path), r"_old", "_new", dry_run=True)
    assert "预览" in result
    assert (tmp_path / "foo_old.txt").exists()  # 未实际重命名
    assert (tmp_path / "bar_old.txt").exists()


async def test_files_skill_batch_rename_execute(tmp_path):
    """batch_rename dry_run=False 时执行实际重命名"""
    (tmp_path / "foo_old.txt").write_text("")

    from skills.files.tools import batch_rename

    result = await batch_rename(str(tmp_path), r"_old", "_new", dry_run=False)
    assert (tmp_path / "foo_new.txt").exists()
    assert not (tmp_path / "foo_old.txt").exists()
    assert "已重命名" in result


async def test_files_skill_batch_rename_no_match(tmp_path):
    """batch_rename 无匹配时返回提示"""
    (tmp_path / "file.txt").write_text("")

    from skills.files.tools import batch_rename

    result = await batch_rename(str(tmp_path), r"xyz_no_match", "new", dry_run=True)
    assert "未找到" in result


# ── T6-2 网络搜索技能增强测试 ─────────────────────────────────────────────────


async def test_web_search_no_api_key_returns_error():
    """未配置 TAVILY_API_KEY 时返回友好错误"""
    import os

    with patch.dict(os.environ, {}, clear=True):
        os.environ.pop("TAVILY_API_KEY", None)
        from skills.web_search.tools import web_search

        result = await web_search("test query")
    assert "[错误]" in result
    assert "TAVILY_API_KEY" in result


async def test_web_search_calls_api():
    """web_search 调用 Tavily API 返回结果（mock httpx）"""
    import os
    from unittest.mock import MagicMock

    # httpx Response.json() 和 raise_for_status() 是同步方法
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "results": [
            {
                "title": "Python 官网",
                "url": "https://python.org",
                "content": "Python 是一门编程语言",
            }
        ]
    }
    mock_response.raise_for_status.return_value = None

    with patch.dict(os.environ, {"TAVILY_API_KEY": "fake-key"}):
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            from skills.web_search.tools import web_search

            result = await web_search("Python")

    assert "Python 官网" in result or "python.org" in result


async def test_fetch_url_returns_text():
    """fetch_url 抓取 URL 并提取正文（mock httpx）"""
    mock_response = AsyncMock()
    mock_response.text = "<html><body><p>Hello World</p></body></html>"
    mock_response.raise_for_status = AsyncMock()

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        from skills.web_search.tools import fetch_url

        result = await fetch_url("https://example.com")

    assert "Hello World" in result
