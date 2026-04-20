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


THINKING_BUDGET: dict["ThinkingLevel", int] = {
    ThinkingLevel.OFF: 0,
    ThinkingLevel.MINIMAL: 512,
    ThinkingLevel.LOW: 1024,
    ThinkingLevel.MEDIUM: 4096,
    ThinkingLevel.HIGH: 8192,
}


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
    expected_output: str = ""
    success_criteria: str = ""
    depends_on: list[str] = Field(default_factory=list)
    status: StepStatus = StepStatus.PENDING
    is_simple: bool = False  # 规划器标注：True=意图单一可直接执行，False=需子规划


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


class SessionThread(BaseModel):
    """会话主线：跨 turn 持续的主题/锚点摘要。

    由 `memory.session_thread.update_session_thread` 每轮结束后增量维护，
    持久化于 meta.json 的 `session_thread` 字段，用于让 Planner 识别当前任务属于同一主线延续。
    """

    summary: str = ""
    key_entities: list[str] = Field(default_factory=list)
    updated_at_turn: str = ""


class TeamResult(BaseModel):
    """Team 执行单个顶层 Step 的结果"""

    step_id: str
    passed: bool
    output: str = ""
    should_replan: bool = True  # 外层重规划参考：False 表示换方案也无意义
    sub_steps: list[StepResult] = Field(default_factory=list)


class AgentState(BaseModel):
    """一次任务执行的完整状态，贯穿整个循环"""

    session_id: str
    task: str
    turn_id: str = ""  # gateway 由 server 注入；CLI 由 cli.py 生成
    history: list[Message] = Field(default_factory=list)
    session_thread: SessionThread | None = None  # 跨轮主线摘要，Planner 用于保持主题锚定
    plan: Plan | None = None
    step_results: list[StepResult] = Field(default_factory=list)
    team_results: list[TeamResult] = Field(default_factory=list)
    thinking_level: ThinkingLevel = ThinkingLevel.MEDIUM
    aborted: bool = False
    created_at: datetime = Field(default_factory=datetime.now)
