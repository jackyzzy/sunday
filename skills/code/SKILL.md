---
name: code
description: 代码阅读与分析辅助，提供代码任务的工具组合约定
version: "1.1"
requires: []
author: sunday
---

# 代码辅助技能

## 定位

本技能不提供独立工具，而是约定如何组合内置 CLI 工具完成代码相关任务。
代码执行能力（`run_python`、`run_shell`）是通用 CLI 工具，不属于本技能。

## 工具组合约定

| 任务 | 使用工具 |
|---|---|
| 读取源码文件 | `read_file(path)` |
| 在代码库中搜索关键词/模式 | `search_files(directory, pattern)` |
| 修改代码文件 | `read_file` 理解现有内容 → `write_file` 写入新内容 |
| 数据计算、格式转换 | `run_python(code)` |
| 调用命令行工具（如 git、pytest） | `run_shell(command)` |

## 使用约定

- 修改代码文件时，先 `read_file` 理解现有结构，再生成完整内容用 `write_file` 写入
- 不得对代码文件做部分字符串替换，始终以完整文件内容写入
- `run_python` 输出文件请写入 `os.environ['SUNDAY_REPORT_DIR']`，不得写入项目源码目录

## 典型用法

```
任务：分析当前项目的依赖关系
步骤：read_file("pyproject.toml") → run_python 解析依赖树 → write_file 生成报告
```
