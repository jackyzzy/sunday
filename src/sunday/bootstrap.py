"""AgentLoop 和 ToolRegistry 统一构建工厂（CLI 和 Gateway 共用）。"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sunday.agent.react_agent import ReactAgent
    from sunday.agent.utils import EmitCallable
    from sunday.config import SundayConfig
    from sunday.tools.registry import ConfirmationHandler, ToolRegistry


def build_tool_registry(
    cfg: "SundayConfig",
    confirmation_handler: "ConfirmationHandler | None" = None,
) -> "ToolRegistry":
    """构建并返回已加载所有工具的 ToolRegistry。

    加载顺序（后加载可覆盖前加载）：
    1. 内置 CLI 工具
    2. skills/ 目录下的技能工具
    3. workspace/tools/ 下的用户自定义工具
    """
    from sunday.tools.cli_tool import register_cli_tools
    from sunday.tools.local_loader import load_skill_tools, load_user_tools
    from sunday.tools.registry import ToolRegistry

    registry = ToolRegistry(cfg, confirmation_handler=confirmation_handler)
    register_cli_tools(registry)

    workspace_dir = cfg.agent.workspace_dir
    project_skills_dir = workspace_dir.parent.parent / "skills"
    load_skill_tools(project_skills_dir, registry)
    load_user_tools(workspace_dir, registry)
    return registry


def build_agent_loop(
    cfg: "SundayConfig",
    emit: "EmitCallable",
    mode: str = "gateway",
    confirmation_handler: "ConfirmationHandler | None" = None,
) -> "ReactAgent":
    """构建 ReactAgent（原 AgentLoop）。

    现在只需传 config，ReactAgent.__init__ 内部自动组装所有依赖。
    函数签名保持不变，CLI 和 Gateway 调用方无需修改。
    """
    from sunday.agent.react_agent import ReactAgent

    return ReactAgent(
        config=cfg,
        emit=emit,
        mode=mode,
        confirmation_handler=confirmation_handler,
    )
