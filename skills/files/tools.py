"""文件操作技能工具 — Phase 6 增强版"""
from __future__ import annotations

import re
from pathlib import Path

from sunday.tools.cli_tool import list_dir, read_file, search_files, write_file

__all__ = [
    "read_file",
    "write_file",
    "list_dir",
    "search_files",
    "content_search",
    "batch_rename",
]


async def content_search(keyword: str, directory: str = ".", case_sensitive: bool = False) -> str:
    """在目录中搜索包含关键词的文件，返回文件路径和匹配行。"""
    try:
        root = Path(directory)
        if not root.exists():
            return f"[错误] 目录不存在：{directory}"

        flags = 0 if case_sensitive else re.IGNORECASE
        pattern = re.compile(re.escape(keyword), flags)
        results = []

        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

            matches = []
            for i, line in enumerate(text.splitlines(), 1):
                if pattern.search(line):
                    matches.append(f"  L{i}: {line.strip()[:120]}")
                    if len(matches) >= 5:
                        break

            if matches:
                results.append(f"{path}:\n" + "\n".join(matches))
                if len(results) >= 20:
                    results.append("（结果过多，已截断）")
                    break

        return "\n\n".join(results) if results else f"未找到包含 '{keyword}' 的文件"
    except Exception as e:
        return f"[错误] 内容搜索失败：{e}"


async def batch_rename(
    directory: str,
    pattern: str,
    replacement: str,
    dry_run: bool = True,
) -> str:
    """批量重命名目录中匹配 pattern 的文件名（正则替换）。

    dry_run=True 时仅预览不执行；实际重命名时 is_dangerous 确认由 ToolRegistry 负责。
    """
    try:
        root = Path(directory)
        if not root.exists():
            return f"[错误] 目录不存在：{directory}"

        regex = re.compile(pattern)
        renames = []
        for path in sorted(root.iterdir()):
            if not path.is_file():
                continue
            new_name = regex.sub(replacement, path.name)
            if new_name != path.name:
                renames.append((path, path.parent / new_name))

        if not renames:
            return f"未找到匹配 pattern '{pattern}' 的文件"

        preview = "\n".join(f"  {src.name} → {dst.name}" for src, dst in renames)

        if dry_run:
            return (
                f"[预览] 将重命名 {len(renames)} 个文件：\n{preview}\n"
                "（传入 dry_run=False 执行实际重命名）"
            )

        renamed = []
        for src, dst in renames:
            if dst.exists():
                renamed.append(f"  跳过（目标已存在）：{dst.name}")
                continue
            src.rename(dst)
            renamed.append(f"  {src.name} → {dst.name}")

        return f"已重命名 {len(renamed)} 个文件：\n" + "\n".join(renamed)
    except re.error as e:
        return f"[错误] 正则表达式无效：{e}"
    except Exception as e:
        return f"[错误] 批量重命名失败：{e}"
