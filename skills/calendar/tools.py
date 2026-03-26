"""Google 日历技能工具"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_CREDENTIALS_FILE = Path("~/.sunday/credentials/gmail_credentials.json").expanduser()
_TOKEN_FILE = Path("~/.sunday/credentials/calendar_token.json").expanduser()
_SCOPES = ["https://www.googleapis.com/auth/calendar"]


def _get_credentials_path() -> Path:
    env_path = os.environ.get("GOOGLE_CREDENTIALS_FILE", "")
    if env_path:
        return Path(env_path)
    return _CREDENTIALS_FILE


def _build_calendar_service():
    """构建 Google Calendar API 服务对象。"""
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
            f"未找到 Google 凭证文件：{creds_path}。"
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

    return build("calendar", "v3", credentials=creds)


def _parse_datetime(dt_str: str) -> str:
    """将 YYYY-MM-DD 或 ISO 8601 字符串转为 RFC3339 格式。"""
    if "T" not in dt_str:
        return f"{dt_str}T00:00:00Z"
    if not dt_str.endswith("Z") and "+" not in dt_str:
        return dt_str + "Z"
    return dt_str


async def list_events(
    date_from: str = "",
    date_to: str = "",
    max_results: int = 10,
) -> str:
    """列出指定日期范围内的日历事件。

    date_from / date_to 格式：YYYY-MM-DD 或 ISO 8601。
    """
    try:
        service = _build_calendar_service()

        now = datetime.now(timezone.utc).isoformat()
        time_min = _parse_datetime(date_from) if date_from else now
        params: dict = {
            "calendarId": "primary",
            "timeMin": time_min,
            "maxResults": max_results,
            "singleEvents": True,
            "orderBy": "startTime",
        }
        if date_to:
            params["timeMax"] = _parse_datetime(date_to) + "T23:59:59Z" if "T" not in date_to else _parse_datetime(date_to)  # noqa: E501

        result = service.events().list(**params).execute()
        events = result.get("items", [])

        if not events:
            return "该时间范围内没有日历事件"

        lines = []
        for e in events:
            start = e.get("start", {}).get("dateTime", e.get("start", {}).get("date", ""))
            end = e.get("end", {}).get("dateTime", e.get("end", {}).get("date", ""))
            title = e.get("summary", "（无标题）")
            location = e.get("location", "")
            event_id = e.get("id", "")
            loc_str = f" @ {location}" if location else ""
            lines.append(f"[{event_id[:8]}] {start[:16]} - {end[:16]}  {title}{loc_str}")

        return "\n".join(lines)

    except RuntimeError as e:
        return f"[错误] {e}"
    except Exception as e:
        return f"[错误] 获取日历事件失败：{e}"


async def create_event(
    title: str,
    start: str,
    end: str,
    description: str = "",
    location: str = "",
) -> str:
    """创建新的日历事件（不可逆操作，需用户确认）。

    start / end 格式：YYYY-MM-DDTHH:MM:SS
    """
    try:
        service = _build_calendar_service()

        event = {
            "summary": title,
            "start": {"dateTime": _parse_datetime(start), "timeZone": "Asia/Shanghai"},
            "end": {"dateTime": _parse_datetime(end), "timeZone": "Asia/Shanghai"},
        }
        if description:
            event["description"] = description
        if location:
            event["location"] = location

        created = service.events().insert(calendarId="primary", body=event).execute()
        event_id = created.get("id", "")
        return f"日历事件已创建：{title}（ID: {event_id[:8]}）\n时间：{start} ~ {end}"

    except RuntimeError as e:
        return f"[错误] {e}"
    except Exception as e:
        return f"[错误] 创建日历事件失败：{e}"


async def update_event(
    event_id: str,
    title: str = "",
    start: str = "",
    end: str = "",
    description: str = "",
) -> str:
    """更新已有日历事件（不可逆操作，需用户确认）。"""
    try:
        service = _build_calendar_service()

        existing = service.events().get(calendarId="primary", eventId=event_id).execute()

        if title:
            existing["summary"] = title
        if start:
            existing["start"] = {"dateTime": _parse_datetime(start), "timeZone": "Asia/Shanghai"}
        if end:
            existing["end"] = {"dateTime": _parse_datetime(end), "timeZone": "Asia/Shanghai"}
        if description:
            existing["description"] = description

        service.events().update(calendarId="primary", eventId=event_id, body=existing).execute()
        return f"日历事件已更新（ID: {event_id[:8]}）"

    except RuntimeError as e:
        return f"[错误] {e}"
    except Exception as e:
        return f"[错误] 更新日历事件失败：{e}"
