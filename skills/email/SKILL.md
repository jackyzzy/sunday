---
name: email
description: Gmail 邮件管理，支持列出、阅读、发送和回复邮件
version: "1.0"
requires: ["google-auth-oauthlib", "google-api-python-client"]
author: sunday
---

# 邮件技能

## 能力

- **list_emails(max_results, query)**：列出收件箱邮件，支持 Gmail 搜索语法
- **read_email(message_id)**：读取指定邮件的完整内容（主题、发件人、正文）
- **send_email(to, subject, body)**：发送新邮件（需用户确认）
- **reply_email(message_id, body)**：回复指定邮件（需用户确认）

## 配置

需要 Google OAuth2 凭证文件，路径通过以下方式指定（优先级从高到低）：

1. 环境变量 `GOOGLE_CREDENTIALS_FILE`
2. `~/.sunday/credentials/gmail_credentials.json`

首次使用会触发浏览器 OAuth2 授权流程，token 保存至 `~/.sunday/credentials/gmail_token.json`。

## 使用约定

- 发送和回复邮件是不可逆操作，执行前必须经用户确认
- 列出和阅读邮件为只读操作，无需确认
- 邮件正文超过 4096 字符时自动截断

## 典型用法

```
任务：汇总今天未读邮件
步骤：list_emails(query="is:unread newer_than:1d") → 遍历 → read_email 获取正文 → 汇总
```
