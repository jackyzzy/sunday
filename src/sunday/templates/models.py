"""模板数据模型。

每个 TaskTypeTemplate 描述一个任务类型从规划到综合整合的完整端到端配置：
- plan_guidance：注入 plan prompt 的任务类型说明
- steps_scaffold：建议的步骤骨架（参考用，不强制）
- synthesis：综合整合步骤配置（是否需要、章节、命名提示）

step_type 的 executor / verifier prompt 路由通过命名约定自动完成
（executor_{step_type}.md / verify_{step_type}.md），不在模板中显式声明。
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class StepTypeMeta(BaseModel):
    """step_type 元信息（注册表 / 文档用途）。

    实际的 executor / verifier prompt 路由由 Executor / Verifier 通过命名约定
    `executor_{step_type}.md` / `verify_{step_type}.md` 完成，不依赖此元数据。
    本注册表用于让用户/工具发现"项目支持哪些 step_type"。
    """

    step_type: str
    description: str = ""
    notes: str = ""


class SynthesisConfig(BaseModel):
    """task_type 的 Synthesis 步骤配置。"""

    enabled: bool = False
    """是否需要在规划末尾自动注入 synthesis 步骤。

    - true：分析推荐、调研报告、代码任务等需要综合文档的场景
    - false：创意写作、简单问答等输出本身即终态的场景
    """

    required_sections: list[str] = Field(default_factory=list)
    """综合文档必需章节，注入 synthesis 步骤的 expected_output 与 success_criteria。"""

    document_name_hint: str = ""
    """文件命名提示。LLM 输出 synthesis_document_name 时参考；缺省时用作 fallback 命名。"""


class TaskTypeTemplate(BaseModel):
    """task_type 全链路配置。

    新增 task_type 仅需在 configs/templates/{task_type}.yaml 创建文件，
    无需修改任何代码（auto-discovery）。
    """

    task_type: str
    description: str = ""
    """简短描述，注入 plan prompt 的任务类型清单，帮 LLM 选型。"""

    trigger_hints: list[str] = Field(default_factory=list)
    """触发关键词，用于辅助 LLM 识别任务类型，注入 plan prompt 清单。"""

    plan_guidance: str = ""
    """该任务类型的规划阶段额外指导（多行文本）。

    Planner 在 LLM 已选定 task_type 之后可用作生成 synthesis 步骤的 intent
    上下文；当前作为元信息保留，未来可扩展用于 plan prompt 的二次优化。
    """

    steps_scaffold: list[dict] = Field(default_factory=list)
    """建议步骤骨架（参考用），注入 plan prompt 帮 LLM 规划。

    格式：[{role, step_type, hint}, ...]
    LLM 可灵活调整，不强制完全遵循。
    """

    synthesis: SynthesisConfig = Field(default_factory=SynthesisConfig)
    """Synthesis 步骤配置，驱动规划末尾的综合整合步骤注入。"""
