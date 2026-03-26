"""Phase 2：Agent 执行循环核心数据模型"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ThinkingLevel(str, Enum):
    OFF = "off"
    MINIMAL = "minimal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class Step(BaseModel):
    """一个原子执行单元"""

    id: str
    intent: str
    expected_input: str = ""
    expected_output: str = ""
    success_criteria: str = ""
    depends_on: list[str] = Field(default_factory=list)
    status: StepStatus = StepStatus.PENDING


class Plan(BaseModel):
    """Planner 输出，描述执行目标"""

    goal: str
    thinking: str | None = None
    steps: list[Step] = Field(default_factory=list)


class ToolCall(BaseModel):
    """一次工具调用请求"""

    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    call_id: str = ""


class ReactIteration(BaseModel):
    """ReAct 单次循环记录"""

    iteration: int
    thought: str = ""
    tool_name: str = ""
    tool_input: dict[str, Any] = Field(default_factory=dict)
    observation: str = ""


class StepResult(BaseModel):
    """Executor 的输出，Verifier 填写 verified/verify_reason"""

    step_id: str
    status: StepStatus = StepStatus.DONE
    output: str = ""
    react_iterations: list[ReactIteration] = Field(default_factory=list)
    verified: bool = False
    verify_reason: str = ""
    created_at: datetime = Field(default_factory=datetime.now)


class Message(BaseModel):
    """会话历史中的一条消息"""

    role: str  # user | assistant | system | tool
    content: str
    ts: datetime = Field(default_factory=datetime.now)


class AgentState(BaseModel):
    """一次任务执行的完整状态，贯穿整个循环"""

    session_id: str
    task: str
    history: list[Message] = Field(default_factory=list)
    plan: Plan | None = None
    step_results: list[StepResult] = Field(default_factory=list)
    thinking_level: ThinkingLevel = ThinkingLevel.MEDIUM
    aborted: bool = False
    created_at: datetime = Field(default_factory=datetime.now)
