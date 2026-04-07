"""Team — 执行单个顶层 Step 的自治单元，内含完整 plan/execute/verify 闭环"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sunday.agent.executor import Executor, MaxStepsError, RepetitionError
from sunday.agent.models import AgentState, Step, StepResult, StepStatus, TeamResult
from sunday.agent.planner import Planner
from sunday.agent.utils import EmitCallable, noop_emit
from sunday.agent.verifier import Verifier

if TYPE_CHECKING:
    from sunday.config import SundayConfig
    from sunday.agent.executor import ToolRegistryProtocol

logger = logging.getLogger(__name__)


class Team:
    """执行单个顶层 Step 的自治单元。

    内部独立运行 Planner → Executor → Verifier 闭环，不重规划（失败直接上报顶层）。
    tool_registry 由顶层传入，所有 Team 共享同一注册表保证工具状态一致。
    """

    def __init__(
        self,
        config: "SundayConfig",
        tool_registry: "ToolRegistryProtocol",
        emit: EmitCallable | None = None,
    ) -> None:
        self.planner = Planner(config)
        self.executor = Executor(config, tool_registry=tool_registry)
        self.verifier = Verifier(config)
        self.emit = emit or noop_emit

    async def run(self, step: Step, parent_state: AgentState) -> TeamResult:
        """执行单个顶层 Step，返回 TeamResult。

        子规划基于 step.intent + step.success_criteria 生成 1~3 个子步骤。
        子步骤串行执行，任意子步骤验证失败即停止并上报。
        """
        session_id = parent_state.session_id

        await self.emit(session_id, "status", {"status": f"team:{step.id}:planning"})

        sub_task = self._build_sub_task(step, parent_state)
        team_plan_prompt = self._get_team_plan_prompt(step.id)

        sub_state = AgentState(
            session_id=session_id,
            task=sub_task,
            thinking_level=parent_state.thinking_level,
        )

        try:
            sub_plan = await self.planner.think_and_plan(sub_state, plan_prompt=team_plan_prompt)
        except Exception as e:
            logger.warning("Team %s 子规划失败：%s", step.id, e)
            return TeamResult(step_id=step.id, passed=False, output=f"子规划失败：{e}")

        sub_state.plan = sub_plan
        logger.debug("Team %s 子规划完成，共 %d 个子步骤", step.id, len(sub_plan.steps))

        # 串行执行子步骤（支持内层重规划）
        max_sub_replans = self.planner.config.reasoning.max_sub_replans
        sub_steps = list(sub_plan.steps)
        idx = 0
        # P2：每个子步骤独立计数，互不影响
        sub_replan_counts: dict[str, int] = {}
        broke_on_failure = False
        last_verify_should_replan = True  # P1：记录最后失败步骤的 should_replan

        while idx < len(sub_steps):
            sub_step = sub_steps[idx]
            await self.emit(session_id, "status", {"status": f"team:{step.id}:{sub_step.id}"})
            sub_step.status = StepStatus.RUNNING

            try:
                result = await self.executor.run(sub_step, sub_state)
            except (MaxStepsError, RepetitionError) as e:
                logger.warning("Team %s 子步骤 %s 执行异常：%s", step.id, sub_step.id, e)
                result = StepResult(
                    step_id=sub_step.id,
                    status=StepStatus.FAILED,
                    output=str(e),
                )

            verify = await self.verifier.check(sub_step, result, sub_state)
            result.verified = verify.passed
            result.verify_reason = verify.reason
            sub_step.status = StepStatus.DONE if verify.passed else StepStatus.FAILED
            sub_state.step_results.append(result)

            if verify.passed:
                idx += 1
                continue

            # 子步骤失败：尝试内层重规划（仅当验证器认为有意义时）
            step_replan_count = sub_replan_counts.get(sub_step.id, 0)
            if step_replan_count < max_sub_replans and verify.should_replan:
                sub_replan_counts[sub_step.id] = step_replan_count + 1
                logger.info(
                    "Team %s 子步骤 %s 失败，触发子任务重规划（%d/%d）",
                    step.id, sub_step.id, step_replan_count + 1, max_sub_replans,
                )
                try:
                    # P0：使用专用的 sub_replan()，而非外层的 replan()
                    new_sub_steps = await self.planner.sub_replan(
                        step, sub_step, result.output, sub_state
                    )
                except Exception as e:
                    logger.warning("Team %s 子任务重规划失败：%s", step.id, e)
                    new_sub_steps = []
                if new_sub_steps:
                    sub_steps = sub_steps[:idx] + new_sub_steps
                    continue  # idx 不变，从新子步骤 sub_steps[idx] 开始

            logger.info("Team %s 子步骤 %s 验证失败，停止执行", step.id, sub_step.id)
            last_verify_should_replan = verify.should_replan
            broke_on_failure = True
            break

        all_passed = not broke_on_failure
        # P4：失败子步骤加 ✗ 标注，帮助外层 replan 准确定位失败原因
        parts = []
        for r in sub_state.step_results:
            tag = "✓" if r.verified else "✗(失败)"
            parts.append(f"[{tag} {r.step_id}] {r.output}")
        output = "\n".join(parts)
        return TeamResult(
            step_id=step.id,
            passed=all_passed,
            output=output,
            should_replan=last_verify_should_replan if broke_on_failure else True,
            sub_steps=sub_state.step_results,
        )

    @staticmethod
    def _build_sub_task(step: Step, parent_state: AgentState) -> str:
        """组装子任务描述，注入期望输出、成功标准和前序上下文。"""
        parts = [step.intent]
        if step.expected_output:
            parts.append(f"期望输出：{step.expected_output}")
        if step.success_criteria:
            parts.append(f"成功标准：{step.success_criteria}")

        prev_outputs = [
            f"- {tr.step_id} 的输出：{tr.output[:5000]}"
            for tr in parent_state.team_results
            if tr.passed and tr.output
        ]
        if prev_outputs:
            parts.append("\n已完成步骤的输出（可直接使用）：\n" + "\n".join(prev_outputs))

        return "\n".join(parts)

    def _get_team_plan_prompt(self, parent_step_id: str) -> str:
        """加载并渲染 Team 子规划 prompt 模板。"""
        tmpl = self.planner.config.load_prompt("team_plan")
        return tmpl.replace("{parent_step_id}", parent_step_id)
