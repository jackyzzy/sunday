---
name: code
description: 代码阅读、编写与执行辅助，支持多语言代码分析和 Python 脚本运行
version: "1.0"
requires: []
author: sunday
---

# 代码辅助技能

## 能力

- **run_python(code)**：在安全环境中执行 Python 代码片段，返回输出
- **explain_code(path)**：读取代码文件并生成解释说明
- **search_code(directory, pattern)**：在代码库中搜索指定模式（正则或关键词）

## 使用约定

- run_python 只执行纯计算代码，不得访问网络或写入系统文件
- 代码执行有 10 秒超时限制
- 操作真实代码文件时使用 files 技能中的 read_file / write_file
- 对于复杂修改，先 read_file 理解现有代码，再生成新内容

## 典型用法

```
任务：分析当前项目的依赖关系
步骤：read_file("pyproject.toml") → 解析依赖 → 生成分析报告
```
