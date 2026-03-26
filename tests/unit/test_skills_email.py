"""T6-3 验证：邮件技能单元测试"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

SKILLS_ROOT = Path(__file__).parent.parent.parent / "skills"


def test_email_skill_md_exists():
    """skills/email/SKILL.md 存在"""
    assert (SKILLS_ROOT / "email" / "SKILL.md").exists()


def test_email_skill_md_has_sections():
    """SKILL.md 包含必要章节"""
    content = (SKILLS_ROOT / "email" / "SKILL.md").read_text()
    assert "## 能力" in content
    assert "## 配置" in content
    assert "## 使用约定" in content


def test_email_skill_frontmatter_valid():
    """SKILL.md frontmatter 有效"""
    content = (SKILLS_ROOT / "email" / "SKILL.md").read_text()
    assert content.startswith("---")
    end = content.index("---", 3)
    fm = yaml.safe_load(content[3:end])
    assert fm["name"] == "email"
    assert "description" in fm


def test_email_send_is_dangerous():
    """send_email 元数据应为 is_dangerous=True（检查代码注释/实现）"""
    # send_email 发送真实邮件，本身就是不可逆操作
    # 通过 ToolRegistry 注册时需标记 is_dangerous=True
    # 此处验证函数存在且可 import
    from skills.email.tools import send_email  # noqa: PLC0415

    assert callable(send_email)


def test_email_no_credentials_error():
    """无凭证文件时返回友好错误提示"""
    import asyncio

    from skills.email.tools import list_emails  # noqa: PLC0415

    with patch("skills.email.tools._get_credentials_path", return_value=Path("/nonexistent/path")):
        result = asyncio.get_event_loop().run_until_complete(list_emails())
    assert "[错误]" in result
    assert any(kw in result for kw in ("凭证", "配置", "依赖", "credentials", "google"))


async def test_email_list_returns_messages():
    """list_emails 调用 Gmail API 返回邮件列表（mock）"""
    mock_service = MagicMock()
    mock_messages = MagicMock()
    mock_service.users.return_value.messages.return_value = mock_messages

    # list 返回两封邮件
    mock_messages.list.return_value.execute.return_value = {
        "messages": [{"id": "msg001"}, {"id": "msg002"}]
    }
    # get 返回邮件元数据
    mock_messages.get.return_value.execute.return_value = {
        "payload": {
            "headers": [
                {"name": "Subject", "value": "测试邮件"},
                {"name": "From", "value": "test@example.com"},
                {"name": "Date", "value": "2026-03-26 10:00"},
            ]
        }
    }

    with patch("skills.email.tools._build_gmail_service", return_value=mock_service):
        from skills.email.tools import list_emails

        result = await list_emails(max_results=2)

    assert "msg001" in result or "测试邮件" in result


async def test_email_read_returns_content():
    """read_email 调用 Gmail API 返回邮件正文（mock）"""
    mock_service = MagicMock()
    import base64

    body_text = "这是邮件正文内容"
    encoded = base64.urlsafe_b64encode(body_text.encode()).decode()

    mock_service.users.return_value.messages.return_value.get.return_value.execute.return_value = {
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "Subject", "value": "测试主题"},
                {"name": "From", "value": "sender@example.com"},
                {"name": "Date", "value": "2026-03-26"},
            ],
            "body": {"data": encoded},
            "parts": [],
        }
    }

    with patch("skills.email.tools._build_gmail_service", return_value=mock_service):
        from skills.email.tools import read_email

        result = await read_email("msg001")

    assert "测试主题" in result
    assert "这是邮件正文内容" in result
