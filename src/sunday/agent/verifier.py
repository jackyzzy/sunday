"""Phase 2：Verifier — 结果验证 + 顶层评估"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from pydantic import BaseModel

from sunday.agent.llm_client import LLMClient
from sunday.agent.models import AgentState, Step, StepResult, TeamResult
from sunday.agent.subject_consistency import (
    SubjectConsistencyChecker,
    build_subject_checker,
)
from sunday.agent.tool_usage_audit import (
    ToolUsageAuditChecker,
    build_tool_usage_auditor,
)
from sunday.agent.utils import strip_code_fence

if TYPE_CHECKING:
    from sunday.config import ModelConfig, SundayConfig

logger = logging.getLogger(__name__)


# 主题一致性检查仅对"有实质内容的输出"生效，避免对简短的中间步骤输出做无谓检查。
_SUBJECT_CHECK_MIN_OUTPUT_CHARS = 200


class VerifyResult(BaseModel):
    """Verifier 的判断结果"""

    passed: bool
    reason: str
    should_replan: bool = False


class Verifier:
    """负责验证每步执行结果，并生成最终评估摘要。验证阶段 temperature=0。"""

    def __init__(
        self,
        config: "SundayConfig",
        subject_checker: SubjectConsistencyChecker | None = None,
        tool_usage_auditor: ToolUsageAuditChecker | None = None,
    ) -> None:
        self.config = config
        self._verify_prompt: str | None = None
        self._evaluate_prompt: str | None = None
        # 主题一致性检查器：未显式注入时由工厂根据 config.quality 构造
        self._subject_checker: SubjectConsistencyChecker = (
            subject_checker if subject_checker is not None
            else build_subject_checker(config)
        )
        # 工具使用审计：未注入时由工厂根据 config.quality 构造
        self._tool_usage_auditor: ToolUsageAuditChecker = (
            tool_usage_auditor if tool_usage_auditor is not None
            else build_tool_usage_auditor(config)
        )

    def _get_verify_prompt(self, step_type: str | None = None) -> str:
        """两级优先级返回 verify prompt：

        1. step_type 命名约定：verify_{step_type}.md（若文件存在）
        2. 默认 verify.md（缓存）

        synthesis 步骤的深度质量检查可由 quality.synthesis_quality_check.enabled
        关闭，关闭后回退到默认 verify.md（不会强制 should_replan）。
        """
        # 配置闸门：synthesis 深度检查开关
        if step_type == "synthesis" and not self.config.quality.synthesis_quality_check.enabled:
            step_type = None
        if step_type:
            try:
                return self.config.load_prompt(f"verify_{step_type}")
            except FileNotFoundError:
                pass  # fallback 到默认
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

        prompt = self._get_verify_prompt(step.step_type).format(
            intent=step.intent,
            success_criteria=step.success_criteria,
            output=result.output[:2000],
        )

        try:
            raw = await self._call_llm(prompt, model_cfg)
            vr = self._parse_verify_result(raw)
        except Exception as e:
            logger.warning("check LLM 调用失败（%s），默认通过", e)
            return VerifyResult(passed=True, reason=f"验证调用失败，默认通过：{e}")

        # 仅在基础验证通过且输出有实质内容时，额外做主题一致性检查，兜底"上下文串话"
        if vr.passed and len(result.output) >= _SUBJECT_CHECK_MIN_OUTPUT_CHARS:
            subjects = self._extract_subjects(state)
            if subjects:
                sc = await self._subject_checker.check(result.output, subjects)
                if not sc.consistent:
                    logger.warning(
                        "步骤 %s 主题不一致：%s（主题：%s）",
                        step.id, sc.reason, subjects,
                    )
                    return VerifyResult(
                        passed=False,
                        reason=f"主题不一致：{sc.reason}",
                        should_replan=True,
                    )

        # 第三道闸门：工具使用审计 — realtime 步骤必须真的联网或带未联网标签
        if vr.passed:
            audit = await self._tool_usage_auditor.check(
                step, result.react_iterations, result.output,
            )
            if not audit.passed:
                logger.warning(
                    "步骤 %s 工具使用审计失败：%s", step.id, audit.reason,
                )
                return VerifyResult(
                    passed=False,
                    reason=f"工具使用审计失败：{audit.reason}",
                    should_replan=True,
                )
        return vr

    @staticmethod
    def _extract_subjects(state: AgentState) -> list[str]:
        """从 AgentState 收集"当前任务主题"线索供主题一致性检查使用。

        来源（按优先级）：
          1. 当前 task（作为主题句）
          2. plan.goal（如已规划）
          3. session_thread.key_entities（跨轮锚点）
        空字符串/重复条目会被过滤。
        """
        subjects: list[str] = []
        if state.task and state.task.strip():
            subjects.append(state.task.strip())
        if state.plan and state.plan.goal.strip() and state.plan.goal not in subjects:
            subjects.append(state.plan.goal.strip())
        if state.session_thread and state.session_thread.key_entities:
            for e in state.session_thread.key_entities:
                if e and e not in subjects:
                    subjects.append(e)
        return subjects

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
