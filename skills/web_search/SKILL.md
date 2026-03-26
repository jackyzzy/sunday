---
name: web_search
description: 网络搜索与信息提取，用于查询最新信息、调研话题、获取网页内容
version: "1.0"
requires: []
author: sunday
---

# 网络搜索技能

## 能力

- **web_search(query, max_results)**：使用搜索引擎搜索关键词，返回标题和摘要列表
- **fetch_url(url)**：抓取指定 URL 的页面内容并提取正文（去除 HTML 标签）

## 使用约定

- 搜索结果默认返回最多 5 条，可通过 max_results 参数调整
- fetch_url 对超过 4096 字符的页面自动截断
- 搜索前先思考：是否真的需要实时网络信息？已知信息优先使用，避免不必要的搜索
- 不要抓取需要登录才能访问的页面

## 典型用法

```
任务：了解最新的 Python 3.13 新特性
步骤：web_search("Python 3.13 新特性") → 选择权威来源 → fetch_url 获取详情
```
