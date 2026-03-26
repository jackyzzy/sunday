---
name: calendar
description: Google 日历管理，支持查看、创建和更新日程事件
version: "1.0"
requires: ["google-auth-oauthlib", "google-api-python-client"]
author: sunday
---

# 日历技能

## 能力

- **list_events(date_from, date_to, max_results)**：列出指定日期范围内的日历事件
- **create_event(title, start, end, description, location)**：创建新日历事件（需用户确认）
- **update_event(event_id, title, start, end, description)**：更新已有事件（需用户确认）

## 配置

使用与邮件技能相同的 Google OAuth2 凭证（`GOOGLE_CREDENTIALS_FILE` 或 `~/.sunday/credentials/`）。

所需 OAuth2 scope：`https://www.googleapis.com/auth/calendar`

## 使用约定

- 日期格式：`YYYY-MM-DD` 或 `YYYY-MM-DDTHH:MM:SS`（ISO 8601）
- 创建和更新事件是不可逆操作，执行前必须经用户确认
- 列出事件为只读操作，无需确认
- 默认使用主日历（primary）

## 典型用法

```
任务：查看明天的日程
步骤：list_events(date_from="2026-03-27", date_to="2026-03-27") → 展示事件列表

任务：安排下周一下午两点的会议
步骤：create_event(title="会议", start="2026-03-30T14:00:00", end="2026-03-30T15:00:00")
```
