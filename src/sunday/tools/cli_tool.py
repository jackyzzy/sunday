"""Phase 4：CLI 工具 + 内置文件工具"""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

from sunday.tools.registry import ToolMeta

if TYPE_CHECKING:
    from sunday.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


async def run_shell(command: str, timeout: int = 30, env: dict | None = None) -> str:
    """执行 shell 命令，返回 stdout + stderr。env 为 None 时继承当前进程环境。"""
    try:
        proc = await asyncio.wait_for(
            asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
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



def format_report_footer(result: str, report_dir: "Path | None") -> str:
    """生成报告目录信息块，供 CLI 打印或 Gateway 拼入 DONE content。"""
    if not report_dir:
        return ""
    lines = [f"\n📁 报告目录：{report_dir}/"]
    m = re.search(r"最终交付文件：(.+)", result)
    if m:
        lines.append(f"   → 最终报告：{m.group(1).strip()}")
    return "\n".join(lines)


def register_cli_tools(registry: "ToolRegistry") -> None:
    """注册所有 CLI 和文件工具到 ToolRegistry。

    write_file 的相对路径路由由 registry._report_dir 控制（AgentLoop.run() 在任务开始时设置）。
    """
    async def _write_file(path: str, content: str) -> str:
        p = Path(path)
        if not p.is_absolute() and getattr(registry, "_report_dir", None):
            p = registry._report_dir / p
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        registry.add_written_file(p.name)
        return f"已写入：{p}"

    async def _run_shell(command: str, timeout: int = 30) -> str:
        import os as _os
        report_dir_val = str(getattr(registry, "_report_dir", None) or "")
        env = {**_os.environ, "SUNDAY_REPORT_DIR": report_dir_val}
        return await run_shell(command, timeout, env=env)

    async def _read_file(path: str) -> str:
        p = Path(path)
        if not p.is_absolute() and getattr(registry, "_report_dir", None):
            report_path = registry._report_dir / p
            if report_path.exists():
                try:
                    return report_path.read_text(encoding="utf-8")
                except Exception as e:
                    return f"[错误] 读取文件失败：{e}"
        try:
            return p.read_text(encoding="utf-8")
        except Exception as e:
            return f"[错误] 读取文件失败：{e}"

    tools = [
        (
            ToolMeta(
                name="run_shell",
                description=(
                    "执行 shell 命令，返回 stdout/stderr 输出。\n"
                    "环境变量 $SUNDAY_REPORT_DIR 已预设为当前任务报告目录，"
                    "输出文件请写入该目录（如：echo '...' > \"$SUNDAY_REPORT_DIR/data.json\"）。"
                ),
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
            _run_shell,
        ),
        (
            ToolMeta(
                name="read_file",
                description=(
                    "读取本地文件内容。相对路径优先在当前任务报告目录中查找，"
                    "不存在时 fallback 到给定路径（适合读取项目源码等绝对路径文件）。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {"path": {"type": "string", "description": "文件路径（报告目录内只需文件名即可）"}},
                    "required": ["path"],
                },
                is_dangerous=False,
                timeout=10,
            ),
            _read_file,
        ),
        (
            ToolMeta(
                name="write_file",
                description=(
                    "写入内容到文件（覆盖）。相对路径自动保存至当前任务报告目录。\n"
                    "命名约定：中间草稿可自由命名；最终交付给用户的报告，"
                    "文件名应清晰反映内容（如 最终报告.md 或 final_report.md），且每个任务只产出一份最终报告。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "文件路径（只需文件名即可，如 report.md）"},
                        "content": {"type": "string", "description": "文件内容"},
                    },
                    "required": ["path", "content"],
                },
                is_dangerous=True,  # 覆盖写入视为危险操作
                timeout=10,
            ),
            _write_file,
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

    async def _run_python(code: str) -> str:
        import os as _os
        report_dir_val = str(getattr(registry, "_report_dir", None) or "")
        env = {**_os.environ, "SUNDAY_REPORT_DIR": report_dir_val}
        try:
            proc = await asyncio.create_subprocess_exec(
                "python3", "-c", code,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
            out = stdout.decode(errors="replace").strip()
            err = stderr.decode(errors="replace").strip()
            parts = []
            if out:
                parts.append(out)
            if err:
                parts.append(f"[stderr] {err}")
            if proc.returncode != 0:
                parts.append(f"[returncode={proc.returncode}]")
            return "\n".join(parts) if parts else ""
        except asyncio.TimeoutError:
            return "[超时] Python 代码执行超过 10 秒"
        except Exception as e:
            return f"[错误] 代码执行失败：{e}"

    registry.register(
        ToolMeta(
            name="run_python",
            description=(
                "在本地子进程中执行 Python 代码片段（10 秒超时）。\n"
                "环境变量 os.environ['SUNDAY_REPORT_DIR'] 已预设为当前任务报告目录，"
                "输出文件请写入该路径（如：open(os.path.join(os.environ['SUNDAY_REPORT_DIR'], 'data.json'), 'w')）。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "要执行的 Python 代码"},
                },
                "required": ["code"],
            },
            is_dangerous=True,
            timeout=15,
        ),
        _run_python,
    )

    _register_health_tool(registry)


def _register_health_tool(registry: "ToolRegistry") -> None:
    """注册 check_tool_health 工具（A 场景：主动探测）。"""

    async def check_tool_health(tool_name: str = "") -> str:
        """探测工具健康状态。

        - tool_name 为空：返回所有工具的健康状态摘要
        - tool_name 非空：对该工具重新 probe，更新 HealthStore，返回探测结果
        """
        health_store = registry._health_store

        if not tool_name:
            return health_store.summary()

        # 查找 probe 函数
        meta_pair = registry._tools.get(tool_name)
        if meta_pair is None:
            return f"[工具错误] 未知工具：{tool_name}"

        meta, _ = meta_pair
        from sunday.tools.probe import BUILTIN_PROBES
        probe_fn = getattr(meta, "probe", None) or BUILTIN_PROBES.get(tool_name)

        if probe_fn is None:
            current = health_store.get(tool_name)
            return (
                f"工具 {tool_name} 未配置 probe，无法主动探测。"
                f"当前状态：{current.status.value}"
            )

        try:
            from sunday.tools.health_store import ErrorType
            result = await asyncio.wait_for(probe_fn(), timeout=15)
        except Exception as e:
            return f"[探测异常] {tool_name} 探测失败：{e}"

        if result.success:
            health_store.record_probe_success(tool_name)
            return f"工具 {tool_name} 探测成功，状态已恢复为 healthy"
        else:
            health_store.record_probe_failure(
                tool_name,
                result.error_type or ErrorType.UNKNOWN,
                result.detail,
                result.suggestion,
            )
            msg = f"工具 {tool_name} 探测失败：{result.detail}"
            if result.suggestion:
                msg += f"\n建议：{result.suggestion}"
            return msg

    registry.register(
        ToolMeta(
            name="check_tool_health",
            description=(
                "探测工具的健康状态。tool_name 为空时返回所有工具的健康摘要；"
                "指定 tool_name 时对该工具发起实时探测并更新状态。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "tool_name": {
                        "type": "string",
                        "description": "要探测的工具名（空字符串表示查看全部状态）",
                    }
                },
                "required": [],
            },
            is_dangerous=False,
            timeout=20,
        ),
        check_tool_health,
    )
