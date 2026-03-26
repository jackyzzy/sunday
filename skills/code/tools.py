"""代码辅助技能工具（Phase 4 占位实现）"""
from __future__ import annotations

import asyncio


async def run_python(code: str) -> str:
    """在子进程中执行 Python 代码片段（10 秒超时）。"""
    try:
        proc = await asyncio.create_subprocess_exec(
            "python3", "-c", code,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
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
