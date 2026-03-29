"""Phase 2：Executor — ReAct 执行循环"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Protocol

from sunday.agent.llm_client import LLMClient
from sunday.agent.models import (
    AgentState,
    ReactIteration,
    Step,
    StepResult,
    StepStatus,
)

if TYPE_CHECKING:
    from sunday.config import Settings

logger = logging.getLogger(__name__)


class MaxStepsError(Exception):
    """ReAct 循环超出最大步骤数"""


class RepetitionError(Exception):
    """连续重复相同工具调用"""


class ToolRegistryProtocol(Protocol):
    """Executor 依赖的工具注册表接口（避免循环 import）"""

    def get_schemas(self) -> list[dict]: ...
    async def execute(self, tool_name: str, arguments: dict, session_id: str) -> str: ...


_STEP_SYSTEM_PROMPT = """你是 Sunday，一个运行在用户本地电脑上的个人 AI 智能体助手。\
你不是 Claude、GPT 或任何其他 AI 产品，你就是 Sunday。

当前步骤：{intent}
期望输出：{expected_output}
成功标准：{success_criteria}

执行原则：
- 直接执行，不解释过程
- 如果有可用工具，优先使用工具完成任务
- 工具调用有次数限制，信息足够时请直接输出结果，不要过度收集
- 任务完成后直接停止，不要继续调用工具
- 如果无需工具即可完成，直接输出结果
"""


class Executor:
    """负责 ReAct 执行循环。执行阶段 temperature=0。"""

    def __init__(
        self,
        settings: "Settings",
        tool_registry: ToolRegistryProtocol | None = None,
    ) -> None:
        self.settings = settings
        self.tool_registry = tool_registry

    async def run(self, step: Step, state: AgentState) -> StepResult:
        """执行单个步骤，返回 StepResult。"""
        cfg = self.settings.sunday
        model_cfg = cfg.model
        max_steps = cfg.reasoning.max_steps
        api_key = self.settings.get_api_key(model_cfg.provider, model_cfg.api_key_env)

        system = _STEP_SYSTEM_PROMPT.format(
            intent=step.intent,
            expected_output=step.expected_output,
            success_criteria=step.success_criteria,
        )
        messages = [{"role": "user", "content": step.intent}]
        tools = self.tool_registry.get_schemas() if self.tool_registry else []
        iterations: list[ReactIteration] = []
        last_tool_call: tuple[str, str] | None = None

        for i in range(max_steps):
            # 最后一次机会：禁用工具，强制模型整合已有信息直接输出
            if i == max_steps - 1:
                messages.append({
                    "role": "user",
                    "content": "你已用完工具调用次数。请根据以上收集到的信息，直接输出最终结果，不要再调用工具。",
                })
                response_data = await self._call_llm(
                    system, messages, [], model_cfg, api_key
                )
                output = self._extract_text(response_data)
                logger.warning(
                    "步骤 %s 达到最大迭代次数 %d，已强制收尾输出", step.id, max_steps
                )
                return StepResult(
                    step_id=step.id,
                    status=StepStatus.DONE,
                    output=output,
                    react_iterations=iterations,
                )

            response_data = await self._call_llm(
                system, messages, tools, model_cfg, api_key
            )
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
            call_key = (tool_name, arguments_str)
            if call_key == last_tool_call:
                raise RepetitionError(f"连续重复调用工具 {tool_name}，参数：{arguments_str}")
            last_tool_call = call_key

            # 执行工具
            arguments = json.loads(arguments_str) if arguments_str else {}
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
                # Anthropic：assistant 消息含原始 content blocks（包含 tool_use），
                # 工具结果用 tool_result block
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
                # OpenAI 兼容（DeepSeek、Qwen 等）：assistant 消息含 tool_calls 数组，
                # 工具结果用 role="tool" + tool_call_id
                messages.append({
                    "role": "assistant",
                    "content": None,
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
        self, system: str, messages: list, tools: list, model_cfg, api_key: str
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
