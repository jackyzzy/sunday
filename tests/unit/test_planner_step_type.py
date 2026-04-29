"""S1-F 验证：step.step_type 必填 Literal + Executor 显式 mapping。"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from sunday.agent.executor import Executor
from sunday.agent.models import Step
from sunday.config import settings as global_settings


def test_step_type_defaults_to_generic():
    """不指定 step_type 时默认 generic（合法默认值）。"""
    step = Step(id="s1", intent="hello")
    assert step.step_type == "generic"


def test_step_type_accepts_enum_values():
    """research / analysis / synthesis / generic 均合法。"""
    for value in ("research", "analysis", "synthesis", "generic"):
        step = Step(id=f"s_{value}", intent="x", step_type=value)
        assert step.step_type == value


def test_step_type_rejects_unknown_value():
    """非 enum 值被 Pydantic 拒绝（loud fail，非静默回退）。"""
    with pytest.raises(ValidationError):
        Step(id="s1", intent="x", step_type="creative")  # 非合法值

    with pytest.raises(ValidationError):
        Step(id="s2", intent="x", step_type="unknown")


def test_step_type_rejects_none():
    """None 不再合法（之前是 optional，现在必须是 enum 之一）。"""
    with pytest.raises(ValidationError):
        Step(id="s1", intent="x", step_type=None)


def test_executor_generic_uses_default_prompt():
    """step_type='generic' 显式映射到 executor_system.md（不是 fallback）。"""
    cfg = global_settings.sunday
    executor = Executor(cfg)
    prompt = executor._get_system_prompt("generic")
    assert prompt  # 加载到了
    # 非空字符串 = 加载成功


def test_executor_unknown_type_loud_fails(monkeypatch):
    """step_type 不是 generic 但缺对应 prompt 文件 → ValueError（不静默回退）。"""
    from sunday.config import SundayConfig

    cfg = global_settings.sunday
    executor = Executor(cfg)

    # 临时让 load_prompt 抛 FileNotFoundError，模拟"专项 prompt 不存在"
    def _missing_prompt(self, name: str) -> str:
        if name == "executor_research":
            raise FileNotFoundError(f"prompt {name}.md not found")
        return "fallback prompt"

    monkeypatch.setattr(SundayConfig, "load_prompt", _missing_prompt)

    with pytest.raises(ValueError, match="executor_research.md"):
        executor._get_system_prompt("research")
