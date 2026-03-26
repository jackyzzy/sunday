"""Phase 2/3：AgentLoop — 主控制器（接入记忆系统）"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Awaitable, Callable

from sunday.agent.executor import Executor, MaxStepsError, RepetitionError
from sunday.agent.models import AgentState, StepStatus
from sunday.agent.planner import Planner
from sunday.agent.verifier import Verifier

if TYPE_CHECKING:
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
    ) -> None:
        self.planner = planner
        self.executor = executor
        self.verifier = verifier
        self.emit = emit or _noop_emit
        self.context_builder = context_builder
        self.memory_manager = memory_manager

    async def run(self, state: AgentState) -> str:
        """执行完整的 think→plan→execute→verify 循环，返回最终摘要。"""
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
            while idx < len(steps):
                step = steps[idx]

                # 检查依赖是否满足
                if not self._deps_satisfied(step, state):
                    step.status = StepStatus.SKIPPED
                    idx += 1
                    continue

                step.status = StepStatus.RUNNING
                await self.emit(state.session_id, "status", {
                    "status": f"executing:{step.id}",
                })

                try:
                    result = await self.executor.run(step, state)
                except (MaxStepsError, RepetitionError) as e:
                    logger.warning("步骤 %s 执行异常：%s", step.id, e)
                    result = type(
                        "StepResult", (),
                        {
                            "step_id": step.id,
                            "status": StepStatus.FAILED,
                            "output": str(e),
                            "react_iterations": [],
                            "verified": False,
                            "verify_reason": "",
                        }
                    )()
                    from sunday.agent.models import StepResult
                    result = StepResult(
                        step_id=step.id,
                        status=StepStatus.FAILED,
                        output=str(e),
                    )

                # VERIFY
                verify_result = await self.verifier.check(step, result, state)
                result.verified = verify_result.passed
                result.verify_reason = verify_result.reason

                if verify_result.passed:
                    step.status = StepStatus.DONE
                else:
                    step.status = StepStatus.FAILED
                    if verify_result.should_replan:
                        logger.info("步骤 %s 验证失败，触发局部重规划", step.id)
                        await self.emit(state.session_id, "status", {"status": "replanning"})
                        new_steps = await self.planner.replan(step, result.output, state)
                        # 替换当前步骤及之后所有步骤
                        steps = steps[:idx] + new_steps
                        state.plan.steps = steps
                        # 不递增 idx，继续执行新的第 idx 步
                        state.step_results.append(result)
                        continue

                state.step_results.append(result)
                await self.emit(state.session_id, "step_result", {
                    "step_id": result.step_id,
                    "status": result.status.value,
                    "verified": result.verified,
                })
                idx += 1

            # SUMMARIZE
            await self.emit(state.session_id, "status", {"status": "summarizing"})
            summary = await self.verifier.summarize(state)
            await self.emit(state.session_id, "status", {"status": "idle"})

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
            logger.info("AgentLoop 结束，session=%s，步骤数=%d",
                        state.session_id, len(state.step_results))

    @staticmethod
    def _deps_satisfied(step, state: AgentState) -> bool:
        """检查步骤的所有依赖是否已经完成。"""
        if not step.depends_on:
            return True
        done_ids = {
            r.step_id for r in state.step_results
            if r.status == StepStatus.DONE
        }
        return all(dep in done_ids for dep in step.depends_on)
