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
    requires_realtime_data: bool = False
    """规划器标注：本步骤是否需要实时数据（联网查询）。

    True 时：Executor 必须调用 web_search/fetch_url；若失败由代码兜底打"未联网"标签；
    Verifier 据此审计是否真有联网调用。False 时：纯写作/合成步骤，无需联网。
    默认 False 兼容老 plan JSON。
    """
    step_type: str | None = None
    """步骤类型提示，用于选择最优 executor / verifier prompt。

    通过命名约定路由：executor_{step_type}.md / verify_{step_type}.md。
    可用 step_type 由 configs/templates/step_types.yaml 描述。
    None 时 fallback 到默认 prompt（executor_system / verify）。
    """


class Plan(BaseModel):
    """Planner 输出，描述执行目标"""

    goal: str
    thinking: str | None = None
    steps: list[Step] = Field(default_factory=list)
    task_type: str | None = None
    """任务类型，由 Plan LLM 在 plan.md 输出。

    可用 task_type 由 configs/templates/*.yaml 自动注册（auto-discovery）。
    用于驱动后续策略选择（如 synthesis 步骤注入、catalog 提示）。
    """
    synthesis_document_name: str | None = None
    """综合整合文档名（仅当所选 task_type 模板的 synthesis.enabled=true 时使用）。

    Planner LLM 输出，应反映整体任务主题（而非单个推荐项）；LLM 漏掉时由
    Planner 用模板的 document_name_hint 兜底。
    """


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
