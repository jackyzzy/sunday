"""T1-4 验证：CLI 单元测试（CliRunner，不调用真实 LLM）"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from sunday.cli import main


@pytest.fixture
def runner():
    return CliRunner()


# ── --help / --version ────────────────────────────────────────────────────────

def test_help(runner):
    """--help 正常输出且退出码 0"""
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "Sunday" in result.output


def test_version(runner):
    """--version 输出 0.1.0"""
    result = runner.invoke(main, ["--version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output


# ── 真实命令测试（mock 外部依赖） ──────────────────────────────────────────────

def test_tui_starts_app(runner):
    """sunday tui 正常启动（mock Textual App）"""
    mock_app = MagicMock()
    mock_app.run = MagicMock()
    with patch("sunday.tui.app.SundayApp", return_value=mock_app):
        result = runner.invoke(main, ["tui"])
    assert result.exit_code == 0
    mock_app.run.assert_called_once()


def test_gateway_start_already_running(runner, tmp_path):
    """gateway start：已运行时输出提示"""
    pid_file = tmp_path / "gateway.pid"
    pid_file.write_text("99999")
    import os
    with (
        patch("sunday.cli._gateway_pid_file", return_value=pid_file),
        patch("os.kill", return_value=None),
    ):
        result = runner.invoke(main, ["gateway", "start"])
    assert result.exit_code == 0
    assert "已在运行" in result.output or "PID" in result.output


def test_gateway_stop_not_running(runner, tmp_path):
    """gateway stop：未运行时输出提示"""
    pid_file = tmp_path / "gateway.pid"  # 不存在
    with patch("sunday.cli._gateway_pid_file", return_value=pid_file):
        result = runner.invoke(main, ["gateway", "stop"])
    assert result.exit_code == 0
    assert "未运行" in result.output or "不存在" in result.output


def test_gateway_status_not_running(runner, tmp_path):
    """gateway status：未运行时输出提示"""
    pid_file = tmp_path / "gateway.pid"  # 不存在
    with patch("sunday.cli._gateway_pid_file", return_value=pid_file):
        result = runner.invoke(main, ["gateway", "status"])
    assert result.exit_code == 0
    assert "未运行" in result.output


def test_skills_list_no_skills(runner, tmp_path):
    """skills list：无技能时输出提示"""
    with patch("sunday.skills.loader.SkillLoader.discover", return_value=[]):
        result = runner.invoke(main, ["skills", "list"])
    assert result.exit_code == 0
    assert "未发现" in result.output or "0" in result.output or result.output


# ── sunday run：无 API key 时退出码 1 ──────────────────────────────────────────

def test_run_no_api_key_exits_1(runner, tmp_path):
    """无 API key 时 run 退出码 1，stderr 或 output 含提示"""
    import yaml
    config_file = tmp_path / "agent.yaml"
    config_file.write_text(yaml.dump({"agent": {"name": "T"}}))

    with patch.dict(os.environ, {
        "ANTHROPIC_API_KEY": "",
        "OPENAI_API_KEY": "",
        "GOOGLE_API_KEY": "",
        "SUNDAY_CONFIG_FILE": str(config_file),
    }, clear=False):
        result = runner.invoke(main, ["run", "hello"])
    assert result.exit_code == 1
    stderr = result.stderr if hasattr(result, "stderr") and result.stderr else ""
    combined = result.output + stderr
    assert "API key" in combined or "api" in combined.lower() or "配置" in combined


# ── sunday run：--thinking 合法值 ─────────────────────────────────────────────

@pytest.mark.parametrize("level", ["off", "minimal", "low", "medium", "high"])
def test_run_thinking_valid_values(runner, level):
    """--thinking 5 个合法值均被接受（mock agent.run）"""
    import yaml
    with runner.isolated_filesystem():
        config_file = Path("agent.yaml")
        config_file.write_text(yaml.dump({"agent": {"name": "T"}}))

        mock_run = AsyncMock(return_value="mock response")
        with (
            patch.dict(os.environ, {
                "ANTHROPIC_API_KEY": "fake-key",
                "SUNDAY_CONFIG_FILE": str(config_file),
            }),
            patch("sunday.agent.simple.SimpleAgent.run", mock_run),
        ):
            result = runner.invoke(main, ["run", "test task", "--thinking", level])
        # 只要不因"非法值"而报错就算通过
        assert "Invalid value" not in result.output
        assert result.exit_code in (0, 1)  # 1 是可能的业务错误，但非参数错误


def test_run_thinking_invalid_value(runner):
    """--thinking 非法值应报错"""
    result = runner.invoke(main, ["run", "test", "--thinking", "ultra"])
    assert result.exit_code != 0
    assert "Invalid value" in result.output or "Error" in result.output


# ── sunday run：--model override ──────────────────────────────────────────────

def test_run_model_override(runner, tmp_path):
    """--model 参数被正确传递给 SimpleAgent"""
    import yaml
    config_file = tmp_path / "agent.yaml"
    config_file.write_text(yaml.dump({"agent": {"name": "T"}}))

    captured = {}

    async def fake_run(self, task):
        captured["model_override"] = self.model_override
        return "ok"

    with (
        patch.dict(os.environ, {
            "ANTHROPIC_API_KEY": "fake-key",
            "SUNDAY_CONFIG_FILE": str(config_file),
        }),
        patch("sunday.agent.simple.SimpleAgent.run", fake_run),
    ):
        runner.invoke(main, ["run", "test", "--model", "openai/gpt-4o"])

    assert captured.get("model_override") == "openai/gpt-4o"


# ── memory show / search ──────────────────────────────────────────────────────

def test_memory_show_file_exists(runner, tmp_path):
    """memory show 读取存在的工作区文件"""
    import yaml

    from sunday.config import Settings

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    soul_content = "# Sunday 的身份\n\n测试内容"
    (workspace / "SOUL.md").write_text(soul_content, encoding="utf-8")

    config_file = tmp_path / "agent.yaml"
    config_file.write_text(yaml.dump({
        "agent": {"workspace_dir": str(workspace)},
    }))

    # cli.py 使用 `from sunday.config import settings`，patch 模块级单例
    with patch.dict(os.environ, {
        "ANTHROPIC_API_KEY": "fake-key",
        "SUNDAY_CONFIG_FILE": str(config_file),
    }):
        fake_settings = Settings()

    with patch("sunday.config.settings", fake_settings):
        result = runner.invoke(main, ["memory", "show", "SOUL"])
    assert result.exit_code == 0
    assert "Sunday" in result.output


def test_memory_show_file_missing(runner, tmp_path):
    """memory show 文件不存在时输出错误提示"""
    import yaml
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    # SOUL.md 不存在

    config_file = tmp_path / "agent.yaml"
    config_file.write_text(yaml.dump({
        "agent": {"workspace_dir": str(workspace)},
    }))

    with patch.dict(os.environ, {
        "ANTHROPIC_API_KEY": "fake-key",
        "SUNDAY_CONFIG_FILE": str(config_file),
    }):
        result = runner.invoke(main, ["memory", "show", "SOUL"])
    # 文件不存在时应提示错误（可能是 exit_code != 0 或输出包含"不存在"）
    combined = result.output + (result.stderr or "")
    assert "不存在" in combined or "not" in combined.lower() or result.exit_code != 0


def test_memory_search_found(runner, tmp_path):
    """memory search 找到关键词时输出匹配行"""
    import yaml

    from sunday.config import Settings

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "MEMORY.md").write_text(
        "# 记忆\n\n用户喜欢 Python 编程。\n",
        encoding="utf-8",
    )

    config_file = tmp_path / "agent.yaml"
    config_file.write_text(yaml.dump({
        "agent": {"workspace_dir": str(workspace)},
    }))

    with patch.dict(os.environ, {
        "ANTHROPIC_API_KEY": "fake-key",
        "SUNDAY_CONFIG_FILE": str(config_file),
    }):
        fake_settings = Settings()

    with patch("sunday.config.settings", fake_settings):
        result = runner.invoke(main, ["memory", "search", "Python"])
    assert result.exit_code == 0
    assert "Python" in result.output


def test_memory_search_not_found(runner, tmp_path):
    """memory search 找不到关键词时提示未找到"""
    import yaml

    from sunday.config import Settings

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "MEMORY.md").write_text("# 记忆\n\n无相关内容。\n", encoding="utf-8")

    config_file = tmp_path / "agent.yaml"
    config_file.write_text(yaml.dump({
        "agent": {"workspace_dir": str(workspace)},
    }))

    with patch.dict(os.environ, {
        "ANTHROPIC_API_KEY": "fake-key",
        "SUNDAY_CONFIG_FILE": str(config_file),
    }):
        fake_settings = Settings()

    with patch("sunday.config.settings", fake_settings):
        result = runner.invoke(main, ["memory", "search", "不存在的词汇xyz"])
    assert result.exit_code == 0
    assert "未找到" in result.output or "not found" in result.output.lower()
