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
        model_cfg: ModelConfig = self.config.model
        max_steps = self.config.reasoning.max_steps
        api_key = model_cfg.get_api_key()

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
                response_data = await self._call_llm(system, messages, [], model_cfg, api_key)
                output = self._extract_text(response_data)
                logger.warning("步骤 %s 达到最大迭代次数 %d，已强制收尾输出", step.id, max_steps)
                return StepResult(
                    step_id=step.id,
                    status=StepStatus.DONE,
                    output=output,
                    react_iterations=iterations,
                )

            response_data = await self._call_llm(system, messages, tools, model_cfg, api_key)
            finish_reason = response_data.get("stop_reason") or response_data.get(
                "finish_reason", "stop"
            )

            # 模型判断任务完成
            if finish_reason in ("stop", "end_turn") or not response_data.get("tool_calls"):
                output = self._extract_text(response_data)
                return StepResult(
                    step_id=step.id,
                    status=StepStatus.DONE,
                    output=output,
                    react_iterations=iterations,
                )

            # 有工具调用
            tool_call = self._extract_tool_call(response_data)
            if tool_call is None:
                output = self._extract_text(response_data)
                return StepResult(
                    step_id=step.id,
                    status=StepStatus.DONE,
                    output=output,
                    react_iterations=iterations,
                )

            tool_name, arguments_str, tool_call_id = tool_call
            current_call = _LastToolCall(tool_name, arguments_str)
            if current_call == last_tool_call:
                raise RepetitionError(f"连续重复调用工具 {tool_name}，参数：{arguments_str}")
            last_tool_call = current_call

            # 执行工具（arguments_str 可能为格式错误的 JSON，容错处理）
            try:
                arguments = json.loads(arguments_str) if arguments_str else {}
            except json.JSONDecodeError:
                logger.warning("工具 %s 参数 JSON 解析失败，使用空参数。原文：%s", tool_name, arguments_str[:200])
                arguments = {}

            if self.tool_registry:
                observation = await self.tool_registry.execute(
                    tool_name, arguments, state.session_id
                )
            else:
                observation = f"[工具 {tool_name} 不可用]"

            iterations.append(ReactIteration(
                iteration=i,
                tool_name=tool_name,
                tool_input=arguments,
                observation=observation,
            ))

            # 按 provider 使用正确的消息格式追加工具调用和结果
            if model_cfg.provider == "anthropic":
                messages.append({
                    "role": "assistant",
                    "content": response_data.get("content", []),
                })
                messages.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": tool_call_id,
                        "content": observation,
                    }],
                })
            else:
                # OpenAI 兼容（DeepSeek、Qwen 等）
                # content 用 "" 而非 None：部分 provider（DeepSeek）拒绝 null content
                messages.append({
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "id": tool_call_id,
                        "type": "function",
                        "function": {"name": tool_name, "arguments": arguments_str},
                    }],
                })
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": observation,
                })

    # ── 内部 LLM 调用（执行阶段 temperature=0） ────────────────────────────

    async def _call_llm(
        self, system: str, messages: list, tools: list, model_cfg: "ModelConfig", api_key: str
    ) -> dict:
        return await LLMClient.call(
            model_cfg, api_key, messages,
            system=system,
            tools=tools or None,
            temperature=0,
        )

    @staticmethod
    def _extract_text(data: dict) -> str:
        return LLMClient.extract_text(data)

    @staticmethod
    def _extract_tool_call(data: dict) -> tuple[str, str, str] | None:
        return LLMClient.extract_tool_call(data)
