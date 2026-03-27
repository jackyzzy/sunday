"""Phase 2：Verifier — 结果验证 + 摘要生成"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

import httpx
from pydantic import BaseModel

from sunday.agent.models import AgentState, Step, StepResult

if TYPE_CHECKING:
    from sunday.config import Settings

logger = logging.getLogger(__name__)

_VERIFY_PROMPT = """你是一个严格的任务验证器。

步骤意图：{intent}
成功标准：{success_criteria}
实际输出：{output}

请判断实际输出是否满足成功标准。

以 JSON 格式输出：
{{
  "passed": true/false,
  "reason": "判断理由（一句话）",
  "should_replan": true/false
}}

规则：
- passed=true 仅当实际输出完全满足成功标准
- should_replan=true 表示换个方案可能成功
- should_replan=false 表示该步骤本身无意义或任务已自然完成

只输出 JSON，不要任何额外说明。"""

_SUMMARIZE_PROMPT = """请根据以下任务执行结果，生成一份简洁的结果摘要。

任务：{task}

执行步骤和结果：
{steps_summary}

要求：
- 说明是否完成了任务
- 列出关键输出或结论
- 如有失败步骤，简要说明原因
- 长度控制在 3~5 句话"""


class VerifyResult(BaseModel):
    """Verifier 的判断结果"""

    passed: bool
    reason: str
    should_replan: bool = False


class Verifier:
    """负责验证每步执行结果，并生成最终摘要。验证阶段 temperature=0。"""

    def __init__(self, settings: "Settings") -> None:
        self.settings = settings

    async def check(self, step: Step, result: StepResult, state: AgentState) -> VerifyResult:
        """对照 success_criteria 判断步骤结果是否通过。"""
        if not step.success_criteria.strip():
            # 无验证标准时默认通过
            return VerifyResult(passed=True, reason="无成功标准，默认通过")

        cfg = self.settings.sunday
        model_cfg = cfg.model
        api_key = self.settings.get_api_key(model_cfg.provider, model_cfg.api_key_env)

        prompt = _VERIFY_PROMPT.format(
            intent=step.intent,
            success_criteria=step.success_criteria,
            output=result.output[:2000],
        )

        raw = await self._call_llm(prompt, model_cfg, api_key)
        return self._parse_verify_result(raw)

    async def summarize(self, state: AgentState) -> str:
        """生成最终结果摘要。"""
        cfg = self.settings.sunday
        model_cfg = cfg.model
        api_key = self.settings.get_api_key(model_cfg.provider, model_cfg.api_key_env)

        steps_summary = "\n".join(
            f"- 步骤 {r.step_id}（{r.status.value}）：{r.output[:200]}"
            for r in state.step_results
        )
        if not steps_summary:
            steps_summary = "无执行记录"

        prompt = _SUMMARIZE_PROMPT.format(
            task=state.task,
            steps_summary=steps_summary,
        )
        return await self._call_llm(prompt, model_cfg, api_key)

    # ── 内部 LLM 调用（验证阶段 temperature=0） ───────────────────────────

    async def _call_llm(self, prompt: str, model_cfg, api_key: str) -> str:
        if model_cfg.provider == "anthropic":
            return await self._call_anthropic(prompt, model_cfg, api_key)
        elif model_cfg.provider == "openai":
            return await self._call_openai(prompt, model_cfg, api_key)
        else:
            raise ValueError(f"Verifier 暂不支持 provider: {model_cfg.provider}")

    async def _call_anthropic(self, prompt: str, model_cfg, api_key: str) -> str:
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        body = {
            "model": model_cfg.id,
            "max_tokens": 1024,
            "temperature": 0,
            "messages": [{"role": "user", "content": prompt}],
        }
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages", headers=headers, json=body
            )
            resp.raise_for_status()
            data = resp.json()

        texts = [b["text"] for b in data.get("content", []) if b.get("type") == "text"]
        return "\n".join(texts)

    async def _call_openai(self, prompt: str, model_cfg, api_key: str) -> str:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": model_cfg.id,
            "max_tokens": 1024,
            "temperature": 0,
            "messages": [{"role": "user", "content": prompt}],
        }
        base = (model_cfg.base_url or "https://api.openai.com/v1").rstrip("/")
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{base}/chat/completions", headers=headers, json=body
            )
            resp.raise_for_status()
            data = resp.json()
        return data["choices"][0]["message"]["content"]

    @staticmethod
    def _parse_verify_result(raw: str) -> VerifyResult:
        text = raw.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        try:
            data = json.loads(text)
            return VerifyResult(
                passed=bool(data.get("passed", False)),
                reason=str(data.get("reason", "")),
                should_replan=bool(data.get("should_replan", False)),
            )
        except (json.JSONDecodeError, KeyError):
            # 解析失败时保守判断为通过，避免无限重规划
            logger.warning("Verifier 响应解析失败，原文：%s", raw[:200])
            return VerifyResult(passed=True, reason=f"解析失败，原文：{raw[:100]}")
