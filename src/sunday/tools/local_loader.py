"""用户自定义本地工具加载器。

扫描 workspace/tools/*.py，每个文件通过 TOOLS 变量声明工具列表：

    # workspace/tools/my_tools.py
    from sunday.tools.registry import ToolMeta

    async def my_fetch(url: str) -> str:
        ...

    TOOLS = [
        (ToolMeta(name="my_fetch", description="...", input_schema=...), my_fetch),
    ]

加载器在 register_cli_tools() 之后调用，用户工具可覆盖同名内置工具。
"""
from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sunday.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


def load_user_tools(workspace_dir: Path, registry: "ToolRegistry") -> int:
    """扫描 workspace_dir/tools/*.py 并将工具注册到 registry。

    返回成功注册的工具数量。文件加载失败时记录日志并跳过，不抛出异常。
    """
    tools_dir = workspace_dir / "tools"
    if not tools_dir.exists():
        return 0

    count = 0
    for py_file in sorted(tools_dir.glob("*.py")):
        if py_file.name.startswith("_"):
            continue
        try:
            spec = importlib.util.spec_from_file_location(py_file.stem, py_file)
            if spec is None or spec.loader is None:
                continue
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
            tool_list = getattr(mod, "TOOLS", [])
            for meta, fn in tool_list:
                registry.register(meta, fn)
                count += 1
                logger.debug("加载用户工具：%s（来自 %s）", meta.name, py_file.name)
        except Exception as e:
            logger.warning("加载用户工具文件失败：%s — %s", py_file.name, e)

    if count:
        logger.info("已加载 %d 个用户自定义工具", count)
    return count


def load_skill_tools(skills_dir: Path, registry: "ToolRegistry") -> int:
    """扫描 skills/*/*.py 和 skills/*/*.sh，注册声明了元数据的工具。

    .py 文件：定义 TOOLS = [(ToolMeta(...), fn), ...] 即自动注册（文件名不限）。
    .sh 文件：文件头部有 YAML frontmatter 注释（# --- ... # ---）即自动注册为 shell 工具。
    _ 开头的文件跳过（私有）。未声明元数据的文件静默跳过（向后兼容）。
    """
    if not skills_dir or not skills_dir.exists():
        return 0

    count = 0
    for skill_dir in sorted(skills_dir.iterdir()):
        if not skill_dir.is_dir():
            continue

        # 扫描 .py 文件
        for py_file in sorted(skill_dir.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            try:
                spec = importlib.util.spec_from_file_location(
                    f"skills.{skill_dir.name}.{py_file.stem}", py_file
                )
                if spec is None or spec.loader is None:
                    continue
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)  # type: ignore[union-attr]
                tool_list = getattr(mod, "TOOLS", [])
                for meta, fn in tool_list:
                    registry.register(meta, fn)
                    count += 1
                    logger.debug("加载技能工具：%s（来自 %s/%s）", meta.name, skill_dir.name, py_file.name)
            except Exception as e:
                logger.warning("加载技能工具失败：%s/%s — %s", skill_dir.name, py_file.name, e)

        # 扫描 .sh 文件
        for sh_file in sorted(skill_dir.glob("*.sh")):
            if sh_file.name.startswith("_"):
                continue
            meta = _parse_sh_frontmatter(sh_file)
            if meta is None:
                continue
            _sh_path = str(sh_file)

            async def _run_sh(_p: str = _sh_path, **kwargs: object) -> str:
                args = " ".join(str(v) for v in kwargs.values())
                return await _shell_exec(f"bash {_p} {args}")

            registry.register(meta, _run_sh)
            count += 1
            logger.debug("加载 Shell 技能工具：%s（来自 %s）", meta.name, sh_file.name)

    if count:
        logger.info("已加载 %d 个技能工具", count)
    return count


def _parse_sh_frontmatter(sh_file: Path):
    """解析 .sh 文件头部的 YAML frontmatter 注释，返回 ToolMeta 或 None。

    格式示例：
        #!/bin/bash
        # ---
        # name: git_log
        # description: 查看 git 提交历史
        # args:
        #   - name: n
        #     type: integer
        #     description: 显示最近几条
        #     optional: true
        # is_dangerous: false
        # timeout: 15
        # ---
    """
    try:
        import yaml as _yaml

        lines = sh_file.read_text(encoding="utf-8").splitlines()
        in_fm = False
        fm_lines: list[str] = []
        for line in lines:
            stripped = line.lstrip("#").strip()
            if stripped == "---":
                if not in_fm:
                    in_fm = True
                else:
                    break
            elif in_fm:
                fm_lines.append(stripped)
        if not fm_lines:
            return None
        fm = _yaml.safe_load("\n".join(fm_lines))
        if not isinstance(fm, dict) or "name" not in fm:
            return None

        args: list[dict] = fm.get("args", []) or []
        properties = {
            a["name"]: {
                "type": a.get("type", "string"),
                "description": a.get("description", ""),
            }
            for a in args
        }
        required = [a["name"] for a in args if not a.get("optional", False)]

        from sunday.tools.registry import ToolMeta

        return ToolMeta(
            name=fm["name"],
            description=fm.get("description", ""),
            input_schema={"type": "object", "properties": properties, "required": required},
            is_dangerous=bool(fm.get("is_dangerous", False)),
            timeout=int(fm.get("timeout", 30)),
        )
    except Exception:
        return None


async def _shell_exec(cmd: str) -> str:
    """执行 shell 命令，返回 stdout + stderr。"""
    import asyncio

    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        out = stdout.decode(errors="replace").strip()
        err = stderr.decode(errors="replace").strip()
        parts = [p for p in [out, f"[stderr] {err}" if err else ""] if p]
        return "\n".join(parts) if parts else ""
    except asyncio.TimeoutError:
        return "[超时] Shell 命令超过 60 秒"
    except Exception as e:
        return f"[错误] Shell 执行失败：{e}"
