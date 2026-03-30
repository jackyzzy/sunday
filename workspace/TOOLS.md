# 工具使用约定

## 优先使用专用工具，而非 Shell 命令

以下操作有专用工具，**不要用 run_shell 替代**：

| 需求 | 使用工具 | 不要用 |
|------|---------|--------|
| 读取文件内容 | `read_file(path)` | `run_shell("cat ...")` |
| 写入文件 | `write_file(path, content)` | `run_shell("echo ... > ...")` |
| 列出目录 | `list_dir(path)` | `run_shell("ls ...")` |
| 搜索文件 | `search_files(dir, pattern)` | `run_shell("find ...")` |

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
