"""主题一致性检查器 — 验证步骤输出是否围绕当前任务的关键主题。

用于兜底检测"上下文串话"：最终报告的内容与当前 task/plan.goal 的主题脱钩，
而是被 L1/L2 跨会话背景里的其他话题占据。

Protocol + 工厂模式，便于以后替换为关键词匹配或小模型实现（只需改 config.quality.subject_consistency.checker）。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from sunday.agent.llm_client import LLMClient
from sunday.agent.utils import strip_code_fence

if TYPE_CHECKING:
    from sunday.config import ModelConfig, SundayConfig

logger = logging.getLogger(__name__)


@dataclass
class SubjectCheckResult:
    """主题一致性检查结果。"""

    consistent: bool
    reason: str


_PROMPT = """你是内容主题一致性审核员。

当前任务主题：{subjects}

实际输出（前 2000 字）：
{output}

请判断实际输出是否围绕"当前任务主题"展开。
- consistent=true：输出的核心内容与任务主题相关（即使细节不完整）
- consistent=false：输出的核心内容与任务主题脱钩（转去讨论了其他无关实体/话题）

只输出 JSON，不要任何额外说明：
{{"consistent": true/false, "reason": "一句话说明"}}
"""


class SubjectConsistencyChecker(Protocol):
    """主题一致性检查器接口。"""

    async def check(
        self, output: str, subjects: list[str]
    ) -> SubjectCheckResult: ...


class LLMSubjectChecker:
    """基于 LLM 的主题一致性检查器（默认实现）。temperature=0。"""

    def __init__(self, model_cfg: "ModelConfig") -> None:
        self._model_cfg = model_cfg

    async def check(
        self, output: str, subjects: list[str]
    ) -> SubjectCheckResult:
        if not subjects or not output.strip():
            return SubjectCheckResult(consistent=True, reason="主题或输出为空，跳过")

        subjects_text = "、".join(subjects)
        prompt = _PROMPT.format(
            subjects=subjects_text,
            output=output[:2000],
        )
        try:
            raw = await LLMClient.call_text(
                self._model_cfg, prompt, max_tokens=512, timeout=30
            )
        except Exception as e:
            logger.warning("主题一致性检查 LLM 调用失败（%s），默认通过", e)
            return SubjectCheckResult(
                consistent=True, reason=f"检查调用失败，默认通过：{e}"
            )

        text = strip_code_fence(raw)
        try:
            data = json.loads(text)
            return SubjectCheckResult(
                consistent=bool(data.get("consistent", True)),
                reason=str(data.get("reason", "")),
            )
        except json.JSONDecodeError:
            logger.warning("主题一致性响应解析失败，原文：%s", raw[:200])
            return SubjectCheckResult(
                consistent=True, reason=f"解析失败，默认通过：{raw[:100]}"
            )


class _AlwaysConsistentChecker:
    """关闭状态下使用的空实现，永远返回 consistent=True。"""

    async def check(
        self, output: str, subjects: list[str]
    ) -> SubjectCheckResult:
        return SubjectCheckResult(consistent=True, reason="主题一致性检查已禁用")


def build_subject_checker(config: "SundayConfig") -> SubjectConsistencyChecker:
    """根据 config.quality.subject_consistency 选择具体实现。

    - enabled=False → _AlwaysConsistentChecker（空实现）
    - checker="llm" → LLMSubjectChecker（默认）
    - checker="keyword" / "small_model" → 未来扩展，当前未实现则回退到 LLM
    """
    sc_cfg = config.quality.subject_consistency
    if not sc_cfg.enabled:
        return _AlwaysConsistentChecker()

    if sc_cfg.checker == "llm":
        return LLMSubjectChecker(config.model)

    logger.warning(
        "未知的主题一致性检查器类型 '%s'，回退到 LLMSubjectChecker", sc_cfg.checker
    )
    return LLMSubjectChecker(config.model)
