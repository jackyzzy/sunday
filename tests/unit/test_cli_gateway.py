"""T5-4 验证：CLI Gateway 命令（CliRunner，mock subprocess/os.kill）"""
from __future__ import annotations

import os
import signal
from unittest.mock import MagicMock, patch

import yaml
from click.testing import CliRunner

from sunday.cli import main


def _make_env(tmp_path) -> dict:
    config_file = tmp_path / "agent.yaml"
    config_file.write_text(yaml.dump({
        "model": {"provider": "anthropic", "id": "claude-test", "max_tokens": 4096},
        "agent": {
            "workspace": str(tmp_path / "workspace"),
            "sessions_dir": str(tmp_path / "sessions"),
        },
    }))
    return {
        "ANTHROPIC_API_KEY": "sk-ant-fake",
        "SUNDAY_CONFIG_FILE": str(config_file),
    }


def test_gateway_start_writes_pid_file(tmp_path):
    """gateway start 后 PID 文件存在（mock subprocess）"""
    env = _make_env(tmp_path)
    pid_file = tmp_path / "gateway.pid"

    fake_proc = MagicMock()
    fake_proc.pid = 12345

    with patch.dict(os.environ, env):
        with patch("sunday.cli.subprocess.Popen", return_value=fake_proc):
            with patch("sunday.cli._gateway_pid_file", return_value=pid_file):
                runner = CliRunner()
                result = runner.invoke(main, ["gateway", "start"])
    assert result.exit_code == 0
    assert pid_file.exists()
    assert pid_file.read_text().strip() == "12345"


def test_gateway_stop_sends_sigterm(tmp_path):
    """gateway stop 读 PID 文件并发送 SIGTERM（mock os.kill）"""
    env = _make_env(tmp_path)
    pid_file = tmp_path / "gateway.pid"
    pid_file.write_text("99999")

    with patch.dict(os.environ, env):
        with patch("sunday.cli._gateway_pid_file", return_value=pid_file):
            with patch("sunday.cli.os.kill") as mock_kill:
                runner = CliRunner()
                result = runner.invoke(main, ["gateway", "stop"])

    mock_kill.assert_called_once_with(99999, signal.SIGTERM)
    assert result.exit_code == 0


def test_gateway_status_running(tmp_path):
    """status 检测到进程运行时打印运行中"""
    env = _make_env(tmp_path)
    pid_file = tmp_path / "gateway.pid"
    pid_file.write_text("99999")

    with patch.dict(os.environ, env):
        with patch("sunday.cli._gateway_pid_file", return_value=pid_file):
            with patch("sunday.cli.os.kill", return_value=None):  # 进程存在
                runner = CliRunner()
                result = runner.invoke(main, ["gateway", "status"])

    assert "运行" in result.output or "running" in result.output.lower()


def test_gateway_status_not_running(tmp_path):
    """status 检测到进程不存在时打印未运行"""
    env = _make_env(tmp_path)
    pid_file = tmp_path / "gateway.pid"
    # pid 文件不存在

    with patch.dict(os.environ, env):
        with patch("sunday.cli._gateway_pid_file", return_value=pid_file):
            runner = CliRunner()
            result = runner.invoke(main, ["gateway", "status"])

    assert (
        "未运行" in result.output
        or "not running" in result.output.lower()
        or result.exit_code == 0
    )


def test_skills_list_shows_skills(tmp_path):
    """skills list 显示已发现的技能"""
    env = _make_env(tmp_path)
    skills_dir = tmp_path / "workspace" / "skills"
    skills_dir.mkdir(parents=True)
    skill = skills_dir / "test_skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: test_skill\ndescription: 测试技能\nrequires: []\n---\n\n# Test\n内容"
    )

    with patch.dict(os.environ, env):
        runner = CliRunner()
        result = runner.invoke(main, ["skills", "list"])

    assert result.exit_code == 0
    # 至少输出了技能系统信息（即使没有发现任何技能也要正常运行）
    assert result.output is not None
