"""T2-1 验证：核心数据模型序列化/反序列化"""
from __future__ import annotations

from datetime import datetime

from sunday.agent.models import (
    AgentState,
    Message,
    Plan,
    ReactIteration,
    Step,
    StepResult,
    StepStatus,
    ThinkingLevel,
    ToolCall,
)

# ── ThinkingLevel ─────────────────────────────────────────────────────────────

def test_thinking_level_enum_values():
    """ThinkingLevel 包含全部 5 个合法值"""
    assert ThinkingLevel.OFF == "off"
    assert ThinkingLevel.MINIMAL == "minimal"
    assert ThinkingLevel.LOW == "low"
    assert ThinkingLevel.MEDIUM == "medium"
    assert ThinkingLevel.HIGH == "high"


def test_thinking_level_from_str():
    """ThinkingLevel 可以从字符串构造"""
    assert ThinkingLevel("medium") == ThinkingLevel.MEDIUM


# ── StepStatus ────────────────────────────────────────────────────────────────

def test_step_status_enum_values():
    """StepStatus 包含全部 5 个合法值"""
    values = {s.value for s in StepStatus}
    assert values == {"pending", "running", "done", "failed", "skipped"}


# ── Step ──────────────────────────────────────────────────────────────────────

def test_step_default_status():
    """Step 默认 status 为 PENDING"""
    step = Step(id="step_1", intent="测试意图")
    assert step.status == StepStatus.PENDING


def test_step_serialization():
    """Step 可以序列化和反序列化"""
    step = Step(
        id="step_1",
        intent="写一首诗",
        expected_output="五言绝句",
        success_criteria="包含 4 行",
        depends_on=["step_0"],
    )
    data = step.model_dump()
    restored = Step.model_validate(data)
    assert restored.id == step.id
    assert restored.intent == step.intent
    assert restored.depends_on == ["step_0"]
    assert restored.status == StepStatus.PENDING


def test_step_depends_on_default_empty():
    """Step.depends_on 默认为空列表"""
    step = Step(id="s1", intent="test")
    assert step.depends_on == []


# ── Plan ──────────────────────────────────────────────────────────────────────

def test_plan_serialization():
    """Plan 可以序列化和反序列化"""
    plan = Plan(
        goal="完成任务",
        thinking="思考内容",
        steps=[Step(id="step_1", intent="步骤1"), Step(id="step_2", intent="步骤2")],
    )
    data = plan.model_dump()
    restored = Plan.model_validate(data)
    assert restored.goal == "完成任务"
    assert restored.thinking == "思考内容"
    assert len(restored.steps) == 2
    assert restored.steps[0].id == "step_1"


def test_plan_thinking_optional():
    """Plan.thinking 可以为 None"""
    plan = Plan(goal="测试")
    assert plan.thinking is None
    assert plan.steps == []


# ── ToolCall ──────────────────────────────────────────────────────────────────

def test_tool_call_defaults():
    """ToolCall 默认 arguments 为空 dict"""
    tc = ToolCall(tool_name="list_files")
    assert tc.arguments == {}
    assert tc.call_id == ""


def test_tool_call_with_args():
    """ToolCall 可以携带参数"""
    tc = ToolCall(tool_name="run_shell", arguments={"command": "ls -la"})
    assert tc.arguments["command"] == "ls -la"


# ── ReactIteration ────────────────────────────────────────────────────────────

def test_react_iteration_serialization():
    """ReactIteration 可以序列化和反序列化"""
    it = ReactIteration(
        iteration=0,
        thought="我需要列出文件",
        tool_name="list_files",
        tool_input={"path": "/tmp"},
        observation="file1.txt\nfile2.txt",
    )
    data = it.model_dump()
    restored = ReactIteration.model_validate(data)
    assert restored.iteration == 0
    assert restored.tool_name == "list_files"
    assert restored.tool_input == {"path": "/tmp"}


# ── StepResult ────────────────────────────────────────────────────────────────

def test_step_result_defaults():
    """StepResult 默认 verified=False，status=DONE"""
    result = StepResult(step_id="step_1")
    assert result.status == StepStatus.DONE
    assert result.verified is False
    assert result.verify_reason == ""
    assert isinstance(result.created_at, datetime)


def test_step_result_roundtrip():
    """StepResult 完整序列化/反序列化"""
    result = StepResult(
        step_id="step_1",
        status=StepStatus.DONE,
        output="完成了",
        react_iterations=[
            ReactIteration(iteration=0, tool_name="foo", observation="bar")
        ],
        verified=True,
        verify_reason="满足标准",
    )
    data = result.model_dump()
    restored = StepResult.model_validate(data)
    assert restored.step_id == "step_1"
    assert restored.verified is True
    assert len(restored.react_iterations) == 1


# ── Message ───────────────────────────────────────────────────────────────────

def test_message_fields():
    """Message 包含 role 和 content"""
    msg = Message(role="user", content="你好")
    assert msg.role == "user"
    assert msg.content == "你好"
    assert isinstance(msg.ts, datetime)


# ── AgentState ────────────────────────────────────────────────────────────────

def test_agent_state_defaults():
    """AgentState 默认字段正确"""
    state = AgentState(session_id="abc123", task="测试任务")
    assert state.history == []
    assert state.plan is None
    assert state.step_results == []
    assert state.thinking_level == ThinkingLevel.MEDIUM
    assert state.aborted is False


def test_agent_state_history_append():
    """AgentState.history 可以追加消息"""
    state = AgentState(session_id="abc", task="test")
    state.history.append(Message(role="user", content="你好"))
    state.history.append(Message(role="assistant", content="我来帮你"))
    assert len(state.history) == 2
    assert state.history[0].role == "user"


def test_agent_state_plan_assignment():
    """AgentState.plan 可以赋值为 Plan"""
    state = AgentState(session_id="x", task="t")
    state.plan = Plan(goal="目标", steps=[Step(id="s1", intent="做事")])
    assert state.plan.goal == "目标"
    assert len(state.plan.steps) == 1


def test_agent_state_serialization():
    """AgentState 完整序列化不抛异常"""
    state = AgentState(
        session_id="sess001",
        task="写诗",
        thinking_level=ThinkingLevel.HIGH,
    )
    state.plan = Plan(goal="写一首诗", steps=[Step(id="s1", intent="写")])
    state.step_results.append(StepResult(step_id="s1", output="静夜思"))
    data = state.model_dump()
    assert data["session_id"] == "sess001"
    assert data["plan"]["goal"] == "写一首诗"
