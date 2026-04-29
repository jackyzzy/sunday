"""AgentLoop / ToolRegistry / MemoryClient 统一构建工厂（CLI 和 Service 共用）。"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sunday.agent.react_agent import ReactAgent
    from sunday.agent.utils import EmitCallable
    from sunday.config import SundayConfig
    from sunday.memory.client import MemoryClient
    from sunday.tools.registry import ConfirmationHandler, ToolRegistry

logger = logging.getLogger(__name__)


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


def build_memory_client(
    cfg: "SundayConfig",
    *,
    run_janitor: bool = False,
    mode: str | None = None,
) -> "MemoryClient":
    """构造单进程共用的 MemoryClient。按 `cfg.memory.backend` 派发实现。

    - `mode="service"`：长驻进程，janitor 自动开启（覆盖 run_janitor 默认）
    - `mode="cli"` 或 `mode=None`：一次性进程，janitor 关
    - `run_janitor` 显式参数仍可强制覆盖
    """
    backend = cfg.memory.backend
    if backend == "local":
        return _build_local_memory_client(cfg, run_janitor=run_janitor, mode=mode)
    if backend == "http":
        raise NotImplementedError(
            "Memory HTTP backend 尚未实现；请在 configs/agent.yaml 中保留 "
            "memory.backend=local（默认）或等待后续版本。"
        )
    raise ValueError(f"未知 memory.backend：{backend!r}（合法值：local | http）")


def _build_local_memory_client(
    cfg: "SundayConfig",
    *,
    run_janitor: bool,
    mode: str | None,
) -> "MemoryClient":
    """LocalMemoryClient 构造逻辑。"""
    from sunday.memory.local import LocalMemoryClient

    # mode 决定 janitor：service 默认开，其他默认关；run_janitor 显式参数最高优先级
    effective_run_janitor = run_janitor or (mode == "service")

    workspace_dir = cfg.agent.workspace_dir
    skills_dir = workspace_dir.parent.parent / "skills"
    return LocalMemoryClient(
        sessions_dir=cfg.agent.sessions_dir,
        memory_dir=cfg.agent.memory_dir,
        log_dir=cfg.agent.log_dir,
        workspace_dir=workspace_dir,
        skills_dir=skills_dir,
        retention_days=cfg.memory.log_retention_days,
        run_janitor=effective_run_janitor,
    )


def build_agent_loop(
    cfg: "SundayConfig",
    emit: "EmitCallable",
    mode: str = "service",
    confirmation_handler: "ConfirmationHandler | None" = None,
    memory_client: "MemoryClient | None" = None,
) -> "ReactAgent":
    """构建 ReactAgent。

    `memory_client` 必传（CLI/Service 各自构造一次），ReactAgent 内部所有
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


def project_template_dir(cfg: "SundayConfig") -> Path:
    """返回项目模板目录（<project_root>/workspace/）。

    用 configs_dir 锚定项目根，即使用户把 workspace_dir 改到任意路径也能正确定位。
    """
    return cfg.configs_dir.parent / "workspace"


class RuntimeNotInitializedError(RuntimeError):
    """运行时关键文件缺失（如 SOUL.md），提示用户先 `sunday init`。"""


def assert_runtime_initialized(cfg: "SundayConfig") -> None:
    """冷启动检查：确认 ~/.sunday/workspace/SOUL.md 存在。

    Service / agent 启动时调用；缺失则 raise RuntimeNotInitializedError。
    不再做"自动补救式"复制 —— seed 是 sunday init 的职责（方向 2：Memory 黑盒）。
    """
    soul_path = cfg.agent.workspace_dir / "SOUL.md"
    if not soul_path.exists():
        raise RuntimeNotInitializedError(
            f"未找到 {soul_path}。请先运行 `sunday init` 完成首次部署。"
        )


async def ensure_runtime_dirs(
    cfg: "SundayConfig", client: "MemoryClient"
) -> dict[str, list[str]]:
    """首次部署 seed：通过 MemoryClient 接口复制 L0/L1 模板。

    由 `sunday init` 调用。Agent / Service 启动时不再走这条路径，假设已 init 过。
    返回 {"workspace": [seeded_files], "knowledge": [seeded_files]}，供 init 上报。
    """
    template_dir = project_template_dir(cfg)
    if not template_dir.is_dir():
        logger.warning("项目模板目录不存在：%s", template_dir)
        return {"workspace": [], "knowledge": []}

    seeded_workspace = await client.workspace.ensure_seeded(template_dir)
    seeded_knowledge = await client.knowledge.ensure_seeded(template_dir)

    # session/log 目录由 LocalMemoryClient 各 sub-client 在构造时已 mkdir，无需在此处理
    return {"workspace": seeded_workspace, "knowledge": seeded_knowledge}
