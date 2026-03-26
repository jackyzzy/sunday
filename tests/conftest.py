"""共用 pytest fixtures — 安全隔离所有测试"""
from __future__ import annotations

import pytest
import yaml


@pytest.fixture
def fake_settings(tmp_path):
    """返回指向临时目录的 Settings 实例，注入假 API key。

    不读取真实 .env，不操作 ~/.sunday/。
    """
    config_file = tmp_path / "agent.yaml"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sessions = tmp_path / "sessions"
    sessions.mkdir()

    config_file.write_text(
        yaml.dump({
            "agent": {
                "name": "TestSunday",
                "workspace_dir": str(workspace),
                "sessions_dir": str(sessions),
            },
            "model": {"provider": "anthropic", "id": "claude-test"},
        }),
        encoding="utf-8",
    )

    env_patch = {
        "ANTHROPIC_API_KEY": "sk-ant-fake-key",
        "OPENAI_API_KEY": "sk-openai-fake-key",
        "SUNDAY_CONFIG_FILE": str(config_file),
    }

    with pytest.MonkeyPatch.context() as mp:
        for k, v in env_patch.items():
            mp.setenv(k, v)
        from sunday.config import Settings
        s = Settings()
        yield s


@pytest.fixture
def mock_workspace(tmp_path):
    """创建标准 workspace 目录结构（5 个 .md 文件），返回目录路径。"""
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    (workspace / "SOUL.md").write_text(
        "# Sunday 的身份\n\n## 性格\n专注高效。\n\n## 能力边界\n本地运行。\n",
        encoding="utf-8",
    )
    (workspace / "AGENTS.md").write_text(
        "# 操作规则\n\n默认操作规则。\n",
        encoding="utf-8",
    )
    (workspace / "MEMORY.md").write_text(
        "# 长期记忆\n\n<!-- 由 AI 自动维护 -->\n",
        encoding="utf-8",
    )
    (workspace / "USER.md").write_text(
        "# 用户档案\n\n<!-- 用户信息 -->\n",
        encoding="utf-8",
    )
    (workspace / "TOOLS.md").write_text(
        "# 工具使用约定\n\n默认工具约定。\n",
        encoding="utf-8",
    )

    return workspace


@pytest.fixture
def minimal_yaml_config(tmp_path):
    """写入最小 agent.yaml（只含 agent.name），返回文件路径。"""
    config_file = tmp_path / "agent.yaml"
    config_file.write_text(
        yaml.dump({"agent": {"name": "MinimalSunday"}}),
        encoding="utf-8",
    )
    return config_file
