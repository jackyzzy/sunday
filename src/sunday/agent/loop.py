"""Phase 2/3：AgentLoop — 主控制器（接入记忆系统）"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from sunday.agent.executor import Executor
from sunday.agent.models import AgentState, StepStatus, TeamResult
from sunday.agent.planner import Planner
from sunday.agent.simple import SimpleNode
from sunday.agent.team import Team
from sunday.agent.utils import EmitCallable, noop_emit
from sunday.agent.verifier import Verifier

if TYPE_CHECKING:
    from sunday.config import SundayConfig
    from sunday.memory.context import ContextBuilder
    from sunday.memory.manager import MemoryManager

logger = logging.getLogger(__name__)


class AgentLoop:
    """AgentLoop 主控制器。

    依赖通过构造函数注入，不直接 import gateway。
    emit 回调解耦 AgentLoop 和 Gateway。
    context_builder 和 memory_manager 为可选注入（Phase 3+）。
    mode 用于 session_log 区分 cli / gateway。
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
        mode: str = "gateway",
    ) -> None:
        self.planner = planner
        self.executor = executor
        self.verifier = verifier
        self.emit = emit or noop_emit
        self.context_builder = context_builder
        self.memory_manager = memory_manager
        self.config = config
        self.mode = mode

    async def run(self, state: AgentState) -> str:
        """执行完整的 think→plan→execute→verify 循环，返回最终摘要。"""
        from sunday.agent.session_log import SessionLog

        # session 结构化日志
        log_dir = self.config.agent.log_dir if self.config else Path.home() / ".sunday" / "logs"
        session_log = SessionLog(log_dir, state.session_id)
        session_log.log_start(state.task, state.thinking_level.value, self.mode)

        # emit 包装：同时转发事件到 session_log
        emit = self.emit
        async def logged_emit(sid: str, et: str, d: dict) -> None:
            await emit(sid, et, d)
            session_log.log(et, d)

        # session report 目录
        session_report_dir: Path | None = None
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

            await logged_emit(state.session_id, "status", {"status": "thinking"})

            # THINK + PLAN
            plan = await self.planner.think_and_plan(state)
            state.plan = plan
            await logged_emit(state.session_id, "plan", {
                "goal": plan.goal,
                "steps": [s.model_dump() for s in plan.steps],
            })

            # EXECUTE + VERIFY（串行，按依赖顺序）
            summary = await self._execute_steps(state, logged_emit, session_log)

            await logged_emit(state.session_id, "status", {"status": "idle"})
            self._write_report(session_report_dir, summary, state)

            # 记忆整合（Phase 3+）
            if self.memory_manager is not None:
                await self.memory_manager.consolidate_session(state)
                logger.debug("记忆整合完成，session=%s", state.session_id)

            session_log.log_end(summary, state.team_results)
            return summary

        except asyncio.CancelledError:
            state.aborted = True
            await emit(state.session_id, "status", {"status": "aborted"})
            session_log.log_abort()
            raise
        except Exception as e:
            logger.exception("AgentLoop 未捕获异常：%s", e)
            await emit(state.session_id, "status", {"status": "error", "message": str(e)})
            session_log.log_error(e)
            raise
        finally:
            logger.info("AgentLoop 结束，session=%s，Team数=%d",
                        state.session_id, len(state.team_results))

    async def _execute_steps(
        self, state: AgentState, emit: EmitCallable, session_log
    ) -> str:
        """串行执行 plan 中所有步骤，处理重规划，返回最终评估摘要。

        重规划双重限制：
        - max_replans_per_step：每个步骤独立计数，防止单步无限重试
        - max_replans_total：整个任务的兜底上限，防止整体失控
        """
        reasoning = self.config.reasoning if self.config else None
        max_per_step = reasoning.max_replans_per_step if reasoning else 2
        max_total = reasoning.max_replans_total if reasoning else 10

        steps = list(state.plan.steps)
        idx = 0
        total_replan_count = 0

        while idx < len(steps):
            step = steps[idx]

            if not self._deps_satisfied(step, state):
                step.status = StepStatus.SKIPPED
                await emit(state.session_id, "step_result", {
                    "step_id": step.id,
                    "status": StepStatus.SKIPPED.value,
                    "verified": None,
                })
                idx += 1
                continue

            step.status = StepStatus.RUNNING
            await emit(state.session_id, "status", {"status": f"executing:{step.id}"})

            step_replan_count = 0
            replan_took_over = False  # 重规划接管了后续步骤，跳过本步骤的正常收尾
            while True:
                if step.is_simple:
                    node = SimpleNode(self.config, self.executor.tool_registry, emit=emit)
                    team_result = await node.run(step, state)
                else:
                    team = Team(self.config, self.executor.tool_registry, emit=emit)
                    team_result = await team.run(step, state)

                if team_result.passed:
                    step.status = StepStatus.DONE
                    break

                step.status = StepStatus.FAILED
                can_replan = (
                    step_replan_count < max_per_step
                    and total_replan_count < max_total
                )
                if can_replan:
                    step_replan_count += 1
                    total_replan_count += 1
                    logger.info(
                        "步骤 %s 失败，触发局部重规划（步骤第 %d/%d 次，全局第 %d/%d 次）",
                        step.id, step_replan_count, max_per_step,
                        total_replan_count, max_total,
                    )
                    await emit(state.session_id, "status", {"status": "replanning"})
                    new_steps = await self._try_replan(step, team_result, state)
                    if new_steps:
                        steps = steps[:idx] + new_steps
                        state.plan.steps = steps
                        # 记录失败结果后由新步骤接管，跳过外层的正常收尾
                        state.team_results.append(team_result)
                        replan_took_over = True
                    # 无论重规划是否产出新步骤，本步骤的重试均结束
                    break
                else:
                    if step_replan_count >= max_per_step:
                        logger.warning(
                            "步骤 %s 已达单步重规划上限 %d，继续执行后续步骤",
                            step.id, max_per_step,
                        )
                    else:
                        logger.warning(
                            "步骤 %s 失败，全局重规划已达上限 %d，继续执行后续步骤",
                            step.id, max_total,
                        )
                    break

            if replan_took_over:
                # new_steps 已替换 steps[idx:]，idx 不变，直接进入下一轮执行新步骤[0]
                continue

            state.team_results.append(team_result)
            await emit(state.session_id, "step_result", {
                "step_id": step.id,
                "status": step.status.value,
                "verified": team_result.passed,
            })
            idx += 1

        # EVALUATE（顶层整体评估）
        await emit(state.session_id, "status", {"status": "summarizing"})
        return await self.verifier.evaluate(state, state.team_results)

    async def _try_replan(self, step, team_result: TeamResult, state: AgentState) -> list:
        """尝试局部重规划，失败时返回空列表。"""
        try:
            new_steps = await self.planner.replan(step, team_result.output, state)
        except Exception as e:
            logger.warning("局部重规划失败（%s），跳过重规划", e)
            return []
        if not new_steps:
            logger.warning("重规划返回空步骤，停止执行循环")
        return new_steps

    def _write_report(self, report_dir: Path | None, summary: str, state: AgentState) -> None:
        """将执行结果写入 session report 目录（可选）。"""
        if report_dir is None:
            return
        report_dir.mkdir(parents=True, exist_ok=True)
        (report_dir / "summary.md").write_text(summary, encoding="utf-8")
        lines: list[str] = []
        for tr in state.team_results:
            mark = "✓" if tr.passed else "✗"
            lines += [f"## {tr.step_id} [{mark}]", tr.output or "", ""]
            for sr in tr.sub_steps:
                lines += [f"  ### {sr.step_id} — {sr.status.value}", sr.output or "", ""]
        (report_dir / "steps.md").write_text("\n".join(lines), encoding="utf-8")
        logger.debug("报告已写入：%s", report_dir)

    @staticmethod
    def _deps_satisfied(step, state: AgentState) -> bool:
        """检查步骤的所有依赖是否已完成（基于 team_results）。"""
        if not step.depends_on:
            return True
        done_ids = {tr.step_id for tr in state.team_results if tr.passed}
        return all(dep in done_ids for dep in step.depends_on)
