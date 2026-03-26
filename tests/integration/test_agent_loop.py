"""T2-5 验证：AgentLoop 集成测试（mock LLM，无真实 API）"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import yaml

from sunday.agent.executor import Executor, MaxStepsError
from sunday.agent.loop import AgentLoop
from sunday.agent.models import AgentState, Plan, Step, StepResult, StepStatus
from sunday.agent.planner import Planner
from sunday.agent.verifier import Verifier, VerifyResult


def _make_settings(tmp_path, provider="anthropic"):
    from sunday.config import Settings
    config_file = tmp_path / "agent.yaml"
    config_file.write_text(yaml.dump({
        "model": {"provider": provider, "id": "claude-test", "max_tokens": 4096},
        "reasoning": {"max_steps": 5},
    }))
    with patch.dict(os.environ, {
        "ANTHROPIC_API_KEY": "sk-ant-fake",
        "SUNDAY_CONFIG_FILE": str(config_file),
    }):
        return Settings()


def _make_plan(n_steps=2) -> Plan:
    return Plan(
        goal="完成测试任务",
        steps=[Step(id=f"step_{i+1}", intent=f"步骤{i+1}", success_criteria="完成")
               for i in range(n_steps)],
    )


def _make_step_result(step_id: str, status=StepStatus.DONE) -> StepResult:
    return StepResult(step_id=step_id, status=status, output=f"{step_id} 输出")


# ── 基本流程 ──────────────────────────────────────────────────────────────────

async def test_loop_completes_simple_task(tmp_path):
    """完整循环：plan → execute → verify → summarize"""
    _make_settings(tmp_path)

    plan = _make_plan(2)
    planner = MagicMock(spec=Planner)
    planner.think_and_plan = AsyncMock(return_value=plan)

    executor = MagicMock(spec=Executor)
    executor.run = AsyncMock(side_effect=[
        _make_step_result("step_1"),
        _make_step_result("step_2"),
    ])

    verifier = MagicMock(spec=Verifier)
    verifier.check = AsyncMock(return_value=VerifyResult(passed=True, reason="ok"))
    verifier.summarize = AsyncMock(return_value="任务完成！")

    loop = AgentLoop(planner=planner, executor=executor, verifier=verifier)
    state = AgentState(session_id="sess", task="测试任务")

    result = await loop.run(state)

    assert result == "任务完成！"
    assert planner.think_and_plan.called
    assert executor.run.call_count == 2
    assert verifier.check.call_count == 2
    assert verifier.summarize.called

    # step_results 被正确记录
    assert len(state.step_results) == 2
    assert state.plan is plan


async def test_loop_emit_called(tmp_path):
    """emit 回调在关键节点被调用"""
    _make_settings(tmp_path)

    plan = _make_plan(1)
    planner = MagicMock(spec=Planner)
    planner.think_and_plan = AsyncMock(return_value=plan)

    executor = MagicMock(spec=Executor)
    executor.run = AsyncMock(return_value=_make_step_result("step_1"))

    verifier = MagicMock(spec=Verifier)
    verifier.check = AsyncMock(return_value=VerifyResult(passed=True, reason="ok"))
    verifier.summarize = AsyncMock(return_value="done")

    emitted = []

    async def capture_emit(session_id, event_type, data):
        emitted.append((event_type, data))

    loop = AgentLoop(planner=planner, executor=executor, verifier=verifier, emit=capture_emit)
    state = AgentState(session_id="sess", task="test")

    await loop.run(state)

    event_types = [e[0] for e in emitted]
    assert "status" in event_types
    assert "plan" in event_types

    # 应该有 thinking 状态
    status_events = [e[1]["status"] for e in emitted if e[0] == "status" and "status" in e[1]]
    assert "thinking" in status_events
    assert "idle" in status_events


async def test_loop_step_results_have_verified_flag(tmp_path):
    """step_results 的 verified 字段被 verifier 填写"""
    _make_settings(tmp_path)

    plan = _make_plan(1)
    planner = MagicMock(spec=Planner)
    planner.think_and_plan = AsyncMock(return_value=plan)

    executor = MagicMock(spec=Executor)
    executor.run = AsyncMock(return_value=_make_step_result("step_1"))

    verifier = MagicMock(spec=Verifier)
    verifier.check = AsyncMock(
        return_value=VerifyResult(passed=True, reason="验证通过")
    )
    verifier.summarize = AsyncMock(return_value="done")

    loop = AgentLoop(planner=planner, executor=executor, verifier=verifier)
    state = AgentState(session_id="sess", task="test")
    await loop.run(state)

    assert state.step_results[0].verified is True
    assert state.step_results[0].verify_reason == "验证通过"


# ── verify 失败触发重规划 ──────────────────────────────────────────────────────

async def test_verify_failure_triggers_replan(tmp_path):
    """Verifier 失败且 should_replan=True 时调用 planner.replan"""
    _make_settings(tmp_path)

    plan = _make_plan(2)
    new_steps = [Step(id="step_2_new", intent="新方法", success_criteria="完成")]

    planner = MagicMock(spec=Planner)
    planner.think_and_plan = AsyncMock(return_value=plan)
    planner.replan = AsyncMock(return_value=new_steps)

    executor = MagicMock(spec=Executor)
    executor.run = AsyncMock(side_effect=[
        _make_step_result("step_1"),
        _make_step_result("step_2", status=StepStatus.FAILED),
        _make_step_result("step_2_new"),  # 重规划后的步骤
    ])

    verifier = MagicMock(spec=Verifier)
    verifier.check = AsyncMock(side_effect=[
        VerifyResult(passed=True, reason="step_1 ok"),       # step_1 通过
        VerifyResult(passed=False, reason="失败", should_replan=True),  # step_2 失败
        VerifyResult(passed=True, reason="step_2_new ok"),    # 新步骤通过
    ])
    verifier.summarize = AsyncMock(return_value="重规划后完成")

    loop = AgentLoop(planner=planner, executor=executor, verifier=verifier)
    state = AgentState(session_id="sess", task="test")

    result = await loop.run(state)

    assert result == "重规划后完成"
    assert planner.replan.called
    # 最终计划包含了新步骤
    step_ids = [s.id for s in state.plan.steps]
    assert "step_2_new" in step_ids


async def test_verify_failure_no_replan_continues(tmp_path):
    """Verifier 失败但 should_replan=False 时继续执行剩余步骤"""
    _make_settings(tmp_path)

    plan = _make_plan(2)
    planner = MagicMock(spec=Planner)
    planner.think_and_plan = AsyncMock(return_value=plan)

    executor = MagicMock(spec=Executor)
    executor.run = AsyncMock(side_effect=[
        _make_step_result("step_1", status=StepStatus.FAILED),
        _make_step_result("step_2"),
    ])

    verifier = MagicMock(spec=Verifier)
    verifier.check = AsyncMock(side_effect=[
        VerifyResult(passed=False, reason="失败", should_replan=False),
        VerifyResult(passed=True, reason="ok"),
    ])
    verifier.summarize = AsyncMock(return_value="部分完成")

    loop = AgentLoop(planner=planner, executor=executor, verifier=verifier)
    state = AgentState(session_id="sess", task="test")

    result = await loop.run(state)

    assert result == "部分完成"
    assert executor.run.call_count == 2  # 两步都执行了


# ── 依赖满足检查 ──────────────────────────────────────────────────────────────

async def test_deps_satisfied_skips_unmet(tmp_path):
    """依赖未满足的步骤被跳过"""
    _make_settings(tmp_path)

    step1 = Step(id="step_1", intent="步骤1", success_criteria="")
    step2 = Step(id="step_2", intent="步骤2", depends_on=["step_1"], success_criteria="")
    plan = Plan(goal="测试", steps=[step1, step2])

    planner = MagicMock(spec=Planner)
    planner.think_and_plan = AsyncMock(return_value=plan)

    # step_1 执行失败（FAILED），step_2 依赖 step_1 DONE → 应被跳过
    executor = MagicMock(spec=Executor)
    executor.run = AsyncMock(
        return_value=_make_step_result("step_1", status=StepStatus.FAILED)
    )

    verifier = MagicMock(spec=Verifier)
    verifier.check = AsyncMock(
        return_value=VerifyResult(passed=False, reason="fail", should_replan=False)
    )
    verifier.summarize = AsyncMock(return_value="done")

    loop = AgentLoop(planner=planner, executor=executor, verifier=verifier)
    state = AgentState(session_id="sess", task="test")
    await loop.run(state)

    # step_2 依赖未满足，executor.run 只被调用了 1 次
    assert executor.run.call_count == 1


# ── 异常处理 ──────────────────────────────────────────────────────────────────

async def test_max_steps_error_handled(tmp_path):
    """Executor 抛出 MaxStepsError 时，步骤被标记 FAILED 并继续"""
    _make_settings(tmp_path)

    plan = _make_plan(2)
    planner = MagicMock(spec=Planner)
    planner.think_and_plan = AsyncMock(return_value=plan)

    executor = MagicMock(spec=Executor)
    executor.run = AsyncMock(side_effect=[
        MaxStepsError("超出步骤数"),
        _make_step_result("step_2"),
    ])

    verifier = MagicMock(spec=Verifier)
    verifier.check = AsyncMock(side_effect=[
        VerifyResult(passed=False, reason="max steps", should_replan=False),
        VerifyResult(passed=True, reason="ok"),
    ])
    verifier.summarize = AsyncMock(return_value="部分完成")

    loop = AgentLoop(planner=planner, executor=executor, verifier=verifier)
    state = AgentState(session_id="sess", task="test")

    result = await loop.run(state)
    assert result == "部分完成"
    assert len(state.step_results) == 2


# ── T3-4：ContextBuilder + MemoryManager 接入 AgentLoop ──────────────────────

async def test_loop_injects_context_into_planner(tmp_path):
    """context_builder.build() 被调用，planner.system_prompt 被设置"""
    from sunday.memory.context import ContextBuilder

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "memory").mkdir()
    (workspace / "SOUL.md").write_text("# Soul\n你是 Sunday。")

    plan = _make_plan(1)
    planner = MagicMock(spec=Planner)
    planner.think_and_plan = AsyncMock(return_value=plan)
    planner.system_prompt = ""

    executor = MagicMock(spec=Executor)
    executor.run = AsyncMock(return_value=_make_step_result("step_1"))

    verifier = MagicMock(spec=Verifier)
    verifier.check = AsyncMock(return_value=VerifyResult(passed=True, reason="ok"))
    verifier.summarize = AsyncMock(return_value="done")

    context_builder = ContextBuilder(workspace)
    loop = AgentLoop(
        planner=planner,
        executor=executor,
        verifier=verifier,
        context_builder=context_builder,
    )
    state = AgentState(session_id="s1", task="测试注入")
    await loop.run(state)

    # system_prompt 被注入（包含 SOUL.md 内容）
    assert "你是 Sunday" in planner.system_prompt


async def test_loop_calls_memory_consolidate(tmp_path):
    """memory_manager.consolidate_session 在循环结束后被调用"""
    from unittest.mock import AsyncMock as AM

    plan = _make_plan(1)
    planner = MagicMock(spec=Planner)
    planner.think_and_plan = AsyncMock(return_value=plan)

    executor = MagicMock(spec=Executor)
    executor.run = AsyncMock(return_value=_make_step_result("step_1"))

    verifier = MagicMock(spec=Verifier)
    verifier.check = AsyncMock(return_value=VerifyResult(passed=True, reason="ok"))
    verifier.summarize = AsyncMock(return_value="done")

    memory_manager = MagicMock()
    memory_manager.consolidate_session = AM(return_value=None)

    loop = AgentLoop(
        planner=planner,
        executor=executor,
        verifier=verifier,
        memory_manager=memory_manager,
    )
    state = AgentState(session_id="s2", task="记忆整合测试")
    await loop.run(state)

    memory_manager.consolidate_session.assert_called_once_with(state)


async def test_loop_no_context_builder_runs_fine(tmp_path):
    """不传 context_builder 时循环正常运行（向后兼容）"""
    plan = _make_plan(1)
    planner = MagicMock(spec=Planner)
    planner.think_and_plan = AsyncMock(return_value=plan)

    executor = MagicMock(spec=Executor)
    executor.run = AsyncMock(return_value=_make_step_result("step_1"))

    verifier = MagicMock(spec=Verifier)
    verifier.check = AsyncMock(return_value=VerifyResult(passed=True, reason="ok"))
    verifier.summarize = AsyncMock(return_value="done")

    loop = AgentLoop(planner=planner, executor=executor, verifier=verifier)
    state = AgentState(session_id="s3", task="向后兼容测试")
    result = await loop.run(state)
    assert result == "done"
