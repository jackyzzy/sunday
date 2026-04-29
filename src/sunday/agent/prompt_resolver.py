"""PromptResolver —— role × task_type 二维查找的单点 prompt 解析器。

设计目标：
- Executor / Verifier 等所有 role 共用一份查找逻辑，不再各自实现 fallback。
- 加新 task_type = 加 enum 值 + 加 prompt 文件，不改代码（参见
  docs/extending-task-modes.md）。
- 行为对齐 Stage 1（S1-F）的"显式 mapping"原则：
    * task_type="generic" → 走 role 的 default 文件（每个 role 自定义命名）。
    * task_type ∈ {research, analysis, synthesis, ...} → 必须找到
      `{role}_{task_type}.md`，否则 raise ValueError（loud fail）。

唯一例外：role-level 的 quality 闸门（如 verifier 的
`quality.synthesis_quality_check.enabled`）允许显式回退，由调用方在
`resolve()` 之前把 task_type 改成 "generic" 即可（resolver 本身不感知）。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sunday.config import SundayConfig


# 每个 role 在 task_type=generic 时使用的默认 prompt 文件名（去掉 .md 扩展）。
# 历史命名约定：
#   - executor 默认是 executor_system.md（Phase 1 命名延续）
#   - verify 默认是 verify.md（无后缀）
# 加新 role：在此 dict 加一条；其他 task_type 仍按 `{role}_{task_type}.md` 自动命中。
_ROLE_DEFAULTS: dict[str, str] = {
    "executor": "executor_system",
    "verify": "verify",
}


class PromptResolver:
    """按 role × task_type 加载 prompt，单点 fallback / loud fail。"""

    def __init__(self, config: "SundayConfig") -> None:
        self.config = config
        self._cache: dict[str, str] = {}  # 文件名 → 文本

    def resolve(self, role: str, task_type: str = "generic") -> str:
        """加载并返回 prompt 文本。

        - role: "executor" / "verify"（key in _ROLE_DEFAULTS）
        - task_type: "generic" / "research" / "analysis" / "synthesis" / 未来扩展

        语义：
        - task_type="generic" → 加载 _ROLE_DEFAULTS[role] 文件
        - 其他 task_type → 加载 `{role}_{task_type}.md`，找不到 raise ValueError
        - 缓存基于文件名 key，多次调用同一 (role, task_type) 不重复 IO
        """
        if role not in _ROLE_DEFAULTS:
            raise ValueError(
                f"未知 role={role!r}（合法值：{sorted(_ROLE_DEFAULTS)}）。"
                f"加新 role 请编辑 sunday/agent/prompt_resolver.py 的 _ROLE_DEFAULTS。"
            )

        prompt_name = self._prompt_name(role, task_type)
        if prompt_name in self._cache:
            return self._cache[prompt_name]

        try:
            text = self.config.load_prompt(prompt_name)
        except FileNotFoundError as e:
            if task_type == "generic":
                # generic 必须有默认文件存在；缺失是配置错误
                raise ValueError(
                    f"role={role!r} 的默认 prompt 文件 {prompt_name}.md 缺失：{e}",
                ) from e
            raise ValueError(
                f"role={role!r} task_type={task_type!r} 要求 {prompt_name}.md 存在，"
                f"但加载失败：{e}。"
                f"请新增该 prompt 文件，或把 step_type 改为 generic。"
                f"参见 docs/extending-task-modes.md。",
            ) from e

        self._cache[prompt_name] = text
        return text

    @staticmethod
    def _prompt_name(role: str, task_type: str) -> str:
        if task_type == "generic":
            return _ROLE_DEFAULTS[role]
        return f"{role}_{task_type}"

    @classmethod
    def supported_roles(cls) -> list[str]:
        """返回当前注册的 role 列表（供调试 / docs 使用）。"""
        return sorted(_ROLE_DEFAULTS)
