"""Phase 2：Executor — ReAct 执行循环"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, NamedTuple, Protocol

from sunday.agent.llm_client import LLMClient
from sunday.agent.models import (
    AgentState,
    ReactIteration,
    Step,
    StepResult,
    StepStatus,
)

if TYPE_CHECKING:
    from sunday.config import ModelConfig, SundayConfig

logger = logging.getLogger(__name__)


class MaxStepsError(Exception):
    """ReAct 循环超出最大步骤数"""


class RepetitionError(Exception):
    """连续重复相同工具调用"""


class ToolRegistryProtocol(Protocol):
    """Executor 依赖的工具注册表接口（避免循环 import）"""

    def get_schemas(self) -> list[dict]: ...
    async def execute(self, tool_name: str, arguments: dict, session_id: str) -> str: ...


class _LastToolCall(NamedTuple):
    name: str
    arguments_str: str


class Executor:
    """负责 ReAct 执行循环。执行阶段 temperature=0。"""

    def __init__(
        self,
        config: "SundayConfig",
        tool_registry: ToolRegistryProtocol | None = None,
    ) -> None:
        self.config = config
        self.tool_registry = tool_registry
        self._system_prompt: str | None = None

    def _get_system_prompt(self) -> str:
        if self._system_prompt is None:
            self._system_prompt = self.config.load_prompt("executor_system")
        return self._system_prompt

    async def run(self, step: Step, state: AgentState) -> StepResult:
        """执行单个步骤，返回 StepResult。网络错误转为 FAILED 结果，不向上传播。"""
        try:
            return await self._run_inner(step, state)
        except Exception as e:
            import httpx
            if isinstance(e, (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError)):
                msg = f"网络连接失败：{type(e).__name__}（请检查网络或代理配置）"
            elif isinstance(e, httpx.RemoteProtocolError):
                msg = f"服务端连接中断：{e}（可能是上下文过长或服务端超时，可重试）"
            else:
                raise
            logger.error("步骤 %s LLM 调用失败：%s", step.id, msg)
            return StepResult(
                step_id=step.id,
                status=StepStatus.FAILED,
                output=msg,
            )

    async def _run_inner(self, step: Step, state: AgentState) -> StepResult:
        """实际执行逻辑（由 run() 包裹以统一捕获网络异常）。"""
        from sunday.agent.providers import get_provider

        model_cfg: ModelConfig = self.config.model
        max_steps = self.config.reasoning.max_react_iteration
        provider = get_provider(model_cfg.provider)

        system = self._get_system_prompt().format(
            intent=step.intent,
            expected_output=step.expected_output,
            success_criteria=step.success_criteria,
        )
        messages = [{"role": "user", "content": step.intent}]
        tools = self.tool_registry.get_schemas() if self.tool_registry else []
        iterations: list[ReactIteration] = []
        last_tool_call: _LastToolCall | None = None

        for i in range(max_steps):
            # 最后一次机会：禁用工具，强制模型整合已有信息直接输出
            if i == max_steps - 1:
                messages.append({
                    "role": "user",
                    "content": "你已用完工具调用次数。请根据以上收集到的信息，直接输出最终结果，不要再调用工具。",
                })
                response = await self._call_llm(system, messages, [], model_cfg)
                logger.warning("步骤 %s 达到最大迭代次数 %d，已强制收尾输出", step.id, max_steps)
                return StepResult(
                    step_id=step.id,
                    status=StepStatus.DONE,
                    output=response.text,
                    react_iterations=iterations,
                )

            response = await self._call_llm(system, messages, tools, model_cfg)

            # 模型判断任务完成
            if response.finish_reason in ("stop", "end_turn") or not response.tool_call:
                return StepResult(
                    step_id=step.id,
                    status=StepStatus.DONE,
                    output=response.text,
                    react_iterations=iterations,
                )

            # 有工具调用
            tc = response.tool_call
            current_call = _LastToolCall(tc.name, tc.arguments)
            if current_call == last_tool_call:
                raise RepetitionError(f"连续重复调用工具 {tc.name}，参数：{tc.arguments}")
            last_tool_call = current_call

            # 执行工具（arguments 可能为格式错误的 JSON，容错处理）
            try:
                arguments = json.loads(tc.arguments) if tc.arguments else {}
            except json.JSONDecodeError:
                logger.warning("工具 %s 参数 JSON 解析失败，使用空参数。原文：%s", tc.name, tc.arguments[:200])
                arguments = {}

            if self.tool_registry:
                observation = await self.tool_registry.execute(
                    tc.name, arguments, state.session_id
                )
            else:
                observation = f"[工具 {tc.name} 不可用]"

            iterations.append(ReactIteration(
                iteration=i,
                tool_name=tc.name,
                tool_input=arguments,
                observation=observation,
            ))

            # 委托 provider 构造正确的 tool result 消息格式
            messages.extend(provider.build_tool_result_messages(response, observation))

    # ── 内部 LLM 调用（执行阶段 temperature=0） ────────────────────────────

    async def _call_llm(
        self, system: str, messages: list, tools: list, model_cfg: "ModelConfig"
    ):
        from sunday.agent.providers.base import LLMResponse
        return await LLMClient.call(
            model_cfg, messages,
            system=system,
            tools=tools or None,
            temperature=0,
        )
