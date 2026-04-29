"""S4-B 验证：PromptResolver 二维查找 / fallback / loud fail。"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from sunday.agent.prompt_resolver import PromptResolver


def _make_settings(tmp_path: Path):
    from sunday.config import Settings

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "SOUL.md").write_text("# Sunday\n", encoding="utf-8")
    (tmp_path / "agent.yaml").write_text(yaml.dump({
        "agent": {
            "workspace_dir": str(workspace),
            "memory_dir": str(tmp_path / "memory"),
            "sessions_dir": str(tmp_path / "sessions"),
            "log_dir": str(tmp_path / "logs"),
        },
        "model": {"provider": "anthropic", "id": "claude-test"},
    }))
    with patch.dict(os.environ, {
        "ANTHROPIC_API_KEY": "sk-ant-fake",
        "SUNDAY_CONFIGS_DIR": str(tmp_path),
    }):
        s = Settings()
        _ = s.sunday
        return s


# ── role × task_type 命中 ────────────────────────────────────────────────────


def test_executor_generic_loads_executor_system():
    """executor + generic → executor_system.md（默认命名约定）。"""
    from sunday.config import settings
    r = PromptResolver(settings.sunday)
    text = r.resolve("executor", "generic")
    assert text  # 非空 = 加载成功


def test_executor_research_loads_specialized_file():
    from sunday.config import settings
    r = PromptResolver(settings.sunday)
    text = r.resolve("executor", "research")
    # 调研类 prompt 应包含特征关键词
    assert "调研" in text or "信息收集" in text or "research" in text.lower()


def test_executor_analysis_loads_specialized_file():
    from sunday.config import settings
    r = PromptResolver(settings.sunday)
    text = r.resolve("executor", "analysis")
    assert "分析" in text or "对比" in text


def test_executor_synthesis_loads_specialized_file():
    from sunday.config import settings
    r = PromptResolver(settings.sunday)
    text = r.resolve("executor", "synthesis")
    assert "整合" in text or "综合" in text


def test_verify_generic_loads_verify_md():
    from sunday.config import settings
    r = PromptResolver(settings.sunday)
    text = r.resolve("verify", "generic")
    assert "passed" in text  # verify.md 含 JSON 输出格式 schema


def test_verify_research_loads_specialized_file_stage4():
    """Stage 4 新增：verify_research.md。"""
    from sunday.config import settings
    r = PromptResolver(settings.sunday)
    text = r.resolve("verify", "research")
    assert "调研" in text or "来源" in text  # 含调研类专项标准


def test_verify_analysis_loads_specialized_file_stage4():
    """Stage 4 新增：verify_analysis.md。"""
    from sunday.config import settings
    r = PromptResolver(settings.sunday)
    text = r.resolve("verify", "analysis")
    assert "分析" in text or "结论" in text  # 含分析类专项标准


def test_verify_synthesis_loads_specialized_file():
    from sunday.config import settings
    r = PromptResolver(settings.sunday)
    text = r.resolve("verify", "synthesis")
    assert "passed" in text


# ── loud fail / 错误路径 ─────────────────────────────────────────────────────


def test_unknown_role_raises_value_error():
    from sunday.config import settings
    r = PromptResolver(settings.sunday)
    with pytest.raises(ValueError, match="未知 role"):
        r.resolve("unknown_role", "generic")


def test_missing_specialized_prompt_raises_loud(monkeypatch):
    """task_type 不是 generic 但文件不存在 → ValueError，明确指向需要新增的文件。"""
    from sunday.config import SundayConfig, settings

    def _missing(self, name: str) -> str:
        if name == "executor_xyz":
            raise FileNotFoundError(f"{name}.md not found")
        return "fallback"

    monkeypatch.setattr(SundayConfig, "load_prompt", _missing)
    r = PromptResolver(settings.sunday)
    with pytest.raises(ValueError, match="executor_xyz.md"):
        r.resolve("executor", "xyz")


def test_missing_default_prompt_for_generic_raises(monkeypatch):
    """generic 时默认文件缺失 = 配置错误，也要 loud fail。"""
    from sunday.config import SundayConfig, settings

    def _all_missing(self, name: str) -> str:
        raise FileNotFoundError(f"{name}.md not found")

    monkeypatch.setattr(SundayConfig, "load_prompt", _all_missing)
    r = PromptResolver(settings.sunday)
    with pytest.raises(ValueError, match="默认 prompt"):
        r.resolve("executor", "generic")


# ── 缓存 / 重复调用 ──────────────────────────────────────────────────────────


def test_resolver_caches_loaded_prompts(monkeypatch):
    """同 (role, task_type) 多次调用，load_prompt 只读一次。"""
    from sunday.config import SundayConfig, settings

    call_count = {"n": 0}

    def _counting_load(self, name: str) -> str:
        call_count["n"] += 1
        return "fake prompt content"

    monkeypatch.setattr(SundayConfig, "load_prompt", _counting_load)
    r = PromptResolver(settings.sunday)
    r.resolve("executor", "generic")
    r.resolve("executor", "generic")
    r.resolve("executor", "generic")
    assert call_count["n"] == 1


def test_supported_roles_lists_known_roles():
    roles = PromptResolver.supported_roles()
    assert "executor" in roles
    assert "verify" in roles
