"""共用 pytest fixtures — 安全隔离所有测试"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml


@pytest.fixture(autouse=True)
def _fake_api_keys(monkeypatch):
    """全局注入假 API key，使「在 LLM 调用时」才解析 key 的代码路径在测试中可用。

    背景：`model_cfg.get_api_key()` 在 LLM 调用时（而非 Settings 构造时）读 os.environ；
    很多测试只在 `Settings()` 构造期临时 patch key，调用期已退出 → 验证/规划的 LLM 调用
    误入 fail-open。这里在每个测试 session 内保证常用 provider 的 key 存在。

    仅注入实际使用的 provider；**不**注入 cohere/未配置 provider，以免破坏
    test_config 中「缺 key 应 raise」的负向用例（它们用本地 monkeypatch 覆盖为空值）。
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake-key")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-fake-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek-fake-key")


def seed_workspace(workspace_dir: Path) -> None:
    """测试辅助：在 tmp workspace 下写最小 L0 文件，让 assert_runtime_initialized 通过。

    集成测试构造 Service / ReactAgent 时调用，避免每个测试重复 mkdir + 写文件。
    """
    workspace_dir.mkdir(parents=True, exist_ok=True)
    soul = workspace_dir / "SOUL.md"
    if not soul.exists():
        soul.write_text("# Test Sunday\n", encoding="utf-8")


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
        "SUNDAY_CONFIGS_DIR": str(tmp_path),
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
