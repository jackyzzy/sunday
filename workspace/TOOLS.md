# 工具使用约定

## 优先使用专用工具，而非 Shell 命令

以下操作有专用工具，**不要用 run_shell 替代**：

| 需求 | 使用工具 | 不要用 |
|------|---------|--------|
| 读取文件内容 | `read_file(path)` | `run_shell("cat ...")` |
| 写入文件 | `write_file(path, content)` | `run_shell("echo ... > ...")` |
| 列出目录 | `list_dir(path)` | `run_shell("ls ...")` |
| 搜索文件名 | `search_files(dir, pattern)` | `run_shell("find ...")` |
| 搜索文件内容 | `content_search(keyword, directory)` | `run_shell("grep -r ...")` |
| 批量重命名 | `batch_rename(dir, pattern, replacement)` | `run_shell("rename ...")` |
| 网络搜索 | `web_search(query)` | `run_shell("curl ...")` |
| 抓取网页 | `fetch_url(url)` | `run_shell("curl ...")` |
| 执行 Python | `run_python(code)` | `run_shell("python3 -c ...")` |

## 工具说明

### 文件操作
- `content_search(keyword, directory=".", case_sensitive=false)` — 递归搜索包含关键词的文件，返回路径和匹配行
- `batch_rename(directory, pattern, replacement, dry_run=true)` — 正则批量重命名，**先用 dry_run=true 预览**，确认后再 dry_run=false 执行（需用户确认）

### 网络
- `web_search(query, max_results=5)` — Tavily 搜索，需配置 `TAVILY_API_KEY`
- `fetch_url(url)` — 抓取网页纯文本，最多返回 4096 字符

### 代码执行
- `run_python(code)` — 在子进程执行 Python 代码片段，10 秒超时（需用户确认）

---

## write_file 路径说明

- 相对路径（如 `report.md`）→ 自动存入当前任务的报告目录
- 绝对路径（如 `/tmp/out.txt`）→ 写入指定位置
- 生成报告、文档、分析结果时，只需提供文件名即可，系统自动归档

## run_shell 使用原则

只在以下情况使用 `run_shell`：
- 执行需要具体命令行工具的操作（如 `git`、`curl`、`python`、`npm` 等）
- 执行管道组合（如 `grep ... | sort | uniq`）
- 专用工具无法满足的场景

**禁止**：`rm -rf`、`dd if=` 等破坏性命令。

## 网络与外部服务

- 不向未知来源发送敏感信息
- 使用网络工具前确认 URL 来源可信
