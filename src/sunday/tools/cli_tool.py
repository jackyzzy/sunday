"""Phase 4：CLI 工具 + 内置文件工具"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from sunday.tools.registry import ToolMeta

if TYPE_CHECKING:
    from sunday.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


async def run_shell(command: str, timeout: int = 30) -> str:
    """执行 shell 命令，返回 stdout + stderr。"""
    try:
        proc = await asyncio.wait_for(
            asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            ),
            timeout=timeout,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return f"[超时] 命令 '{command}' 超过 {timeout}s 未完成"
    except asyncio.TimeoutError:
        return f"[超时] 命令 '{command}' 超过 {timeout}s 未完成"
    except Exception as e:
        return f"[错误] 命令执行失败：{e}"

    out = stdout.decode(errors="replace").strip()
    err = stderr.decode(errors="replace").strip()
    rc = proc.returncode

    parts = []
    if out:
        parts.append(out)
    if err:
        parts.append(f"[stderr] {err}")
    if rc != 0:
        parts.append(f"[returncode={rc}]")
    return "\n".join(parts) if parts else ""


async def read_file(path: str) -> str:
    """读取文件内容。"""
    try:
        return Path(path).read_text(encoding="utf-8")
    except Exception as e:
        return f"[错误] 读取文件失败：{e}"


async def write_file(path: str, content: str) -> str:
    """写入文件内容（覆盖）。"""
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"已写入：{path}"
    except Exception as e:
        return f"[错误] 写入文件失败：{e}"


async def list_dir(path: str) -> str:
    """列出目录内容。"""
    try:
        entries = sorted(Path(path).iterdir(), key=lambda p: (p.is_file(), p.name))
        lines = []
        for entry in entries:
            prefix = "📄" if entry.is_file() else "📁"
            lines.append(f"{prefix} {entry.name}")
        return "\n".join(lines) if lines else "（空目录）"
    except Exception as e:
        return f"[错误] 列出目录失败：{e}"


async def search_files(directory: str, pattern: str) -> str:
    """在目录中搜索匹配 glob pattern 的文件。"""
    try:
        matches = sorted(Path(directory).rglob(pattern))
        if not matches:
            return f"未找到匹配 '{pattern}' 的文件"
        return "\n".join(str(m) for m in matches)
    except Exception as e:
        return f"[错误] 搜索文件失败：{e}"


def register_cli_tools(registry: "ToolRegistry") -> None:
    """注册所有 CLI 和文件工具到 ToolRegistry。"""
    tools = [
        (
            ToolMeta(
                name="run_shell",
                description="执行 shell 命令，返回 stdout/stderr 输出。",
                input_schema={
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "要执行的 shell 命令"},
                        "timeout": {"type": "integer", "description": "超时秒数，默认 30"},
                    },
                    "required": ["command"],
                },
                is_dangerous=False,
                timeout=30,
            ),
            run_shell,
        ),
        (
            ToolMeta(
                name="read_file",
                description="读取本地文件内容。",
                input_schema={
                    "type": "object",
                    "properties": {"path": {"type": "string", "description": "文件路径"}},
                    "required": ["path"],
                },
                is_dangerous=False,
                timeout=10,
            ),
            read_file,
        ),
        (
            ToolMeta(
                name="write_file",
                description="写入内容到本地文件（覆盖）。",
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "文件路径"},
                        "content": {"type": "string", "description": "文件内容"},
                    },
                    "required": ["path", "content"],
                },
                is_dangerous=True,  # 覆盖写入视为危险操作
                timeout=10,
            ),
            write_file,
        ),
        (
            ToolMeta(
                name="list_dir",
                description="列出目录下的文件和子目录。",
                input_schema={
                    "type": "object",
                    "properties": {"path": {"type": "string", "description": "目录路径"}},
                    "required": ["path"],
                },
                is_dangerous=False,
                timeout=10,
            ),
            list_dir,
        ),
        (
            ToolMeta(
                name="search_files",
                description="在目录中搜索匹配 glob pattern 的文件。",
                input_schema={
                    "type": "object",
                    "properties": {
                        "directory": {"type": "string", "description": "搜索根目录"},
                        "pattern": {"type": "string", "description": "glob 匹配模式，如 '*.py'"},
                    },
                    "required": ["directory", "pattern"],
                },
                is_dangerous=False,
                timeout=15,
            ),
            search_files,
        ),
    ]

    for meta, fn in tools:
        registry.register(meta, fn)
