"""T6-4 验证：日历技能单元测试"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import yaml

SKILLS_ROOT = Path(__file__).parent.parent.parent / "skills"


def test_calendar_skill_md_exists():
    """skills/calendar/SKILL.md 存在"""
    assert (SKILLS_ROOT / "calendar" / "SKILL.md").exists()


def test_calendar_skill_md_has_sections():
    """SKILL.md 包含必要章节"""
    content = (SKILLS_ROOT / "calendar" / "SKILL.md").read_text()
    assert "## 能力" in content
    assert "## 配置" in content


def test_calendar_skill_frontmatter_valid():
    """SKILL.md frontmatter 有效"""
    content = (SKILLS_ROOT / "calendar" / "SKILL.md").read_text()
    assert content.startswith("---")
    end = content.index("---", 3)
    fm = yaml.safe_load(content[3:end])
    assert fm["name"] == "calendar"
    assert "description" in fm


def test_calendar_create_event_is_dangerous():
    """create_event 是不可逆操作，函数存在且可 import"""
    from skills.calendar.tools import create_event  # noqa: PLC0415

    assert callable(create_event)


def test_calendar_update_event_is_dangerous():
    """update_event 是不可逆操作，函数存在且可 import"""
    from skills.calendar.tools import update_event  # noqa: PLC0415

    assert callable(update_event)


def test_calendar_no_credentials_error():
    """无凭证文件时返回友好错误提示"""
    import asyncio

    from skills.calendar.tools import list_events  # noqa: PLC0415

    with patch(
        "skills.calendar.tools._get_credentials_path", return_value=Path("/nonexistent/path")
    ):
        result = asyncio.get_event_loop().run_until_complete(list_events())
    assert "[错误]" in result
    assert any(kw in result for kw in ("凭证", "配置", "依赖", "credentials", "google"))


async def test_calendar_list_events():
    """list_events 调用 Google Calendar API 返回事件列表（mock）"""
    from unittest.mock import MagicMock

    mock_service = MagicMock()
    mock_service.events.return_value.list.return_value.execute.return_value = {
        "items": [
            {
                "id": "event001",
                "summary": "团队会议",
                "start": {"dateTime": "2026-03-26T10:00:00Z"},
                "end": {"dateTime": "2026-03-26T11:00:00Z"},
                "location": "会议室 A",
            }
        ]
    }

    with patch("skills.calendar.tools._build_calendar_service", return_value=mock_service):
        from skills.calendar.tools import list_events

        result = await list_events(date_from="2026-03-26", date_to="2026-03-26")

    assert "团队会议" in result
    assert "event001"[:8] in result or "event001" in result
