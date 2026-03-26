"""Phase 2/3：Planner — THINK + PLAN + DECOMPOSE（支持注入 system_prompt）"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

import httpx

from sunday.agent.models import AgentState, Plan, Step, StepStatus, ThinkingLevel

if TYPE_CHECKING:
    from sunday.config import Settings

logger = logging.getLogger(__name__)

# thinking_level → budget_tokens
THINKING_BUDGET: dict[ThinkingLevel, int] = {
    ThinkingLevel.OFF: 0,
    ThinkingLevel.MINIMAL: 512,
    ThinkingLevel.LOW: 1024,
    ThinkingLevel.MEDIUM: 4096,
    ThinkingLevel.HIGH: 8192,
}

_PLAN_PROMPT = """你是一个任务规划专家。请根据以下任务，制定清晰的执行计划。

任务：{task}

要求：
- 将任务分解为 1~6 个可独立执行的步骤
- 每步需明确意图、期望输入输出、成功判断标准
- 步骤之间的依赖关系用 depends_on 表达

请以 JSON 格式输出，结构如下：
{{
  "goal": "任务总目标",
  "steps": [
    {{
      "id": "step_1",
      "intent": "这步要做什么",
      "expected_input": "输入是什么",
      "expected_output": "输出是什么",
      "success_criteria": "如何判断成功",
      "depends_on": []
    }}
  ]
}}

只输出 JSON，不要任何额外说明。"""

_REPLAN_PROMPT = """执行计划中的一个步骤失败了，需要局部重规划。

失败步骤：{failed_step_intent}
失败原因：{reason}
已完成步骤结果摘要：{completed_summary}
原始任务目标：{goal}
剩余未完成步骤：{remaining_steps}

请重新规划从失败步骤开始的后续步骤，输出替代方案。

以 JSON 格式输出替代步骤列表：
{{
  "steps": [
    {{
      "id": "step_X",
      "intent": "...",
      "expected_input": "...",
      "expected_output": "...",
      "success_criteria": "...",
      "depends_on": []
    }}
  ]
}}

只输出 JSON，不要任何额外说明。"""


class Planner:
    """负责 THINK + PLAN + DECOMPOSE 阶段。

    规划阶段使用低温度（0.3），禁止调用外部工具。
    """

    def __init__(self, settings: "Settings", system_prompt: str = "") -> None:
        self.settings = settings
        self.system_prompt = system_prompt  # 由 ContextBuilder 注入

    async def think_and_plan(self, state: AgentState) -> Plan:
        """根据任务和上下文生成结构化 Plan。"""
        cfg = self.settings.sunday
        model_cfg = cfg.model
        provider = model_cfg.provider
        model_id = model_cfg.id
        api_key = self.settings.get_api_key(provider)

        thinking_level = state.thinking_level
        budget = THINKING_BUDGET.get(thinking_level, 4096)

        # 将 system_prompt（L0 上下文）追加到规划提示前
        task_context = f"{self.system_prompt}\n\n---\n\n" if self.system_prompt else ""
        prompt = task_context + _PLAN_PROMPT.format(task=state.task)

        if provider == "anthropic":
            raw = await self._call_anthropic(
                prompt, model_id, api_key, model_cfg.max_tokens, budget
            )
        elif provider == "openai":
            raw = await self._call_openai(prompt, model_id, api_key, model_cfg.max_tokens)
        else:
            raise ValueError(f"Planner 暂不支持 provider: {provider}")

        thinking_text, plan_text = self._split_thinking(raw)
        plan = self._parse_plan(plan_text, thinking=thinking_text)
        logger.info("规划完成，共 %d 个步骤", len(plan.steps))
        return plan

    async def replan(self, failed_step: Step, result_output: str, state: AgentState) -> list[Step]:
        """局部重规划：替换 failed_step 之后所有未执行步骤。"""
        cfg = self.settings.sunday
        model_cfg = cfg.model
        api_key = self.settings.get_api_key(model_cfg.provider)

        completed = [r for r in state.step_results if r.status == StepStatus.DONE]
        completed_summary = "; ".join(f"{r.step_id}: {r.output[:100]}" for r in completed)

        remaining = []
        found = False
        for step in (state.plan.steps if state.plan else []):
            if step.id == failed_step.id:
                found = True
            if found:
                remaining.append(step.intent)

        prompt = _REPLAN_PROMPT.format(
            failed_step_intent=failed_step.intent,
            reason=result_output[:500],
            completed_summary=completed_summary or "无",
            goal=state.plan.goal if state.plan else state.task,
            remaining_steps=json.dumps(remaining, ensure_ascii=False),
        )

        if model_cfg.provider == "anthropic":
            raw = await self._call_anthropic(prompt, model_cfg.id, api_key, 4096, budget=0)
        elif model_cfg.provider == "openai":
            raw = await self._call_openai(prompt, model_cfg.id, api_key, 4096)
        else:
            raise ValueError(f"Planner 暂不支持 provider: {model_cfg.provider}")

        _, plan_text = self._split_thinking(raw)
        data = json.loads(plan_text.strip())
        return [Step(**s) for s in data.get("steps", [])]

    # ── 内部 LLM 调用（规划阶段 temperature=0.3，不调用工具） ──────────────

    async def _call_anthropic(
        self, prompt: str, model_id: str, api_key: str, max_tokens: int, budget: int
    ) -> str:
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        body: dict = {
            "model": model_id,
            "max_tokens": max_tokens,
            "temperature": 0.3,
            "messages": [{"role": "user", "content": prompt}],
        }
        if budget > 0:
            body["thinking"] = {"type": "enabled", "budget_tokens": budget}

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages", headers=headers, json=body
            )
            resp.raise_for_status()
            data = resp.json()

        parts = []
        for block in data.get("content", []):
            if block.get("type") == "thinking":
                parts.append(f"<thinking>{block.get('thinking', '')}</thinking>")
            elif block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n".join(parts)

    async def _call_openai(
        self, prompt: str, model_id: str, api_key: str, max_tokens: int
    ) -> str:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": model_id,
            "max_tokens": max_tokens,
            "temperature": 0.3,
            "messages": [{"role": "user", "content": prompt}],
        }
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions", headers=headers, json=body
            )
            resp.raise_for_status()
            data = resp.json()
        return data["choices"][0]["message"]["content"]

    @staticmethod
    def _split_thinking(raw: str) -> tuple[str | None, str]:
        """将 <thinking>...</thinking> 从输出中分离。"""
        if "<thinking>" in raw and "</thinking>" in raw:
            start = raw.index("<thinking>") + len("<thinking>")
            end = raw.index("</thinking>")
            thinking = raw[start:end].strip()
            rest = raw[raw.index("</thinking>") + len("</thinking>"):].strip()
            return thinking, rest
        return None, raw

    @staticmethod
    def _parse_plan(text: str, thinking: str | None = None) -> Plan:
        """解析 JSON 格式的 Plan，容错处理 markdown 代码块。"""
        text = text.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        data = json.loads(text)
        steps = [Step(**s) for s in data.get("steps", [])]
        return Plan(goal=data.get("goal", ""), thinking=thinking, steps=steps)
