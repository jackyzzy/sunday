"""Team — 执行单个顶层 Step 的自治单元，内含完整 plan/execute/verify 闭环"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Awaitable, Callable

from sunday.agent.executor import Executor, MaxStepsError, RepetitionError
from sunday.agent.models import AgentState, Step, StepResult, StepStatus, TeamResult
from sunday.agent.planner import Planner
from sunday.agent.verifier import Verifier

if TYPE_CHECKING:
    from sunday.config import SundayConfig
    from sunday.agent.executor import ToolRegistryProtocol

logger = logging.getLogger(__name__)

# Team 专用规划提示模板（尚未渲染，parent_step_id 在 run() 中填入）
_TEAM_PLAN_PROMPT_TMPL = """你是一个自动化执行助手，运行在本地电脑上，可以直接调用工具完成任务。

当前子任务（父步骤：{parent_step_id}）：{{task}}

请将这个子任务分解为 1~3 个**可直接程序化执行**的步骤。

规则：
- 每步必须是**工具调用或直接输出**，不能是"打开终端""输入命令"等人机交互操作
- 步骤数量尽量少，能一步完成的不拆成两步
- success_criteria 用于自动验证，应基于输出内容判断，不应要求交互操作
- 步骤 id 必须以父步骤 ID 为前缀，格式为 "{parent_step_id}.1"、"{parent_step_id}.2" 等

以 JSON 格式输出，不要任何额外说明：
{{{{
  "goal": "子任务目标",
  "steps": [
    {{{{
      "id": "{parent_step_id}.1",
      "intent": "这步要做什么（直接操作描述）",
      "expected_input": "需要什么输入",
      "expected_output": "输出是什么",
      "success_criteria": "输出满足什么条件算成功",
      "depends_on": []
    }}}}
  ]
}}}}"""

EmitCallable = Callable[[str, str, dict], Awaitable[None]]


async def _noop_emit(session_id: str, event_type: str, data: dict) -> None:
    _ = session_id, event_type, data


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
        self.emit = emit or _noop_emit

    async def run(self, step: Step, parent_state: AgentState) -> TeamResult:
        """执行单个顶层 Step，返回 TeamResult。

        子规划基于 step.intent + step.success_criteria 生成 1~3 个子步骤。
        子步骤串行执行，任意子步骤验证失败即停止并上报。
        """
        session_id = parent_state.session_id

        await self.emit(session_id, "status", {
            "status": f"team:{step.id}:planning",
        })

        # 子规划：以顶层 Step 的意图作为子任务
        sub_task = step.intent
        if step.expected_output:
            sub_task += f"\n期望输出：{step.expected_output}"
        if step.success_criteria:
            sub_task += f"\n成功标准：{step.success_criteria}"

        # 注入前序 Team 的输出作为上下文，让当前 Team 知道已有哪些信息
        if parent_state.team_results:
            prev_context = "\n".join(
                f"- {tr.step_id} 的输出：{tr.output[:5000]}"
                for tr in parent_state.team_results
                if tr.passed
            )
            if prev_context:
                sub_task += f"\n\n已完成步骤的输出（可直接使用）：\n{prev_context}"

        sub_state = AgentState(
            session_id=session_id,
            task=sub_task,
            thinking_level=parent_state.thinking_level,
        )

        team_plan_prompt = _TEAM_PLAN_PROMPT_TMPL.format(parent_step_id=step.id)
        try:
            sub_plan = await self.planner.think_and_plan(sub_state, plan_prompt=team_plan_prompt)
        except Exception as e:
            logger.warning("Team %s 子规划失败：%s", step.id, e)
            return TeamResult(step_id=step.id, passed=False, output=f"子规划失败：{e}")

        sub_state.plan = sub_plan
        logger.debug("Team %s 子规划完成，共 %d 个子步骤", step.id, len(sub_plan.steps))

        # 串行执行子步骤
        all_passed = True
        for sub_step in sub_plan.steps:
            await self.emit(session_id, "status", {
                "status": f"team:{step.id}:{sub_step.id}",
            })

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

            if verify.passed:
                sub_step.status = StepStatus.DONE
            else:
                sub_step.status = StepStatus.FAILED
                all_passed = False

            sub_state.step_results.append(result)

            if not all_passed:
                logger.info("Team %s 子步骤 %s 验证失败，停止执行", step.id, sub_step.id)
                break

        # 汇总输出
        output = "\n".join(r.output for r in sub_state.step_results if r.output)
        return TeamResult(
            step_id=step.id,
            passed=all_passed,
            output=output,
            sub_steps=sub_state.step_results,
        )
