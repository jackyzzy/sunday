---
name: files
description: 本地文件与目录操作，包括读写、列目录、搜索文件内容
version: "1.0"
requires: []
author: sunday
---

# 文件操作技能

## 能力

- **read_file(path)**：读取本地文件的文本内容
- **write_file(path, content)**：写入或覆盖本地文件（需用户确认）
- **list_dir(path)**：列出目录下的文件和子目录
- **search_files(directory, pattern)**：在目录树中按 glob 模式搜索文件

## 使用约定

- 读取操作无需确认，写入操作需要用户确认
- 路径使用绝对路径，避免歧义
- 大文件（> 4096 字符）输出会被自动截断，如需完整内容请分段读取
- 不要读取 `.env`、`*.key`、`*.pem` 等敏感文件

## 典型用法

```
任务：查看当前项目的 Python 文件
步骤：search_files(".", "*.py") → 列出所有 .py 文件 → 按需 read_file 查看内容
```
