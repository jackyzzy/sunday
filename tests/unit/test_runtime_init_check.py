"""S1-G 验证：assert_runtime_initialized 冷启动检查。"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from sunday.bootstrap import RuntimeNotInitializedError, assert_runtime_initialized


def _make_settings(tmp_path: Path):
    from sunday.config import Settings

    config_file = tmp_path / "agent.yaml"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_file.write_text(yaml.dump({
        "agent": {
            "workspace_dir": str(workspace),
            "sessions_dir": str(tmp_path / "sessions"),
        },
        "model": {"provider": "anthropic", "id": "claude-test"},
    }))
    with patch.dict(os.environ, {
        "ANTHROPIC_API_KEY": "sk-ant-fake",
        "SUNDAY_CONFIGS_DIR": str(tmp_path),
    }):
        s = Settings()
        _ = s.sunday  # 强制触发 cached_property，固化配置加载路径
        return s


def test_assert_runtime_initialized_fails_when_soul_missing(tmp_path):
    """SOUL.md 缺失时 raise RuntimeNotInitializedError，提示 sunday init。"""
    settings = _make_settings(tmp_path)
    # workspace 目录存在但 SOUL.md 不存在
    with pytest.raises(RuntimeNotInitializedError, match="sunday init"):
        assert_runtime_initialized(settings.sunday)


def test_assert_runtime_initialized_passes_when_soul_exists(tmp_path):
    """SOUL.md 存在时直接通过（不做任何 IO）。"""
    settings = _make_settings(tmp_path)
    soul = settings.sunday.agent.workspace_dir / "SOUL.md"
    soul.write_text("# Sunday\n", encoding="utf-8")

    # 不抛异常即视为通过
    assert_runtime_initialized(settings.sunday)


def test_react_agent_construction_fails_without_soul(tmp_path):
    """未 init 时构造 ReactAgent 直接报错（不再自动补救）。"""
    from sunday.agent.react_agent import ReactAgent
    from sunday.bootstrap import build_memory_client

    settings = _make_settings(tmp_path)
    client = build_memory_client(settings.sunday, run_janitor=False)

    try:
        with pytest.raises(RuntimeNotInitializedError):
            ReactAgent(config=settings.sunday, memory_client=client)
    finally:
        # 用同步方式释放（async aclose 在测试外不方便，但 client 的资源是文件句柄，
        # 进程退出时会被回收；这里只是显式表达意图）
        pass
