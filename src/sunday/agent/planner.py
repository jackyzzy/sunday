"""Phase 2/3：Planner — THINK + (opt-in) FACT_CHECK + REALTIME_HINTS + PLAN + DECOMPOSE。

阶段调用链（默认 fact_check.enabled=false、realtime_hints.enabled=true）：

    THINK（LLM-only，识别不确定断言）
        ↓
    [opt-in] FACT_CHECK（白名单工具核实，默认关）
        ↓
    REALTIME_HINTS（纯函数，复用 think 的 claims）
        ↓
    PLAN（注入 facts + hints，要求 LLM 标注每步 requires_realtime_data）
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from sunday.agent.fact_check import ToolRegistryLike, VerifiedFact, maybe_fact_check
from sunday.agent.llm_client import LLMClient
from sunday.agent.models import THINKING_BUDGET, AgentState, Plan, Step
from sunday.agent.realtime_hints import classify, format_for_plan_prompt
from sunday.agent.utils import EmitCallable, noop_emit, strip_code_fence
from sunday.service.protocol import EventType

if TYPE_CHECKING:
    from sunday.config import ModelConfig, SundayConfig
    from sunday.memory.models import RuntimeRules
    from sunday.templates.loader import TemplateLoader

logger = logging.getLogger(__name__)


def _truncate_head_tail(content: str, limit: int) -> str:
    """长文本保留首尾各半，避免主题句丢失在中间。"""
    if len(content) <= limit:
        return content
    half = max(limit // 2, 200)
    return f"{content[:half]}\n...[中间省略]...\n{content[-half:]}"


class Planner:
    """负责 THINK + PLAN + DECOMPOSE 阶段。

    规划阶段使用低温度（0.3），禁止调用外部工具。
    """

    def __init__(
        self,
        config: "SundayConfig",
        system_prompt: str = "",
        runtime_rules: "RuntimeRules | None" = None,
        templates: "TemplateLoader | None" = None,
    ) -> None:
        self.config = config
        self.system_prompt = system_prompt  # 由 ContextBuilder 注入
        self._runtime_rules = runtime_rules  # 由 ReactAgent 通过 client.workspace 预加载
        self._templates = templates  # 由 ReactAgent 注入，用于 task_type 模板查询
        self._plan_prompt: str | None = None
        self._replan_prompt: str | None = None
        self._sub_replan_prompt: str | None = None
        self._think_prompt: str | None = None

    def _get_plan_prompt(self) -> str:
        if self._plan_prompt is None:
            self._plan_prompt = self.config.load_prompt("plan")
        return self._plan_prompt

    def _get_replan_prompt(self) -> str:
        if self._replan_prompt is None:
            self._replan_prompt = self.config.load_prompt("replan")
        return self._replan_prompt

    def _get_sub_replan_prompt(self) -> str:
        if self._sub_replan_prompt is None:
            self._sub_replan_prompt = self.config.load_prompt("sub_replan")
        return self._sub_replan_prompt

    def _get_think_prompt(self) -> str:
        if self._think_prompt is None:
            self._think_prompt = self.config.load_prompt("think")
        return self._think_prompt

    async def think_and_plan(
        self,
        state: AgentState,
        plan_prompt: str | None = None,
        tool_registry: ToolRegistryLike | None = None,
        emit: EmitCallable | None = None,
    ) -> Plan:
        """根据任务和上下文生成结构化 Plan。

        若 config.quality.fact_check.enabled，并且有 tool_registry 可用，
        会在正式生成 Plan 前插入一次 FACT_CHECK 子阶段：
            THINK（识别可疑事实）→ maybe_fact_check（白名单工具核实）→ PLAN（注入 verified_facts）
        否则行为与原来完全一致。
        """
        model_cfg: ModelConfig = self.config.model
        budget = THINKING_BUDGET.get(state.thinking_level, 4096)
        emit = emit or noop_emit

        task_context = f"{self.system_prompt}\n\n---\n\n" if self.system_prompt else ""

        # 注入会话主线（跨轮主题锚点）
        thread_context = ""
        thread = state.session_thread
        if thread and (thread.summary or thread.key_entities):
            entities = "、".join(thread.key_entities) if thread.key_entities else "（无）"
            summary = thread.summary or "（尚未形成明确主线）"
            thread_context = (
                f"# 会话主线\n"
                f"主题概括：{summary}\n"
                f"关键实体：{entities}\n\n---\n\n"
            )

        # 注入对话历史（让 Planner 区分新任务/续任务）
        history_context = ""
        if state.history:
            limit = self.config.memory.history_max_chars
            history_lines = "\n".join(
                f"{m.role}: {_truncate_head_tail(m.content, limit)}"
                for m in state.history[-10:]
            )
            history_context = f"# 对话历史\n{history_lines}\n\n---\n\n"

        # ── THINK 阶段：识别不确定断言（LLM-only，零外部工具）──
        # 解耦：think 由 realtime_hints OR fact_check 任一启用即触发，结果共享。
        quality = self.config.quality
        claims: list[str] = []
        if quality.realtime_hints.enabled or quality.fact_check.enabled:
            claims = await self._identify_uncertain_claims(
                state, thread_context, history_context, model_cfg
            )

        # ── FACT_CHECK 子阶段（opt-in，默认关）──
        verified_facts: list[VerifiedFact] = []
        if quality.fact_check.enabled and claims:
            await emit(state.session_id, EventType.PLAN_FACT_CHECK, {
                "phase": "start",
                "claims": claims,
            })
            verified_facts = await maybe_fact_check(
                claims, self.config, tool_registry, state.session_id
            )
            await emit(state.session_id, EventType.PLAN_FACT_CHECK, {
                "phase": "done",
                "facts": [
                    {"claim": f.claim, "tool": f.tool, "finding": f.finding[:300]}
                    for f in verified_facts
                ],
            })

        facts_context = ""
        if verified_facts:
            lines = "\n".join(
                f"- {f.claim}\n  ↳ 来源[{f.tool}]：{f.finding[:300]}"
                for f in verified_facts
            )
            facts_context = (
                "# 已核实事实（FACT_CHECK 子阶段）\n"
                "规划时如与历史记忆冲突，以此为准：\n"
                f"{lines}\n\n---\n\n"
            )

        # ── REALTIME_HINTS：合成"实时数据需求"提示注入 plan prompt ──
        hints_context = ""
        if quality.realtime_hints.enabled:
            hints = classify(state.task, claims=claims, rules=self._runtime_rules)
            if hints.has_signal:
                await emit(state.session_id, EventType.PLAN_REALTIME_HINTS, {
                    "phase": "done",
                    "task_keywords": hints.task_keywords,
                    "claim_entities": hints.claim_entities,
                })
            hints_context = format_for_plan_prompt(hints)

        # 任务类型清单（来自 TemplateLoader），帮 LLM 选择 task_type
        catalog_context = self._build_task_type_catalog_context()

        active_prompt = plan_prompt if plan_prompt is not None else self._get_plan_prompt()
        prompt = (
            task_context + thread_context + history_context + facts_context + hints_context
            + catalog_context + active_prompt.format(task=state.task)
        )

        response = await LLMClient.call(
            model_cfg,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=model_cfg.max_tokens,
            temperature=0.3,
            thinking_budget=budget,
        )

        try:
            plan = self._parse_plan(response.text, thinking=response.thinking)
        except ValueError as _parse_err:
            # DeepSeek 偶尔输出缺逗号等格式错误的 JSON，让模型自修正后重试一次
            logger.warning("规划 JSON 解析失败（%s），发回模型修正后重试", _parse_err)
            fix_resp = await LLMClient.call(
                model_cfg,
                messages=[{"role": "user", "content":
                    f"以下 JSON 格式有误，请修正并只返回合法 JSON（不要有任何额外文字或代码块）：\n\n{response.text[:3000]}"}],
                max_tokens=model_cfg.max_tokens,
                temperature=0,
                thinking_budget=0,
            )
            plan = self._parse_plan(fix_resp.text, thinking=response.thinking)

        # 模板驱动：根据 task_type 模板的 synthesis 配置注入综合整合步骤
        self._maybe_inject_synthesis_step(plan)

        logger.info("规划完成，共 %d 个步骤", len(plan.steps))
        return plan

    def _maybe_inject_synthesis_step(self, plan: Plan) -> None:
        """按模板配置自动注入 synthesis 步骤。

        条件：plan.task_type 对应的模板存在且 synthesis.enabled=true。
        synthesis_document_name 缺失时用模板的 document_name_hint 作 fallback。
        模板的 plan_guidance 作为任务类型专属指导附加到 intent。
        """
        if not plan.task_type or not self._templates:
            return
        tpl = self._templates.get_task_template(plan.task_type)
        if not tpl or not tpl.synthesis.enabled:
            return

        # 文档命名 fallback：LLM 未给名 → 模板提示 → task_type 默认
        doc_name = plan.synthesis_document_name
        if not doc_name:
            doc_name = tpl.synthesis.document_name_hint or f"{plan.task_type}_报告.md"
            plan.synthesis_document_name = doc_name

        sections = tpl.synthesis.required_sections
        sections_desc = "；".join(sections) if sections else "综合整合分析结果"

        intent = f"将所有前序步骤的输出整合为单一综合报告：{doc_name}"
        if tpl.plan_guidance.strip():
            intent += f"\n\n任务类型专属指导：\n{tpl.plan_guidance.strip()}"

        plan.steps.append(Step(
            id="step_final_synthesis",
            step_type="synthesis",
            intent=intent,
            expected_output=(
                f"文件 {doc_name}，内容包含：{sections_desc}"
            ),
            success_criteria=(
                "文档自包含，读者无需查阅其他文件即可获取关键信息；"
                f"必须包含以下章节：{sections_desc}"
            ),
            depends_on=[s.id for s in plan.steps],
            is_simple=True,
            requires_realtime_data=False,
        ))

    def _build_task_type_catalog_context(self) -> str:
        """构造任务类型清单上下文，注入 plan prompt 帮 LLM 选型。

        从所有已加载模板汇总：task_type、描述、触发关键词、是否产出综合文档。
        """
        if not self._templates:
            return ""
        types = self._templates.list_task_types()
        if not types:
            return ""
        lines = ["# 可选任务类型清单（请根据任务性质从中选择最匹配的 task_type）"]
        for tt in sorted(types):
            tpl = self._templates.get_task_template(tt)
            if not tpl:
                continue
            tag = "产出综合文档" if tpl.synthesis.enabled else "直接输出，无综合文档"
            hints = "、".join(tpl.trigger_hints[:5]) if tpl.trigger_hints else "—"
            lines.append(
                f"- `{tpl.task_type}`：{tpl.description}（{tag}；关键词：{hints}）"
            )
        return "\n".join(lines) + "\n\n---\n\n"

    async def _identify_uncertain_claims(
        self,
        state: AgentState,
        thread_context: str,
        history_context: str,
        model_cfg: "ModelConfig",
    ) -> list[str]:
        """轻量 LLM 调用：从 task/thread/history 中挑出需核实的事实声明（至多 3 条）。"""
        try:
            prompt = self._get_think_prompt().format(
                task=state.task,
                thread_context=thread_context,
                history_context=history_context,
            )
        except Exception as e:
            logger.warning("think 提示构建失败（%s），跳过 FACT_CHECK", e)
            return []

        try:
            raw = await LLMClient.call_text(
                model_cfg, prompt, max_tokens=512, temperature=0, timeout=30
            )
        except Exception as e:
            logger.warning("think 阶段 LLM 调用失败（%s），跳过 FACT_CHECK", e)
            return []

        text = strip_code_fence(raw)
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            logger.info("think 响应非 JSON，跳过 FACT_CHECK，原文：%s", raw[:200])
            return []

        if not data.get("needs_fact_check", False):
            return []
        claims = data.get("claims", []) or []
        # 规范化 + 截断
        clean: list[str] = []
        for c in claims[:3]:
            if isinstance(c, str) and c.strip():
                clean.append(c.strip()[:80])
        return clean

    async def replan(self, failed_step: Step, result_output: str, state: AgentState) -> list[Step]:
        """局部重规划：替换 failed_step 之后所有未执行步骤。"""
        model_cfg: ModelConfig = self.config.model

        completed = [tr for tr in state.team_results if tr.passed]
        completed_summary = "; ".join(f"{tr.step_id}: {tr.output[:150]}" for tr in completed)

        remaining = []
        found = False
        for step in (state.plan.steps if state.plan else []):
            if step.id == failed_step.id:
                found = True
            if found:
                remaining.append(step.intent)

        prompt = self._get_replan_prompt().format(
            failed_step_intent=failed_step.intent,
            reason=result_output[:500],
            completed_summary=completed_summary or "无",
            goal=state.plan.goal if state.plan else state.task,
            remaining_steps=json.dumps(remaining, ensure_ascii=False),
        )

        raw = await LLMClient.call_text(
            model_cfg, prompt, max_tokens=4096, temperature=0.3
        )
        plan_text = strip_code_fence(raw)
        if not plan_text:
            logger.warning("replan LLM 响应为空，将返回空步骤列表")
            return []
        try:
            data = json.loads(plan_text)
        except json.JSONDecodeError as e:
            logger.warning("replan 响应 JSON 解析失败（%s），返回空步骤列表。原文：%s", e, plan_text[:200])
            return []
        new_steps = [Step(**s) for s in data.get("steps", [])]
        # 继承失败步骤的 step_type：若 replan LLM 未显式声明（落入默认 "generic"）
        # 但原失败步骤是专项类型，保留专项 prompt 路由；否则尊重 LLM 输出
        for s in new_steps:
            if s.step_type == "generic" and failed_step.step_type != "generic":
                s.step_type = failed_step.step_type
        return new_steps

    async def sub_replan(
        self,
        parent_step: "Step",
        failed_sub_step: "Step",
        result_output: str,
        sub_state: "AgentState",
    ) -> list["Step"]:
        """Team 内层重规划：替换失败子步骤及其后续子步骤。

        使用独立的 sub_replan.md prompt，从 sub_state.step_results 取已完成子步骤摘要。
        """
        model_cfg: ModelConfig = self.config.model

        # 从子状态取已完成子步骤摘要
        completed = [r for r in sub_state.step_results if r.verified]
        completed_sub_summary = "; ".join(
            f"{r.step_id}: {r.output[:150]}" for r in completed
        ) or "无"

        # 剩余未执行子步骤（从失败步骤起）
        remaining: list[str] = []
        found = False
        for step in (sub_state.plan.steps if sub_state.plan else []):
            if step.id == failed_sub_step.id:
                found = True
            if found:
                remaining.append(step.intent)

        prompt = self._get_sub_replan_prompt().format(
            parent_step_intent=parent_step.intent,
            failed_sub_step_intent=failed_sub_step.intent,
            reason=result_output[:500],
            completed_sub_summary=completed_sub_summary,
            remaining_sub_steps=json.dumps(remaining, ensure_ascii=False),
            parent_step_id=parent_step.id,
        )

        raw = await LLMClient.call_text(
            model_cfg, prompt, max_tokens=4096, temperature=0.3
        )
        plan_text = strip_code_fence(raw)
        if not plan_text:
            logger.warning("sub_replan LLM 响应为空，返回空步骤列表")
            return []
        try:
            data = json.loads(plan_text)
        except json.JSONDecodeError as e:
            logger.warning("sub_replan 响应解析失败（%s），原文：%s", e, plan_text[:200])
            return []
        new_steps = [Step(**s) for s in data.get("steps", [])]
        # 继承失败子步骤的 step_type（同 replan 的语义）
        for s in new_steps:
            if s.step_type == "generic" and failed_sub_step.step_type != "generic":
                s.step_type = failed_sub_step.step_type
        return new_steps

    @staticmethod
    def _parse_plan(text: str, thinking: str | None = None) -> Plan:
        """解析 JSON 格式的 Plan，容错处理 markdown 代码块及前后多余文字。"""
        import re
        text = strip_code_fence(text)
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # 部分模型（DeepSeek 等）在 JSON 前后附加说明文字，尝试提取第一个完整 JSON 对象
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group())
                except json.JSONDecodeError as e:
                    logger.error("Plan JSON 解析失败，原文（前500字）：%s", text[:500])
                    raise ValueError(
                        f"Planner 响应不是合法 JSON（{e}），请检查模型输出格式"
                    ) from e
            else:
                logger.error("Plan 响应中未找到 JSON 对象，原文（前500字）：%s", text[:500])
                raise ValueError(
                    f"Planner 响应中未找到 JSON 对象，原文：{text[:200]}"
                )
        steps = [Step(**s) for s in data.get("steps", [])]
        return Plan(
            goal=data.get("goal", ""),
            thinking=thinking,
            steps=steps,
            task_type=data.get("task_type"),
            synthesis_document_name=data.get("synthesis_document_name"),
        )
