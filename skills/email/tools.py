"""Gmail 邮件技能工具"""
from __future__ import annotations

import base64
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_CREDENTIALS_FILE = Path("~/.sunday/credentials/gmail_credentials.json").expanduser()
_TOKEN_FILE = Path("~/.sunday/credentials/gmail_token.json").expanduser()
_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]


def _get_credentials_path() -> Path:
    env_path = os.environ.get("GOOGLE_CREDENTIALS_FILE", "")
    if env_path:
        return Path(env_path)
    return _CREDENTIALS_FILE


def _build_gmail_service():
    """构建 Gmail API 服务对象。未配置凭证时抛出 RuntimeError。"""
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as e:
        raise RuntimeError(
            f"缺少 Google API 依赖：{e}。"
            "请运行：uv add google-auth-oauthlib google-api-python-client"
        ) from e

    creds_path = _get_credentials_path()
    if not creds_path.exists():
        raise RuntimeError(
            f"未找到 Gmail 凭证文件：{creds_path}。"
            "请按照 SKILL.md 中的配置说明完成 OAuth2 设置。"
        )

    creds = None
    if _TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(_TOKEN_FILE), _SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), _SCOPES)
            creds = flow.run_local_server(port=0)
        _TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        _TOKEN_FILE.write_text(creds.to_json())

    return build("gmail", "v1", credentials=creds)


async def list_emails(max_results: int = 10, query: str = "") -> str:
    """列出 Gmail 收件箱邮件。

    query 支持 Gmail 搜索语法，如 'is:unread newer_than:1d'。
    """
    try:
        service = _build_gmail_service()
        params: dict = {"userId": "me", "maxResults": max_results}
        if query:
            params["q"] = query

        result = service.users().messages().list(**params).execute()
        messages = result.get("messages", [])
        if not messages:
            return "收件箱为空（或无匹配邮件）"

        lines = []
        for msg in messages[:max_results]:
            detail = (
                service.users()
                .messages()
                .get(userId="me", id=msg["id"], format="metadata",
                     metadataHeaders=["Subject", "From", "Date"])
                .execute()
            )
            headers = {h["name"]: h["value"] for h in detail.get("payload", {}).get("headers", [])}
            subject = headers.get("Subject", "（无主题）")
            sender = headers.get("From", "（未知发件人）")
            date = headers.get("Date", "")
            lines.append(f"[{msg['id']}] {date[:16]}  {sender[:30]}  {subject}")

        return "\n".join(lines)

    except RuntimeError as e:
        return f"[错误] {e}"
    except Exception as e:
        return f"[错误] 获取邮件列表失败：{e}"


async def read_email(message_id: str) -> str:
    """读取指定邮件的完整内容。"""
    try:
        service = _build_gmail_service()
        msg = service.users().messages().get(userId="me", id=message_id, format="full").execute()

        payload = msg.get("payload", {})
        headers = {h["name"]: h["value"] for h in payload.get("headers", [])}
        subject = headers.get("Subject", "（无主题）")
        sender = headers.get("From", "（未知）")
        date = headers.get("Date", "")

        body = _extract_body(payload)
        if len(body) > 4096:
            body = body[:4096] + "\n\n[正文已截断]"

        return f"主题：{subject}\n发件人：{sender}\n时间：{date}\n\n{body}"

    except RuntimeError as e:
        return f"[错误] {e}"
    except Exception as e:
        return f"[错误] 读取邮件失败：{e}"


async def send_email(to: str, subject: str, body: str) -> str:
    """发送新邮件（不可逆操作，需用户确认）。"""
    try:
        import email.mime.text

        service = _build_gmail_service()
        mime_msg = email.mime.text.MIMEText(body)
        mime_msg["to"] = to
        mime_msg["subject"] = subject
        raw = base64.urlsafe_b64encode(mime_msg.as_bytes()).decode()
        service.users().messages().send(userId="me", body={"raw": raw}).execute()
        return f"邮件已发送至：{to}，主题：{subject}"

    except RuntimeError as e:
        return f"[错误] {e}"
    except Exception as e:
        return f"[错误] 发送邮件失败：{e}"


async def reply_email(message_id: str, body: str) -> str:
    """回复指定邮件（不可逆操作，需用户确认）。"""
    try:
        import email.mime.text

        service = _build_gmail_service()
        original = service.users().messages().get(
            userId="me", id=message_id, format="metadata",
            metadataHeaders=["Subject", "From", "Message-ID", "References"]
        ).execute()

        headers = {
            h["name"]: h["value"]
            for h in original.get("payload", {}).get("headers", [])
        }
        to = headers.get("From", "")
        subject = headers.get("Subject", "")
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"
        thread_id = original.get("threadId", "")

        mime_msg = email.mime.text.MIMEText(body)
        mime_msg["to"] = to
        mime_msg["subject"] = subject
        if "Message-ID" in headers:
            mime_msg["In-Reply-To"] = headers["Message-ID"]
            mime_msg["References"] = headers.get("References", "") + " " + headers["Message-ID"]

        raw = base64.urlsafe_b64encode(mime_msg.as_bytes()).decode()
        service.users().messages().send(
            userId="me", body={"raw": raw, "threadId": thread_id}
        ).execute()
        return f"已回复邮件 [{message_id}]，收件人：{to}"

    except RuntimeError as e:
        return f"[错误] {e}"
    except Exception as e:
        return f"[错误] 回复邮件失败：{e}"


def _extract_body(payload: dict) -> str:
    """从 Gmail payload 中递归提取纯文本正文。"""
    mime_type = payload.get("mimeType", "")
    body_data = payload.get("body", {}).get("data", "")

    if mime_type == "text/plain" and body_data:
        return base64.urlsafe_b64decode(body_data).decode(errors="replace")

    if mime_type == "text/html" and body_data:
        import re
        html = base64.urlsafe_b64decode(body_data).decode(errors="replace")
        text = re.sub(r"<[^>]+>", " ", html)
        return re.sub(r"\s+", " ", text).strip()

    for part in payload.get("parts", []):
        result = _extract_body(part)
        if result:
            return result

    return ""
