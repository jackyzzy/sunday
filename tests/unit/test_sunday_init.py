"""S1-E 验证：sunday init 命令。"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import yaml

from sunday.cli_init import (
    PROVIDERS,
    _provider_already_configured,
    _scan_env_file,
    _update_agent_yaml,
    _write_env_file,
)

# ── 单元：纯函数 ──────────────────────────────────────────────────────────────


def test_scan_env_file_skips_placeholders(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "ANTHROPIC_API_KEY=sk-ant-...\n"  # 占位符
        "OPENAI_API_KEY=sk-real-1234\n"   # 真实值
        "DEEPSEEK_API_KEY=\n"             # 空值
        "# QWEN_API_KEY=...\n"            # 注释
        "TAVILY_API_KEY=tvly-real-key\n",
        encoding="utf-8",
    )
    keys = _scan_env_file(env)
    assert "OPENAI_API_KEY" in keys
    assert keys["OPENAI_API_KEY"] == "sk-real-1234"
    assert "TAVILY_API_KEY" in keys
    assert "ANTHROPIC_API_KEY" not in keys  # 占位符
    assert "DEEPSEEK_API_KEY" not in keys   # 空值
    assert "QWEN_API_KEY" not in keys       # 注释


def test_scan_env_file_handles_missing(tmp_path: Path) -> None:
    assert _scan_env_file(tmp_path / "missing.env") == {}


def test_provider_already_configured_for_ollama() -> None:
    ollama = next(p for p in PROVIDERS if p.key == "ollama")
    assert _provider_already_configured(ollama, env_keys={}) is True  # 无需 KEY


def test_provider_already_configured_checks_env() -> None:
    deepseek = next(p for p in PROVIDERS if p.key == "deepseek")
    assert _provider_already_configured(deepseek, {"DEEPSEEK_API_KEY": "x"}) is True
    assert _provider_already_configured(deepseek, {"OPENAI_API_KEY": "x"}) is False


def test_update_agent_yaml_writes_model_section(tmp_path: Path) -> None:
    yaml_path = tmp_path / "agent.yaml"
    yaml_path.write_text(
        yaml.dump({"agent": {"name": "Sunday"}, "model": {"provider": "old"}}),
        encoding="utf-8",
    )
    deepseek = next(p for p in PROVIDERS if p.key == "deepseek")
    _update_agent_yaml(yaml_path, deepseek)

    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    assert data["model"]["provider"] == "openai"
    assert data["model"]["id"] == "deepseek-chat"
    assert data["model"]["base_url"] == "https://api.deepseek.com/v1"
    assert data["model"]["api_key_env"] == "DEEPSEEK_API_KEY"
    # agent 节保留
    assert data["agent"]["name"] == "Sunday"


def test_update_agent_yaml_creates_file_if_missing(tmp_path: Path) -> None:
    yaml_path = tmp_path / "agent.yaml"
    anthropic = next(p for p in PROVIDERS if p.key == "anthropic")
    _update_agent_yaml(yaml_path, anthropic)
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    assert data["model"]["provider"] == "anthropic"
    assert "base_url" not in data["model"]  # anthropic 不需要


def test_update_agent_yaml_removes_stale_base_url(tmp_path: Path) -> None:
    """从 deepseek 切到 anthropic 时，旧 base_url 应被清掉。"""
    yaml_path = tmp_path / "agent.yaml"
    yaml_path.write_text(
        yaml.dump({"model": {
            "provider": "openai", "id": "deepseek-chat",
            "base_url": "https://api.deepseek.com/v1",
            "api_key_env": "DEEPSEEK_API_KEY",
        }}),
        encoding="utf-8",
    )
    anthropic = next(p for p in PROVIDERS if p.key == "anthropic")
    _update_agent_yaml(yaml_path, anthropic)
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    assert "base_url" not in data["model"]


def test_write_env_file_creates_new(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    deepseek = next(p for p in PROVIDERS if p.key == "deepseek")
    written = _write_env_file(env, deepseek, "sk-real-key")
    assert written is True
    assert "DEEPSEEK_API_KEY=sk-real-key" in env.read_text(encoding="utf-8")


def test_write_env_file_appends_without_destroying_existing(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("OPENAI_API_KEY=sk-real-1234\n", encoding="utf-8")
    deepseek = next(p for p in PROVIDERS if p.key == "deepseek")
    _write_env_file(env, deepseek, "sk-deepseek-real")
    content = env.read_text(encoding="utf-8")
    assert "OPENAI_API_KEY=sk-real-1234" in content
    assert "DEEPSEEK_API_KEY=sk-deepseek-real" in content


def test_write_env_file_replaces_placeholder(tmp_path: Path) -> None:
    """已存在但是占位符的 KEY 行被替换。"""
    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_API_KEY=sk-ant-...\n", encoding="utf-8")
    anthropic = next(p for p in PROVIDERS if p.key == "anthropic")
    _write_env_file(env, anthropic, "sk-ant-real")
    assert "ANTHROPIC_API_KEY=sk-ant-real" in env.read_text(encoding="utf-8")


def test_write_env_file_writes_placeholder_when_empty(tmp_path: Path) -> None:
    """key_value="" 时写注释占位（fill before sunday run）。"""
    env = tmp_path / ".env"
    deepseek = next(p for p in PROVIDERS if p.key == "deepseek")
    _write_env_file(env, deepseek, "")
    assert "# DEEPSEEK_API_KEY=  # fill before sunday run" in env.read_text(encoding="utf-8")


def test_write_env_file_skips_when_no_key_value(tmp_path: Path) -> None:
    """key_value=None（已配置）时不写。"""
    env = tmp_path / ".env"
    env.write_text("OPENAI_API_KEY=sk-real\n", encoding="utf-8")
    openai = next(p for p in PROVIDERS if p.key == "openai")
    written = _write_env_file(env, openai, None)
    assert written is False
    assert env.read_text(encoding="utf-8") == "OPENAI_API_KEY=sk-real\n"


# ── 集成：CliRunner 全流程 ───────────────────────────────────────────────────


def test_init_command_seeds_workspace_and_writes_yaml(tmp_path: Path) -> None:
    """sunday init 端到端：选 deepseek + 提供 KEY → agent.yaml 改写 + .env 写入 + L0/L1 seed。"""
    from click.testing import CliRunner

    from sunday.cli import main

    # 在 tmp_path 下复刻最小项目结构
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "agent.yaml").write_text(
        yaml.dump({
            "agent": {
                "name": "Sunday",
                "workspace_dir": str(tmp_path / "_sunday" / "workspace"),
                "memory_dir": str(tmp_path / "_sunday" / "memory"),
                "sessions_dir": str(tmp_path / "_sunday" / "sessions"),
                "log_dir": str(tmp_path / "_sunday" / "logs"),
            },
            "model": {"provider": "anthropic", "id": "old"},
        }),
        encoding="utf-8",
    )
    # 模拟项目模板
    template = tmp_path / "workspace"
    template.mkdir()
    (template / "SOUL.md").write_text("# Sunday template\n", encoding="utf-8")
    (template / "AGENTS.md").write_text("# agents\n", encoding="utf-8")
    (template / "TOOLS.md").write_text("# tools\n", encoding="utf-8")
    (template / "RUNTIME_RULES.md").write_text("# rules\n", encoding="utf-8")
    (template / "MEMORY.md").write_text("# memory\n", encoding="utf-8")
    (template / "USER.md").write_text("# user\n", encoding="utf-8")

    runner = CliRunner()
    with patch.dict(os.environ, {
        "ANTHROPIC_API_KEY": "sk-ant-fake",
        "SUNDAY_CONFIGS_DIR": str(tmp_path / "configs"),
    }):
        # 用 chdir 切到 tmp_path 让 init 把 .env 写到这里
        with runner.isolated_filesystem(temp_dir=tmp_path) as iso:
            iso_path = Path(iso)
            # 复制配置目录到 isolated fs（init 用 cwd 定位）
            import shutil
            shutil.copytree(tmp_path / "configs", iso_path / "configs")
            shutil.copytree(template, iso_path / "workspace")

            # 重新指向 isolated fs 内的 configs 和 workspace_dir
            iso_yaml = iso_path / "configs" / "agent.yaml"
            iso_yaml.write_text(
                yaml.dump({
                    "agent": {
                        "name": "Sunday",
                        "workspace_dir": str(iso_path / "_sunday" / "workspace"),
                        "memory_dir": str(iso_path / "_sunday" / "memory"),
                        "sessions_dir": str(iso_path / "_sunday" / "sessions"),
                        "log_dir": str(iso_path / "_sunday" / "logs"),
                    },
                    "model": {"provider": "anthropic", "id": "old"},
                }),
                encoding="utf-8",
            )

            with patch.dict(os.environ, {
                "ANTHROPIC_API_KEY": "sk-ant-fake",
                "SUNDAY_CONFIGS_DIR": str(iso_path / "configs"),
            }):
                # stdin: 选项 3 = deepseek；getpass 模拟通过 patch
                with patch("sunday.cli_init.getpass.getpass", return_value="sk-deepseek-test"):
                    result = runner.invoke(main, ["init"], input="3\n")

            assert result.exit_code == 0, f"exit={result.exit_code}\n{result.output}"

            # 检查 agent.yaml 被改写
            data = yaml.safe_load(iso_yaml.read_text(encoding="utf-8"))
            assert data["model"]["provider"] == "openai"
            assert data["model"]["id"] == "deepseek-chat"
            assert data["model"]["api_key_env"] == "DEEPSEEK_API_KEY"

            # 检查 .env 被写入
            env_content = (iso_path / ".env").read_text(encoding="utf-8")
            assert "DEEPSEEK_API_KEY=sk-deepseek-test" in env_content

            # 检查 L0/L1 文件已 seed 到 user 路径
            assert (iso_path / "_sunday" / "workspace" / "SOUL.md").exists()
            assert (iso_path / "_sunday" / "memory" / "MEMORY.md").exists()
