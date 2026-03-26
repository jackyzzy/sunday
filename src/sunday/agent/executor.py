"""Phase 2：Executor — ReAct 执行循环"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Protocol

import httpx

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


_STEP_SYSTEM_PROMPT = """你是一个严格执行任务的 AI 助手。

当前步骤：{intent}
期望输出：{expected_output}
成功标准：{success_criteria}

执行原则：
- 直接执行，不解释过程
- 如果有可用工具，优先使用工具完成任务
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
        api_key = self.settings.get_api_key(model_cfg.provider)

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

            tool_name, arguments_str = tool_call
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

            # 将工具调用和结果追加到消息历史
            messages.append({"role": "assistant", "content": self._extract_text(response_data)})
            messages.append({"role": "user", "content": f"工具 {tool_name} 返回：{observation}"})

        raise MaxStepsError(
            f"步骤 {step.id} 超出最大迭代次数 {max_steps}，强制终止"
        )

    # ── 内部 LLM 调用（执行阶段 temperature=0） ────────────────────────────

    async def _call_llm(
        self, system: str, messages: list, tools: list, model_cfg, api_key: str
    ) -> dict:
        if model_cfg.provider == "anthropic":
            return await self._call_anthropic(system, messages, tools, model_cfg, api_key)
        elif model_cfg.provider == "openai":
            return await self._call_openai(system, messages, tools, model_cfg, api_key)
        else:
            raise ValueError(f"Executor 暂不支持 provider: {model_cfg.provider}")

    async def _call_anthropic(
        self, system: str, messages: list, tools: list, model_cfg, api_key: str
    ) -> dict:
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        body: dict = {
            "model": model_cfg.id,
            "max_tokens": model_cfg.max_tokens,
            "temperature": 0,
            "system": system,
            "messages": messages,
        }
        if tools:
            body["tools"] = tools

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages", headers=headers, json=body
            )
            resp.raise_for_status()
            data = resp.json()

        # 统一格式：判断是否有工具调用
        tool_use_blocks = [b for b in data.get("content", []) if b.get("type") == "tool_use"]
        if tool_use_blocks:
            tb = tool_use_blocks[0]
            data["tool_calls"] = [{
                "name": tb["name"],
                "arguments": json.dumps(tb.get("input", {}), ensure_ascii=False),
            }]
        return data

    async def _call_openai(
        self, system: str, messages: list, tools: list, model_cfg, api_key: str
    ) -> dict:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        full_messages = [{"role": "system", "content": system}] + messages
        body: dict = {
            "model": model_cfg.id,
            "max_tokens": model_cfg.max_tokens,
            "temperature": 0,
            "messages": full_messages,
        }
        if tools:
            body["tools"] = tools

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions", headers=headers, json=body
            )
            resp.raise_for_status()
            data = resp.json()

        choice = data["choices"][0]
        msg = choice["message"]
        result: dict = {
            "finish_reason": choice["finish_reason"],
            "content": [{"type": "text", "text": msg.get("content") or ""}],
        }
        if msg.get("tool_calls"):
            tc = msg["tool_calls"][0]
            result["tool_calls"] = [{
                "name": tc["function"]["name"],
                "arguments": tc["function"].get("arguments", "{}"),
            }]
        return result

    @staticmethod
    def _extract_text(data: dict) -> str:
        for block in data.get("content", []):
            if isinstance(block, dict) and block.get("type") == "text":
                return block.get("text", "")
        # OpenAI 格式兜底
        if "choices" in data:
            return data["choices"][0]["message"].get("content") or ""
        return ""

    @staticmethod
    def _extract_tool_call(data: dict) -> tuple[str, str] | None:
        tool_calls = data.get("tool_calls")
        if not tool_calls:
            return None
        tc = tool_calls[0]
        return tc.get("name", ""), tc.get("arguments", "{}")
