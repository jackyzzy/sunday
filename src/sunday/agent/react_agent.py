"""ReactAgent — 顶层 ReAct Agent（面向对象重构）。

执行流程：
    Gateway/CLI 创建 ReactAgent(config, emit, mode)
        → run(state)
            → inject_memory_context()   # L0 上下文注入
            → plan(state)               # THINK + PLAN
            → execute(state, ...)       # 遍历步骤，每步创建 Team/SimpleNode
            → evaluate(state)           # 顶层评估汇总
            → consolidate_memory(state) # 记忆整合

与旧 AgentLoop 的区别：
- __init__ 只需传 config，所有子组件在构造时自动初始化（无需外部注入）
- _create_node() 实现配置驱动的节点工厂，支持 clone ToolRegistry 后叠加专属技能
- 各阶段提取为独立方法（plan/execute/evaluate/replan），职责清晰
- memory 相关操作封装为 inject_memory_context / consolidate_memory / _build_step_context
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from sunday.agent.models import AgentState, Plan, Step, StepStatus, TeamResult
from sunday.agent.planner import Planner
from sunday.agent.simple import SimpleNode
from sunday.agent.team import Team
from sunday.agent.utils import EmitCallable, noop_emit
from sunday.agent.verifier import Verifier

if TYPE_CHECKING:
    from sunday.config import SundayConfig
    from sunday.memory.context import ContextBuilder
    from sunday.memory.manager import MemoryManager
    from sunday.tools.registry import ConfirmationHandler, ToolRegistry

logger = logging.getLogger(__name__)


class ReactAgent:
    """顶层 ReAct Agent。

    网关/CLI 通过 ReactAgent(config, emit, mode) 创建，__init__ 自动初始化
    所有子组件（Planner、Verifier、ToolRegistry、ContextBuilder、MemoryManager），
    无需外部手动注入依赖。

    子节点（Team / SimpleNode）在 _create_node() 中按步骤按需创建，
    各自接收 clone 后的 ToolRegistry 副本，可独立叠加专属技能工具。
    """

    def __init__(
        self,
        config: "SundayConfig",
        emit: EmitCallable | None = None,
        mode: str = "gateway",
        confirmation_handler: "ConfirmationHandler | None" = None,
    ) -> None:
        from sunday.bootstrap import build_tool_registry
        from sunday.memory.context import ContextBuilder
        from sunday.memory.manager import MemoryManager
        from sunday.skills.loader import SkillLoader

        self.config = config
        self.emit = emit or noop_emit
        self.mode = mode
        self.session_report_dir: Path | None = None
        self._probed: bool = False

        # 基础工具集（共享，每个节点 clone 后按需叠加）
        self.tool_registry: ToolRegistry = build_tool_registry(config, confirmation_handler)

        # 顶层规划器与评估器
        self.planner = Planner(config)
        self.verifier = Verifier(config)

        # Memory 接口（Phase 3 已实现）
        workspace_dir = config.agent.workspace_dir
        skills_dir = workspace_dir.parent.parent / "skills"
        skill_loader = SkillLoader(
            project_skills_dir=skills_dir,
            user_skills_dir=workspace_dir / "skills",
        )
        skill_loader.discover()
        self.context_builder: ContextBuilder = ContextBuilder(
            workspace_dir, skill_loader=skill_loader, config=config
        )
        self.memory_manager: MemoryManager = MemoryManager(workspace_dir, config=config)

    # ── 主入口 ───────────────────────────────────────────────────────────────

    async def run(self, state: AgentState) -> str:
        """执行完整的 think→plan→execute→verify 循环，返回最终摘要。"""
        from sunday.agent.session_log import SessionLog
        from sunday.tools.cli_tool import make_session_report_dir

        # 首次运行时对所有有 probe 的工具执行端到端探测（B 场景）
        if not self._probed:
            await self.tool_registry.probe_all()
            self._probed = True

        # 日志目录：优先从 config 读取，不存在时回退默认路径（兼容测试中 mock config）
        log_dir = (self.config.agent.log_dir
                   if getattr(self.config, "agent", None) and self.config.agent.log_dir
                   else Path.home() / ".sunday" / "logs")
        session_log = SessionLog(log_dir, state.session_id)
        session_log.log_start(state.task, state.thinking_level.value, self.mode)

        emit = self.emit

        async def logged_emit(sid: str, et: str, d: dict) -> None:
            await emit(sid, et, d)
            session_log.log(et, d)

        # session report 目录（report_dir 为 None 时跳过写报告）
        report_dir = (self.config.agent.report_dir
                      if getattr(self.config, "agent", None) and self.config.agent.report_dir
                      else None)
        session_report_dir: Path | None = (
            make_session_report_dir(report_dir, state.task, state.session_id)
            if report_dir else None
        )
        self.tool_registry.set_report_dir(session_report_dir)
        self.session_report_dir = session_report_dir

        try:
            # L0 上下文注入
            await self.inject_memory_context(state)

            await logged_emit(state.session_id, "status", {"status": "thinking"})

            # THINK + PLAN
            plan = await self.plan(state)
            state.plan = plan
            max_steps = self.config.reasoning.max_steps
            if len(plan.steps) > max_steps:
                logger.warning("计划步骤数 %d 超过上限 %d，已截断", len(plan.steps), max_steps)
                plan.steps = plan.steps[:max_steps]
                state.plan = plan
            await logged_emit(state.session_id, "plan", {
                "goal": plan.goal,
                "steps": [
                    {"id": s.id, "intent": s.intent[:200], "criteria": s.success_criteria[:100]}
                    for s in plan.steps
                ],
            })

            # EXECUTE + VERIFY
            summary = await self.execute(state, logged_emit, session_log)

            await logged_emit(state.session_id, "status", {"status": "idle"})
            self._write_report(session_report_dir, summary, state)

            # 记忆整合
            await self.consolidate_memory(state)

            session_log.log_end(summary, state.team_results)
            return summary

        except asyncio.CancelledError:
            state.aborted = True
            await emit(state.session_id, "status", {"status": "aborted"})
            session_log.log_abort()
            raise
        except Exception as e:
            logger.exception("ReactAgent 未捕获异常：%s", e)
            await emit(state.session_id, "status", {"status": "error", "message": str(e)})
            session_log.log_error(e)
            raise
        finally:
            logger.info("ReactAgent 结束，session=%s，Team数=%d",
                        state.session_id, len(state.team_results))

    # ── 各阶段独立方法 ───────────────────────────────────────────────────────

    async def plan(self, state: AgentState) -> Plan:
        """THINK + PLAN 阶段：调用顶层 Planner 生成 Plan。"""
        return await self.planner.think_and_plan(state)

    async def execute(self, state: AgentState, emit: EmitCallable, session_log) -> str:
        """EXECUTE + VERIFY 阶段：串行执行所有步骤，含重规划，返回最终评估摘要。"""
        reasoning = self.config.reasoning
        max_replans = reasoning.max_replans

        steps = list(state.plan.steps)
        idx = 0
        total_replan_count = 0

        while idx < len(steps):
            step = steps[idx]

            if not self._deps_satisfied(step, state):
                step.status = StepStatus.SKIPPED
                await emit(state.session_id, "step_result", {
                    "step_id": step.id,
                    "status": StepStatus.SKIPPED.value,
                    "verified": None,
                    "verify_reason": "",
                    "duration_ms": 0,
                })
                idx += 1
                continue

            step.status = StepStatus.RUNNING
            step_start_ts = datetime.now(timezone.utc)
            await emit(state.session_id, "status", {
                "status": f"executing:{step.id}",
                "intent": step.intent[:200],
                "node_type": "simple" if step.is_simple else "team",
            })

            replan_took_over = False
            while True:
                node = self._create_node(step, emit)
                team_result = await node.run(step, state)

                if team_result.passed:
                    step.status = StepStatus.DONE
                    break

                step.status = StepStatus.FAILED
                can_replan = (
                    total_replan_count < max_replans
                    and team_result.should_replan
                )
                if can_replan:
                    total_replan_count += 1
                    logger.info(
                        "步骤 %s 失败，触发局部重规划（全局第 %d/%d 次）",
                        step.id, total_replan_count, max_replans,
                    )
                    await emit(state.session_id, "status", {
                        "status": "replanning",
                        "step_id": step.id,
                        "replan_count": total_replan_count,
                        "max_replans": max_replans,
                        "failure_reason": team_result.output[:300],
                    })
                    new_steps = await self.replan(step, team_result, state)
                    if new_steps:
                        steps = steps[:idx] + new_steps
                        state.plan.steps = steps
                        state.team_results.append(team_result)
                        replan_took_over = True
                    break
                else:
                    logger.warning(
                        "步骤 %s 失败，全局重规划已达上限 %d，继续执行后续步骤",
                        step.id, max_replans,
                    )
                    break

            if replan_took_over:
                continue

            # 提取 verify_reason：优先取最后一个失败子步骤的原因
            verify_reason = ""
            if team_result.sub_steps:
                failing = [s for s in team_result.sub_steps if not s.verified]
                src = failing[-1] if failing else team_result.sub_steps[-1]
                verify_reason = src.verify_reason or ""

            duration_ms = round(
                (datetime.now(timezone.utc) - step_start_ts).total_seconds() * 1000
            )

            state.team_results.append(team_result)
            await emit(state.session_id, "step_result", {
                "step_id": step.id,
                "status": step.status.value,
                "verified": team_result.passed,
                "verify_reason": verify_reason,
                "duration_ms": duration_ms,
            })
            idx += 1

        # EVALUATE
        await emit(state.session_id, "status", {"status": "summarizing"})
        return await self.evaluate(state)

    async def evaluate(self, state: AgentState) -> str:
        """EVALUATE 阶段：顶层整体评估，生成任务摘要。"""
        return await self.verifier.evaluate(
            state, state.team_results, self.tool_registry.agent_written_files
        )

    async def replan(self, step: Step, team_result: TeamResult, state: AgentState) -> list[Step]:
        """顶层局部重规划，失败时返回空列表。"""
        try:
            new_steps = await self.planner.replan(step, team_result.output, state)
        except Exception as e:
            logger.warning("局部重规划失败（%s），跳过重规划", e)
            return []
        if not new_steps:
            logger.warning("重规划返回空步骤，停止执行循环")
        return new_steps

    # ── Memory 接口 ─────────────────────────────────────────────────────────

    async def inject_memory_context(self, state: AgentState) -> None:
        """注入 L0 上下文到 Planner（Phase 3 已实现）。"""
        try:
            ctx = self.context_builder.build(state.session_id)
            self.planner.system_prompt = ctx.system_prompt
            logger.debug("上下文注入完成，token_estimate=%d", ctx.token_estimate)
        except Exception as e:
            logger.warning("上下文注入失败，跳过：%s", e)

    async def consolidate_memory(self, state: AgentState) -> None:
        """整合会话记忆（Phase 3 已实现）。"""
        try:
            await self.memory_manager.consolidate_session(state)
            logger.debug("记忆整合完成，session=%s", state.session_id)
        except Exception as e:
            logger.warning("记忆整合失败，跳过：%s", e)

    async def _build_step_context(self, step: Step, state: AgentState) -> str:
        """为子 Agent 构建步骤执行上下文（预留接口，当前未调用）。

        当前逻辑已内联在 Team._build_sub_task() 中；此方法是未来统一上下文构建的入口。
        TODO(memory): 未来通过 MemoryManager 检索相关记忆替换此方法，
                      实现跨会话的上下文感知，并在 _create_node() 中注入给子节点。
        """
        prev_outputs = [
            f"- {tr.step_id}: {tr.output[:2000]}"
            for tr in state.team_results if tr.passed and tr.output
        ]
        return "\n".join(prev_outputs) if prev_outputs else ""

    # ── 节点工厂（配置驱动） ─────────────────────────────────────────────────

    def _create_node(self, step: Step, emit: EmitCallable | None = None) -> Team | SimpleNode:
        """根据 step 和 agent.yaml nodes 配置创建对应的执行节点。

        优先读取 config.nodes[step.id] 的专属配置；未配置则根据 step.is_simple 决定。
        每个节点获得独立的 ToolRegistry clone，可在基础工具集上叠加专属技能工具。
        emit 优先使用传入值（logged_emit），回退到 self.emit（raw）。
        """
        from sunday.tools.local_loader import load_skill_tools

        node_cfg = self.config.nodes.get(step.id)

        # clone 基础工具集，避免节点间互相影响
        registry = self.tool_registry.clone()

        # 叠加节点专属技能工具
        if node_cfg and node_cfg.extra_skills:
            workspace_dir = self.config.agent.workspace_dir
            skills_dir = workspace_dir.parent.parent / "skills"
            for skill_name in node_cfg.extra_skills:
                skill_dir = skills_dir / skill_name
                if skill_dir.exists():
                    load_skill_tools(skill_dir, registry)
                    logger.debug("节点 %s 加载专属技能：%s", step.id, skill_name)
                else:
                    logger.warning("节点 %s 专属技能目录不存在：%s", step.id, skill_dir)

        # 确定节点类型
        node_type = node_cfg.type if node_cfg else "auto"
        use_simple = (
            node_type == "simple"
            or (node_type == "auto" and step.is_simple)
        )

        _emit = emit or self.emit
        if use_simple:
            return SimpleNode(self.config, registry, emit=_emit)
        return Team(self.config, registry, emit=_emit)

    # ── 辅助方法 ────────────────────────────────────────────────────────────

    def _write_report(self, report_dir: Path | None, summary: str, state: AgentState) -> None:
        """将执行结果写入 session report 目录（可选）。"""
        if report_dir is None:
            return
        report_dir.mkdir(parents=True, exist_ok=True)
        (report_dir / "summary.md").write_text(summary, encoding="utf-8")
        lines: list[str] = []
        for tr in state.team_results:
            mark = "✓" if tr.passed else "✗"
            lines += [f"## {tr.step_id} [{mark}]", tr.output or "", ""]
            for sr in tr.sub_steps:
                lines += [f"  ### {sr.step_id} — {sr.status.value}", sr.output or "", ""]
        (report_dir / "steps.md").write_text("\n".join(lines), encoding="utf-8")
        logger.debug("报告已写入：%s", report_dir)

    @staticmethod
    def _deps_satisfied(step: Step, state: AgentState) -> bool:
        """检查步骤的所有依赖是否已完成（基于 team_results）。"""
        if not step.depends_on:
            return True
        done_ids = {tr.step_id for tr in state.team_results if tr.passed}
        return all(dep in done_ids for dep in step.depends_on)
