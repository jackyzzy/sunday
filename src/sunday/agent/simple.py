"""SimpleNode：两层架构中的简单步骤执行节点。

接口与 Team 一致：run(step, parent_state) → TeamResult。
由 ReactAgent._create_node() 创建，接收 clone 后的 ToolRegistry。

历史 SimpleAgent（Phase 1 遗留 chatbot 旁路）已在 S1-C 删除 ——
所有任务执行统一走 Service + ReactAgent 全链路。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sunday.agent.executor import ToolRegistryProtocol
    from sunday.agent.models import AgentState, Step, TeamResult
    from sunday.agent.utils import EmitCallable
    from sunday.config import SundayConfig
    from sunday.templates.loader import TemplateLoader


class SimpleNode:
    """执行单个简单 Step 的轻量节点（无子规划，单次 ReAct + 验证）。

    接口与 Team 一致：run(step, parent_state) → TeamResult。
    tool_registry 由 ReactAgent._create_node() 传入（基础注册表的 clone）。
    """

    def __init__(
        self,
        config: "SundayConfig",
        tool_registry: "ToolRegistryProtocol",
        emit: "EmitCallable | None" = None,
        executor_prompt_override: str | None = None,
        templates: "TemplateLoader | None" = None,  # noqa: ARG002 — 接口对齐 Team；SimpleNode 当前不做子规划，但保留参数以便未来扩展
    ) -> None:
        from sunday.agent.executor import Executor
        from sunday.agent.utils import noop_emit
        from sunday.agent.verifier import Verifier

        self.emit = emit or noop_emit
        self.executor = Executor(
            config,
            tool_registry=tool_registry,
            executor_prompt_override=executor_prompt_override,
            emit=self.emit,
        )
        self.verifier = Verifier(config)

    async def run(self, step: "Step", parent_state: "AgentState") -> "TeamResult":
        from sunday.agent.executor import MaxStepsError, RepetitionError
        from sunday.agent.models import StepResult, StepStatus, TeamResult

        session_id = parent_state.session_id
        await self.emit(session_id, "status", {"status": f"simple:{step.id}"})
        try:
            result = await self.executor.run(step, parent_state)
        except (MaxStepsError, RepetitionError) as e:
            result = StepResult(step_id=step.id, status=StepStatus.FAILED, output=str(e))
        verify = await self.verifier.check(step, result, parent_state)
        result.verified = verify.passed
        result.verify_reason = verify.reason
        if verify.unverified:
            result.output = self.verifier.apply_unverified_label(result.output)
            await self.emit(session_id, "verify_unavailable", {
                "step_id": step.id,
                "reason": verify.reason,
            })
        return TeamResult(
            step_id=step.id,
            passed=verify.passed,
            output=result.output,
            sub_steps=[result],
        )
