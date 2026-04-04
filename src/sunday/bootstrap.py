"""AgentLoop 和 ToolRegistry 统一构建工厂（CLI 和 Gateway 共用）。

消除 cli.py 与 gateway/server.py 中重复的构建逻辑。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sunday.agent.loop import AgentLoop
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
) -> "AgentLoop":
    """构建完整 AgentLoop，注入所有依赖。

    mode: "cli" 或 "gateway"，写入 session_log。
    """
    from sunday.agent.executor import Executor
    from sunday.agent.loop import AgentLoop
    from sunday.agent.planner import Planner
    from sunday.agent.verifier import Verifier
    from sunday.memory.context import ContextBuilder
    from sunday.memory.manager import MemoryManager
    from sunday.skills.loader import SkillLoader

    workspace_dir = cfg.agent.workspace_dir
    project_skills_dir = workspace_dir.parent.parent / "skills"

    registry = build_tool_registry(cfg, confirmation_handler=confirmation_handler)

    skill_loader = SkillLoader(
        project_skills_dir=project_skills_dir,
        user_skills_dir=workspace_dir / "skills",
    )
    skill_loader.discover()

    context_builder = ContextBuilder(workspace_dir, skill_loader=skill_loader, config=cfg)
    memory_manager = MemoryManager(workspace_dir, config=cfg)

    return AgentLoop(
        planner=Planner(cfg),
        executor=Executor(cfg, tool_registry=registry),
        verifier=Verifier(cfg),
        emit=emit,
        context_builder=context_builder,
        memory_manager=memory_manager,
        config=cfg,
        mode=mode,
    )
