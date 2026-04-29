"""ReactAgent — 顶层 ReAct Agent。

执行流程：
    Service/CLI 通过 build_agent_loop() 构造 ReactAgent
        → run(state)
            → inject_memory_context()   # L0/L1/L2 上下文注入（走 MemoryClient）
            → plan(state)               # THINK + PLAN
            → execute(state, ...)       # 遍历步骤，每步创建 Team/SimpleNode
            → evaluate(state)           # 顶层评估汇总
            → consolidate_memory(state) # 记忆整合（走 Consolidator → MemoryClient）

所有持久化都通过外部注入的 MemoryClient 走，不持有任何文件 IO。
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
from sunday.memory.consolidator import Consolidator
from sunday.memory.context import ContextBuilder
from sunday.memory.models import LogEvent

if TYPE_CHECKING:
    from sunday.config import SundayConfig
    from sunday.memory.client import MemoryClient
    from sunday.tools.registry import ConfirmationHandler, ToolRegistry

logger = logging.getLogger(__name__)


class ReactAgent:
    """顶层 ReAct Agent。

    所有子组件（Planner、Verifier、ToolRegistry、ContextBuilder、Consolidator）
    在 __init__ 中根据传入的 config + memory_client 自动初始化。
    """

    _probed: bool = False  # 类变量默认值，测试通过 __new__ 绕过 __init__ 时也生效

    def __init__(
        self,
        config: "SundayConfig",
        memory_client: "MemoryClient",
        emit: EmitCallable | None = None,
        mode: str = "service",
        confirmation_handler: "ConfirmationHandler | None" = None,
    ) -> None:
        from sunday.bootstrap import assert_runtime_initialized, build_tool_registry
        from sunday.skills.loader import SkillLoader
        from sunday.templates.loader import TemplateLoader

        # 冷启动检查：未 init 则直接报错（方向 2：Memory 黑盒，agent 不做 seed）
        assert_runtime_initialized(config)

        self.config = config
        self.memory = memory_client
        self.emit = emit or noop_emit
        self.mode = mode
        self.session_report_dir: Path | None = None
        self._probed: bool = False

        # 基础工具集（共享，每个节点 clone 后按需叠加）
        self.tool_registry: ToolRegistry = build_tool_registry(config, confirmation_handler)

        # 任务模板自动加载（与 SkillLoader 同构）
        workspace_dir = config.agent.workspace_dir
        self.templates = TemplateLoader(
            builtin_dir=config.configs_dir / "templates",
            user_dir=workspace_dir / "templates",
        )
        self.templates.discover()

        # 顶层规划器与评估器
        self.planner = Planner(config, templates=self.templates)
        self.verifier = Verifier(config)

        # 上下文与记忆整合（全部走 MemoryClient）
        skill_loader = SkillLoader(
            project_skills_dir=workspace_dir.parent.parent / "skills",
            user_skills_dir=workspace_dir / "skills",
        )
        skill_loader.discover()
        self.context_builder = ContextBuilder(
            client=memory_client, skill_loader=skill_loader, config=config,
        )
        self.consolidator = Consolidator(memory_client, config)

    # ── 主入口 ───────────────────────────────────────────────────────────────

    async def run(self, state: AgentState) -> str:
        """执行完整的 think→plan→execute→verify 循环，返回最终摘要。"""

        # 每次任务开始时重置文件写入记录，避免跨会话污染
        self.tool_registry._agent_written_files = []

        # 首次运行时对所有有 probe 的工具执行端到端探测
        if not self._probed:
            await self.tool_registry.probe_all()
            self._probed = True

        start_ts = datetime.now(timezone.utc)
        emit = self.emit

        async def logged_emit(sid: str, et: str, d: dict) -> None:
            await emit(sid, et, d)
            await self._log_event(sid, et, d)

        # session report 目录：sessions/{session_id}/reports/{turn_id}/
        if state.turn_id:
            session_report_dir = self.memory.sessions.report_dir(
                state.session_id, state.turn_id
            )
        else:
            session_report_dir = None
        self.tool_registry.set_report_dir(session_report_dir)
        self.session_report_dir = session_report_dir

        # 写 session_start 日志
        if state.session_id:
            await self.memory.logs.emit(state.session_id, LogEvent(
                event="session_start",
                session_id=state.session_id,
                data={
                    "task": state.task,
                    "thinking_level": state.thinking_level.value,
                    "mode": self.mode,
                },
            ))

        try:
            await self.inject_memory_context(state)
            await logged_emit(state.session_id, "status", {"status": "thinking"})

            plan = await self.plan(state, emit=logged_emit)
            state.plan = plan
            if not plan.steps:
                msg = "规划失败：LLM 未能生成有效的执行步骤，请重试或简化任务描述。"
                logger.warning(msg)
                await self._log_session_end(state, start_ts, msg, [])
                return msg
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

            summary = await self.execute(state, logged_emit)

            await logged_emit(state.session_id, "status", {"status": "idle"})
            self._write_report(session_report_dir, summary, state)

            await self.consolidate_memory(state)
            await self._log_session_end(state, start_ts, summary, state.team_results)
            return summary

        except asyncio.CancelledError:
            state.aborted = True
            await emit(state.session_id, "status", {"status": "aborted"})
            await self._log_session_abort(state, start_ts)
            raise
        except Exception as e:
            logger.exception("ReactAgent 未捕获异常：%s", e)
            await emit(state.session_id, "status", {"status": "error", "message": str(e)})
            await self._log_session_error(state, start_ts, e)
            raise
        finally:
            logger.info("ReactAgent 结束，session=%s，Team数=%d",
                        state.session_id, len(state.team_results))

    # ── 各阶段独立方法 ───────────────────────────────────────────────────────

    async def plan(self, state: AgentState, emit: EmitCallable | None = None) -> Plan:
        # 首次 plan 之前预加载 runtime rules（每进程一次缓存）
        if self.planner._runtime_rules is None:
            try:
                self.planner._runtime_rules = await self.memory.workspace.read_runtime_rules()
            except Exception:
                logger.exception("读取 runtime rules 失败，使用内置默认")
        return await self.planner.think_and_plan(
            state,
            tool_registry=self.tool_registry,
            emit=emit or self.emit,
        )

    async def execute(self, state: AgentState, emit: EmitCallable) -> str:
        reasoning = self.config.reasoning
        max_replans = reasoning.max_replans

        steps = list(state.plan.steps)
        cycle = self._detect_dependency_cycle(steps)
        if cycle:
            msg = f"规划错误：检测到循环依赖 {' → '.join(cycle)}，任务无法执行。"
            logger.error(msg)
            return msg
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
                        replan_took_over = True
                        logger.debug("步骤 %s 失败结果已丢弃，由重规划步骤替代", step.id)
                    break
                else:
                    logger.warning(
                        "步骤 %s 失败，全局重规划已达上限 %d，继续执行后续步骤",
                        step.id, max_replans,
                    )
                    break

            if replan_took_over:
                continue

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

        await emit(state.session_id, "status", {"status": "summarizing"})
        return await self.evaluate(state)

    async def evaluate(self, state: AgentState) -> str:
        return await self.verifier.evaluate(
            state, state.team_results, self.tool_registry.agent_written_files
        )

    async def replan(self, step: Step, team_result: TeamResult, state: AgentState) -> list[Step]:
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
        try:
            ctx = await self.context_builder.build(state.session_id)
            self.planner.system_prompt = ctx.system_prompt
            logger.debug("上下文注入完成，token_estimate=%d", ctx.token_estimate)
        except Exception as e:
            logger.warning("上下文注入失败，跳过：%s", e)

    async def consolidate_memory(self, state: AgentState) -> None:
        try:
            await self.consolidator.consolidate(state)
            logger.debug("记忆整合完成，session=%s", state.session_id)
        except Exception as e:
            logger.warning("记忆整合失败，跳过：%s", e)

    # ── 节点工厂（配置驱动） ─────────────────────────────────────────────────

    def _create_node(self, step: Step, emit: EmitCallable | None = None) -> Team | SimpleNode:
        from sunday.tools.local_loader import load_skill_tools

        node_cfg = self.config.nodes.get(step.id)
        registry = self.tool_registry.clone()

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

        node_type = node_cfg.type if node_cfg else "auto"
        use_simple = (
            node_type == "simple"
            or (node_type == "auto" and step.is_simple)
        )

        executor_prompt_override = node_cfg.executor_prompt if node_cfg else None
        _emit = emit or self.emit
        # 测试可能用 __new__ 绕过 __init__，此时 self.templates 不存在 → 容错为 None
        templates = getattr(self, "templates", None)
        if use_simple:
            return SimpleNode(
                self.config, registry, emit=_emit,
                executor_prompt_override=executor_prompt_override,
                templates=templates,
            )
        return Team(
            self.config, registry, emit=_emit,
            executor_prompt_override=executor_prompt_override,
            templates=templates,
        )

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
        if not step.depends_on:
            return True
        done_ids = {tr.step_id for tr in state.team_results if tr.passed}
        return all(dep in done_ids for dep in step.depends_on)

    @staticmethod
    def _detect_dependency_cycle(steps: list[Step]) -> list[str] | None:
        from collections import deque

        step_ids = {s.id for s in steps}
        in_degree: dict[str, int] = {s.id: 0 for s in steps}
        graph: dict[str, list[str]] = {s.id: [] for s in steps}

        for s in steps:
            for dep in s.depends_on:
                if dep in step_ids:
                    graph[dep].append(s.id)
                    in_degree[s.id] += 1

        queue = deque(sid for sid, deg in in_degree.items() if deg == 0)
        visited = 0
        while queue:
            node = queue.popleft()
            visited += 1
            for neighbor in graph[node]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if visited == len(steps):
            return None

        cycle_nodes = [sid for sid, deg in in_degree.items() if deg > 0]
        return cycle_nodes

    # ── 日志事件路由 ───────────────────────────────────────────────────────

    async def _log_event(self, session_id: str, event_type: str, data: dict) -> None:
        """把 emit 事件翻译为语义化 LogEvent，写入 logs/{sid}.jsonl。

        过滤规则：仅记录关键执行事件（plan/step_start/step_result/replan/error 等），
        跳过 thinking 等噪声状态。
        """
        if not session_id:
            return
        mapped = _map_emit_to_log(event_type, data)
        if mapped is None:
            return
        event_name, payload = mapped
        await self.memory.logs.emit(session_id, LogEvent(
            event=event_name, session_id=session_id, data=payload,
        ))

    async def _log_session_end(
        self, state: AgentState, start_ts: datetime, summary: str,
        team_results: list[TeamResult],
    ) -> None:
        if not state.session_id:
            return
        total = len(team_results)
        passed = sum(1 for tr in team_results if tr.passed)
        outcome = "success" if total and passed == total else (
            "partial" if passed > 0 else "failed"
        )
        await self.memory.logs.emit(state.session_id, LogEvent(
            event="session_end",
            session_id=state.session_id,
            data={
                "outcome": outcome,
                "steps_total": total,
                "steps_passed": passed,
                "steps_failed": total - passed,
                "duration_seconds": _elapsed(start_ts),
                "summary_snippet": summary[:200],
            },
        ))

    async def _log_session_abort(self, state: AgentState, start_ts: datetime) -> None:
        if not state.session_id:
            return
        await self.memory.logs.emit(state.session_id, LogEvent(
            event="session_end",
            session_id=state.session_id,
            data={"outcome": "aborted", "duration_seconds": _elapsed(start_ts)},
        ))

    async def _log_session_error(
        self, state: AgentState, start_ts: datetime, error: Exception,
    ) -> None:
        if not state.session_id:
            return
        await self.memory.logs.emit(state.session_id, LogEvent(
            event="session_end",
            session_id=state.session_id,
            data={
                "outcome": "error",
                "error": str(error),
                "duration_seconds": _elapsed(start_ts),
            },
        ))


def _elapsed(start_ts: datetime) -> float:
    return round((datetime.now(timezone.utc) - start_ts).total_seconds(), 2)


def _map_emit_to_log(event_type: str, data: dict) -> tuple[str, dict] | None:
    """把 emit 事件映射为 logs/ 写入用的语义化事件 + 字段裁剪。

    Service 模式 stream.jsonl 已记录原始事件流；这里输出的是用于人工 / jq
    分析的精简版（CLI 与 Service 行为一致）。
    """
    if event_type == "status":
        status_val = data.get("status", "")
        if status_val.startswith("executing:"):
            return ("step_start", {
                "step_id": status_val.split(":", 1)[1],
                "intent": data.get("intent", ""),
                "node_type": data.get("node_type", ""),
            })
        if status_val == "replanning":
            return ("replan", {
                "step_id": data.get("step_id", ""),
                "replan_count": data.get("replan_count"),
                "max_replans": data.get("max_replans"),
                "failure_reason": data.get("failure_reason", ""),
            })
        return None
    if event_type == "plan":
        return ("plan", {
            "goal": data.get("goal", ""),
            "steps": data.get("steps", []),
        })
    if event_type == "step_result":
        return ("step_result", {
            "step_id": data.get("step_id", ""),
            "status": data.get("status", ""),
            "verified": data.get("verified"),
            "verify_reason": data.get("verify_reason", ""),
            "duration_ms": data.get("duration_ms"),
        })
    if event_type == "sub_step_result":
        return ("sub_step_result", {
            "parent_step_id": data.get("parent_step_id", ""),
            "sub_step_id": data.get("sub_step_id", ""),
            "status": data.get("status", ""),
            "verified": data.get("verified"),
            "verify_reason": data.get("verify_reason", ""),
        })
    if event_type == "team_error":
        return ("team_error", {
            "step_id": data.get("step_id", ""),
            "phase": data.get("phase", ""),
            "sub_step_id": data.get("sub_step_id", ""),
            "error": data.get("error", ""),
        })
    if event_type == "error":
        return ("error", {"message": data.get("message", "")})
    return None
