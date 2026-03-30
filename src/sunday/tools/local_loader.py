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
