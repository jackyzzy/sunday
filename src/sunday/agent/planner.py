"""Phase 2/3：Planner — THINK + PLAN + DECOMPOSE（支持注入 system_prompt）"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from sunday.agent.llm_client import LLMClient
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
        api_key = self.settings.get_api_key(model_cfg.provider, model_cfg.api_key_env)

        budget = THINKING_BUDGET.get(state.thinking_level, 4096)

        task_context = f"{self.system_prompt}\n\n---\n\n" if self.system_prompt else ""
        prompt = task_context + _PLAN_PROMPT.format(task=state.task)

        messages = [{"role": "user", "content": prompt}]
        data = await LLMClient.call(
            model_cfg, api_key, messages,
            max_tokens=model_cfg.max_tokens,
            temperature=0.3,
            thinking_budget=budget,
        )

        thinking_text = data.get("thinking")
        plan_text = LLMClient.extract_text(data)
        plan = self._parse_plan(plan_text, thinking=thinking_text)
        logger.info("规划完成，共 %d 个步骤", len(plan.steps))
        return plan

    async def replan(self, failed_step: Step, result_output: str, state: AgentState) -> list[Step]:
        """局部重规划：替换 failed_step 之后所有未执行步骤。"""
        cfg = self.settings.sunday
        model_cfg = cfg.model
        api_key = self.settings.get_api_key(model_cfg.provider, model_cfg.api_key_env)

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

        raw = await LLMClient.call_text(
            model_cfg, api_key, prompt, max_tokens=4096, temperature=0.3
        )
        plan_text = self._strip_code_fence(raw)
        if not plan_text:
            logger.warning("replan LLM 响应为空，将返回空步骤列表")
            return []
        data = json.loads(plan_text)
        return [Step(**s) for s in data.get("steps", [])]

    @staticmethod
    def _strip_code_fence(text: str) -> str:
        """去除 markdown 代码块包装（```json...``` 或 ```...```）。"""
        text = text.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            inner = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
            text = "\n".join(inner).strip()
        return text

    @staticmethod
    def _parse_plan(text: str, thinking: str | None = None) -> Plan:
        """解析 JSON 格式的 Plan，容错处理 markdown 代码块。"""
        text = Planner._strip_code_fence(text)
        data = json.loads(text)
        steps = [Step(**s) for s in data.get("steps", [])]
        return Plan(goal=data.get("goal", ""), thinking=thinking, steps=steps)
