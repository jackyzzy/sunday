"""Phase 2/3：AgentLoop — 主控制器（接入记忆系统）"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Awaitable, Callable

from sunday.agent.executor import Executor
from sunday.agent.models import AgentState, StepResult, StepStatus, TeamResult
from sunday.agent.planner import Planner
from sunday.agent.team import Team
from sunday.agent.verifier import Verifier

if TYPE_CHECKING:
    from sunday.config import SundayConfig
    from sunday.memory.context import ContextBuilder
    from sunday.memory.manager import MemoryManager

logger = logging.getLogger(__name__)

# emit 回调类型：emit(session_id, event_type_str, data_dict)
EmitCallable = Callable[[str, str, dict], Awaitable[None]]


async def _noop_emit(session_id: str, event_type: str, data: dict) -> None:
    """默认空 emit，用于 CLI 模式。"""
    _ = session_id, event_type, data


class AgentLoop:
    """AgentLoop 主控制器。

    依赖通过构造函数注入，不直接 import gateway。
    emit 回调解耦 AgentLoop 和 Gateway。
    context_builder 和 memory_manager 为可选注入（Phase 3+）。
    """

    def __init__(
        self,
        planner: Planner,
        executor: Executor,
        verifier: Verifier,
        emit: EmitCallable | None = None,
        context_builder: "ContextBuilder | None" = None,
        memory_manager: "MemoryManager | None" = None,
        config: "SundayConfig | None" = None,
    ) -> None:
        self.planner = planner
        self.executor = executor
        self.verifier = verifier
        self.emit = emit or _noop_emit
        self.context_builder = context_builder
        self.memory_manager = memory_manager
        self.config = config

    async def run(self, state: AgentState) -> str:
        """执行完整的 think→plan→execute→verify 循环，返回最终摘要。"""
        # 根据 config 在运行时计算 session 专属报告目录，并通知 ToolRegistry
        session_report_dir: "Path | None" = None
        if self.config is not None:
            from sunday.tools.cli_tool import make_session_report_dir
            session_report_dir = make_session_report_dir(
                self.config.agent.report_dir, state.task, state.session_id
            )
            if hasattr(self.executor.tool_registry, "set_report_dir"):
                self.executor.tool_registry.set_report_dir(session_report_dir)

        try:
            # 注入 L0 上下文到 Planner（Phase 3+）
            if self.context_builder is not None:
                ctx = self.context_builder.build(state.session_id)
                self.planner.system_prompt = ctx.system_prompt
                logger.debug("上下文注入完成，token_estimate=%d", ctx.token_estimate)

            await self.emit(state.session_id, "status", {"status": "thinking"})

            # THINK + PLAN
            plan = await self.planner.think_and_plan(state)
            state.plan = plan
            await self.emit(state.session_id, "plan", {
                "goal": plan.goal,
                "steps": [s.model_dump() for s in plan.steps],
            })

            # EXECUTE + VERIFY（串行，按依赖顺序）
            steps = list(plan.steps)
            idx = 0
            replan_count = 0
            max_replans = 3
            while idx < len(steps):
                step = steps[idx]

                # 检查依赖是否满足
                if not self._deps_satisfied(step, state):
                    step.status = StepStatus.SKIPPED
                    await self.emit(state.session_id, "step_result", {
                        "step_id": step.id,
                        "status": StepStatus.SKIPPED.value,
                        "verified": None,
                    })
                    idx += 1
                    continue

                step.status = StepStatus.RUNNING
                await self.emit(state.session_id, "status", {
                    "status": f"executing:{step.id}",
                })

                # 每个 Step 交给独立 Team 执行（内含 plan/execute/verify 闭环）
                team = Team(self.config, self.executor.tool_registry, emit=self.emit)
                team_result = await team.run(step, state)

                if team_result.passed:
                    step.status = StepStatus.DONE
                else:
                    step.status = StepStatus.FAILED
                    if replan_count < max_replans:
                        replan_count += 1
                        logger.info("步骤 %s Team 执行失败，触发局部重规划（第 %d/%d 次）",
                                    step.id, replan_count, max_replans)
                        await self.emit(state.session_id, "status", {"status": "replanning"})
                        try:
                            new_steps = await self.planner.replan(step, team_result.output, state)
                        except Exception as replan_err:
                            logger.warning("局部重规划失败（%s），跳过重规划继续执行", replan_err)
                            new_steps = []
                        if new_steps:
                            steps = steps[:idx] + new_steps
                            state.plan.steps = steps
                            state.team_results.append(team_result)
                            continue
                        logger.warning("重规划返回空步骤，提前结束执行循环")
                        state.team_results.append(team_result)
                        break
                    else:
                        logger.warning("步骤 %s 执行失败，已达重规划上限 %d，继续执行后续步骤",
                                       step.id, max_replans)

                state.team_results.append(team_result)
                await self.emit(state.session_id, "step_result", {
                    "step_id": step.id,
                    "status": step.status.value,
                    "verified": team_result.passed,
                })
                idx += 1

            # EVALUATE（顶层整体评估）
            await self.emit(state.session_id, "status", {"status": "summarizing"})
            summary = await self.verifier.evaluate(state, state.team_results)
            await self.emit(state.session_id, "status", {"status": "idle"})

            # 落盘到 session_report_dir
            if session_report_dir is not None:
                session_report_dir.mkdir(parents=True, exist_ok=True)
                (session_report_dir / "summary.md").write_text(summary, encoding="utf-8")
                lines: list[str] = []
                for tr in state.team_results:
                    status_mark = "✓" if tr.passed else "✗"
                    lines += [f"## {tr.step_id} [{status_mark}]", tr.output or "", ""]
                    for sr in tr.sub_steps:
                        lines += [f"  ### {sr.step_id} — {sr.status.value}", sr.output or "", ""]
                (session_report_dir / "steps.md").write_text("\n".join(lines), encoding="utf-8")
                logger.debug("报告已写入：%s", session_report_dir)

            # 记忆整合（Phase 3+）
            if self.memory_manager is not None:
                await self.memory_manager.consolidate_session(state)
                logger.debug("记忆整合完成，session=%s", state.session_id)

            return summary

        except asyncio.CancelledError:
            state.aborted = True
            await self.emit(state.session_id, "status", {"status": "aborted"})
            raise
        except Exception as e:
            logger.exception("AgentLoop 未捕获异常：%s", e)
            await self.emit(state.session_id, "status", {
                "status": "error",
                "message": str(e),
            })
            raise
        finally:
            logger.info("AgentLoop 结束，session=%s，Team数=%d",
                        state.session_id, len(state.team_results))

    @staticmethod
    def _deps_satisfied(step, state: AgentState) -> bool:
        """检查步骤的所有依赖是否已经完成（基于 team_results）。"""
        if not step.depends_on:
            return True
        done_ids = {tr.step_id for tr in state.team_results if tr.passed}
        return all(dep in done_ids for dep in step.depends_on)
