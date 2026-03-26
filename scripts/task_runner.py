"""任务运行器 — 从 agent.yaml tasks 节读取并执行定义的任务。

用法：
    uv run python scripts/task_runner.py daily_brief
    uv run python scripts/task_runner.py --list
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

# 确保能 import sunday 包（非安装模式运行时）
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

logger = logging.getLogger(__name__)


class TaskRunner:
    """从配置文件加载并运行任务。"""

    def __init__(self, settings=None) -> None:
        if settings is None:
            from sunday.config import Settings

            settings = Settings()
        self._settings = settings

    def list_tasks(self) -> list[str]:
        """返回配置中所有任务名称。"""
        return list(self._settings.sunday.tasks.keys())

    def get_task(self, name: str):
        """获取任务配置，不存在时抛出 ValueError。"""
        tasks = self._settings.sunday.tasks
        if name not in tasks:
            available = ", ".join(tasks.keys()) if tasks else "（无）"
            raise ValueError(f"未知任务：'{name}'。可用任务：{available}")
        return tasks[name]

    async def run_task(self, name: str, agent_loop=None) -> str:
        """运行指定任务，返回执行结果。

        agent_loop 可注入替代 AgentLoop（用于测试）。
        """
        task_cfg = self.get_task(name)
        description = task_cfg.description
        steps = task_cfg.steps

        logger.info("开始执行任务：%s", name)

        from sunday.agent.models import AgentState

        full_task = description
        if steps:
            steps_text = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(steps))
            full_task = f"{description}\n\n执行步骤：\n{steps_text}"

        state = AgentState(session_id=f"task_{name}", task=full_task)

        if agent_loop is not None:
            return await agent_loop.run(state) or ""

        # 构建完整 AgentLoop
        from sunday.agent.executor import Executor
        from sunday.agent.loop import AgentLoop
        from sunday.agent.planner import Planner
        from sunday.agent.verifier import Verifier
        from sunday.gateway.protocol import EventType
        from sunday.memory.context import ContextBuilder
        from sunday.memory.manager import MemoryManager
        from sunday.skills.loader import SkillLoader
        from sunday.tools.cli_tool import register_cli_tools
        from sunday.tools.registry import ToolRegistry

        cfg = self._settings
        workspace_dir = cfg.sunday.agent.workspace_dir

        registry = ToolRegistry(cfg, confirmation_handler=_cli_confirm)
        register_cli_tools(registry)

        skill_loader = SkillLoader(
            project_skills_dir=Path(__file__).parent.parent / "skills",
            user_skills_dir=workspace_dir / "skills",
        )
        skill_loader.discover()

        context_builder = ContextBuilder(workspace_dir, skill_loader=skill_loader)
        memory_manager = MemoryManager(workspace_dir, cfg)

        async def emit(sid, event_type, data):
            if event_type == EventType.STREAM:
                print(data.get("delta", ""), end="", flush=True)
            elif event_type == EventType.STATUS:
                state_name = data.get("state", "")
                if state_name == "thinking":
                    print("\n[思考中...]", flush=True)
                elif state_name.startswith("executing"):
                    print(f"\n[执行：{state_name}]", flush=True)

        loop = AgentLoop(
            planner=Planner(cfg),
            executor=Executor(cfg, tool_registry=registry),
            verifier=Verifier(cfg),
            emit=emit,
            context_builder=context_builder,
            memory_manager=memory_manager,
        )

        result = await loop.run(state)
        print()
        return result or ""


async def _cli_confirm(tool_name: str, arguments: dict, session_id: str) -> bool:
    """命令行确认处理器。"""
    print(f"\n[确认] 工具 '{tool_name}' 是不可逆操作。")
    print(f"参数：{arguments}")
    answer = input("是否确认执行？(y/N) ").strip().lower()
    return answer in ("y", "yes")


async def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

    if len(sys.argv) < 2 or sys.argv[1] in ("--help", "-h"):
        print("用法：uv run python scripts/task_runner.py <task_name>")
        print("      uv run python scripts/task_runner.py --list")
        sys.exit(0)

    runner = TaskRunner()

    if sys.argv[1] == "--list":
        tasks = runner.list_tasks()
        if tasks:
            print("可用任务：")
            for name in tasks:
                task_cfg = runner.get_task(name)
                print(f"  {name}: {task_cfg.description}")
        else:
            print("未配置任何任务（请在 configs/agent.yaml 的 tasks 节中添加）")
        return

    task_name = sys.argv[1]
    try:
        result = await runner.run_task(task_name)
        if result:
            print(f"\n任务完成：{result[:500]}")
    except ValueError as e:
        print(f"错误：{e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"任务执行失败：{e}", file=sys.stderr)
        logger.exception("任务执行异常")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
