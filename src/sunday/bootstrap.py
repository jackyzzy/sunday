"""AgentLoop / ToolRegistry / MemoryClient 统一构建工厂（CLI 和 Gateway 共用）。"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sunday.agent.react_agent import ReactAgent
    from sunday.agent.utils import EmitCallable
    from sunday.config import SundayConfig
    from sunday.memory.client import MemoryClient
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


def build_memory_client(cfg: "SundayConfig", *, run_janitor: bool = True) -> "MemoryClient":
    """构造单进程共用的 MemoryClient。

    路径来源于 cfg.agent.* 与 cfg.memory.log_retention_days；当前阶段返回
    LocalMemoryClient（文件后端），未来可按 cfg.memory.backend 派发到 HTTP 实现。
    """
    from sunday.memory.local import LocalMemoryClient

    workspace_dir = cfg.agent.workspace_dir
    skills_dir = workspace_dir.parent.parent / "skills"
    return LocalMemoryClient(
        sessions_dir=cfg.agent.sessions_dir,
        memory_dir=cfg.agent.memory_dir,
        log_dir=cfg.agent.log_dir,
        workspace_dir=workspace_dir,
        skills_dir=skills_dir,
        retention_days=cfg.memory.log_retention_days,
        run_janitor=run_janitor,
    )


def build_agent_loop(
    cfg: "SundayConfig",
    emit: "EmitCallable",
    mode: str = "gateway",
    confirmation_handler: "ConfirmationHandler | None" = None,
    memory_client: "MemoryClient | None" = None,
) -> "ReactAgent":
    """构建 ReactAgent。

    `memory_client` 必传（CLI/Gateway 各自构造一次），ReactAgent 内部所有
    持久化都通过它走。
    """
    from sunday.agent.react_agent import ReactAgent

    if memory_client is None:
        memory_client = build_memory_client(cfg)

    return ReactAgent(
        config=cfg,
        emit=emit,
        mode=mode,
        confirmation_handler=confirmation_handler,
        memory_client=memory_client,
    )
