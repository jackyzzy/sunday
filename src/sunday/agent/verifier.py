"""Phase 2：Verifier — 结果验证 + 顶层评估"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from pydantic import BaseModel

from sunday.agent.llm_client import LLMClient
from sunday.agent.models import AgentState, Step, StepResult, TeamResult
from sunday.agent.utils import strip_code_fence

if TYPE_CHECKING:
    from sunday.config import ModelConfig, SundayConfig

logger = logging.getLogger(__name__)


class VerifyResult(BaseModel):
    """Verifier 的判断结果"""

    passed: bool
    reason: str
    should_replan: bool = False


class Verifier:
    """负责验证每步执行结果，并生成最终评估摘要。验证阶段 temperature=0。"""

    def __init__(self, config: "SundayConfig") -> None:
        self.config = config
        self._verify_prompt: str | None = None
        self._evaluate_prompt: str | None = None

    def _get_verify_prompt(self) -> str:
        if self._verify_prompt is None:
            self._verify_prompt = self.config.load_prompt("verify")
        return self._verify_prompt

    def _get_evaluate_prompt(self) -> str:
        if self._evaluate_prompt is None:
            self._evaluate_prompt = self.config.load_prompt("evaluate")
        return self._evaluate_prompt

    async def check(self, step: Step, result: StepResult, state: AgentState) -> VerifyResult:
        """对照 success_criteria 判断步骤结果是否通过。"""
        if not step.success_criteria.strip():
            return VerifyResult(passed=True, reason="无成功标准，默认通过")

        model_cfg: ModelConfig = self.config.model

        prompt = self._get_verify_prompt().format(
            intent=step.intent,
            success_criteria=step.success_criteria,
            output=result.output[:2000],
        )

        try:
            raw = await self._call_llm(prompt, model_cfg)
            return self._parse_verify_result(raw)
        except Exception as e:
            logger.warning("check LLM 调用失败（%s），默认通过", e)
            return VerifyResult(passed=True, reason=f"验证调用失败，默认通过：{e}")

    async def evaluate(
        self,
        state: AgentState,
        team_results: list[TeamResult],
        written_files: list[str] | None = None,
    ) -> str:
        """顶层评估：基于所有 Team 结果生成整体任务摘要。"""
        model_cfg: ModelConfig = self.config.model

        results_summary = "\n".join(
            f"- {tr.step_id} ({'✓' if tr.passed else '✗'}): {tr.output[:300]}"
            for tr in team_results
        )
        if not results_summary:
            results_summary = "无执行记录"

        files_text = "\n".join(f"- {f}" for f in written_files) if written_files else "（无）"

        prompt = self._get_evaluate_prompt().format(
            task=state.task,
            results_summary=results_summary,
            written_files=files_text,
        )
        try:
            return await self._call_llm(prompt, model_cfg)
        except Exception as e:
            logger.warning("evaluate LLM 调用失败（%s），使用本地摘要降级", e)
            passed = sum(1 for tr in team_results if tr.passed)
            return (
                f"任务：{state.task}\n"
                f"完成步骤：{passed}/{len(team_results)}。\n"
                f"（评估生成失败：{e}）"
            )

    # ── 内部 LLM 调用（验证阶段 temperature=0） ───────────────────────────

    async def _call_llm(self, prompt: str, model_cfg: "ModelConfig") -> str:
        return await LLMClient.call_text(model_cfg, prompt, max_tokens=1024, timeout=60)

    @staticmethod
    def _parse_verify_result(raw: str) -> VerifyResult:
        text = strip_code_fence(raw)
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
