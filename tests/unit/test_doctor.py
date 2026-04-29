"""S2-A 验证：sunday doctor 健康检查。"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from click.testing import CliRunner

from sunday.cli import main
from sunday.cli_doctor import (
    CheckLevel,
    _check_api_key,
    _check_runtime_dirs,
    _check_soul_not_empty,
    _check_template_diff,
)


def _make_settings(tmp_path: Path, *, with_soul: bool = True, with_dirs: bool = True):
    """构造 Settings，可选择性创建 workspace / SOUL。"""
    from sunday.config import Settings

    workspace = tmp_path / "workspace"
    if with_dirs:
        workspace.mkdir()
        (tmp_path / "memory").mkdir()
        (tmp_path / "sessions").mkdir()
        (tmp_path / "logs").mkdir()
    if with_soul:
        workspace.mkdir(exist_ok=True)
        (workspace / "SOUL.md").write_text("# Sunday 身份\n", encoding="utf-8")

    (tmp_path / "agent.yaml").write_text(yaml.dump({
        "agent": {
            "workspace_dir": str(workspace),
            "memory_dir": str(tmp_path / "memory"),
            "sessions_dir": str(tmp_path / "sessions"),
            "log_dir": str(tmp_path / "logs"),
        },
        "model": {"provider": "anthropic", "id": "claude-test", "api_key_env": "FAKE_KEY_X"},
    }))
    with patch.dict(os.environ, {
        "FAKE_KEY_X": "sk-fake",
        "SUNDAY_CONFIGS_DIR": str(tmp_path),
    }, clear=False):
        s = Settings()
        _ = s.sunday
        return s


# ── 单项 check ────────────────────────────────────────────────────────────────


def test_api_key_pass(tmp_path: Path):
    settings = _make_settings(tmp_path)
    with patch.dict(os.environ, {"FAKE_KEY_X": "sk-real"}):
        result = _check_api_key(settings.sunday)
    assert result.level == CheckLevel.PASS


def test_api_key_fail_when_missing(tmp_path: Path):
    settings = _make_settings(tmp_path)
    with patch.dict(os.environ, {"FAKE_KEY_X": ""}):
        result = _check_api_key(settings.sunday)
    assert result.level == CheckLevel.FAIL
    assert "FAKE_KEY_X" in result.message


def test_api_key_pass_for_local_model(tmp_path: Path):
    """api_key_env=None（如 ollama）→ 跳过 KEY 检查。"""
    settings = _make_settings(tmp_path)
    settings.sunday.model.api_key_env = None
    result = _check_api_key(settings.sunday)
    assert result.level == CheckLevel.PASS


def test_runtime_dirs_pass(tmp_path: Path):
    settings = _make_settings(tmp_path)
    result = _check_runtime_dirs(settings.sunday)
    assert result.level == CheckLevel.PASS


def test_runtime_dirs_fail_when_missing(tmp_path: Path):
    settings = _make_settings(tmp_path)
    # 删除一个目录
    import shutil
    shutil.rmtree(settings.sunday.agent.memory_dir)
    result = _check_runtime_dirs(settings.sunday)
    assert result.level == CheckLevel.FAIL
    assert "memory" in result.message


def test_soul_not_empty_pass(tmp_path: Path):
    settings = _make_settings(tmp_path)
    result = _check_soul_not_empty(settings.sunday)
    assert result.level == CheckLevel.PASS


def test_soul_not_empty_warn_when_empty(tmp_path: Path):
    settings = _make_settings(tmp_path)
    soul = settings.sunday.agent.workspace_dir / "SOUL.md"
    soul.write_text("", encoding="utf-8")
    result = _check_soul_not_empty(settings.sunday)
    assert result.level == CheckLevel.WARN


def test_soul_not_empty_fail_when_missing(tmp_path: Path):
    settings = _make_settings(tmp_path)
    soul = settings.sunday.agent.workspace_dir / "SOUL.md"
    soul.unlink()
    result = _check_soul_not_empty(settings.sunday)
    assert result.level == CheckLevel.FAIL


def test_template_diff_warns_when_diverged(tmp_path: Path):
    """项目模板 vs 用户模板内容不同 → WARN，输出 unified diff 行。

    `project_template_dir` 在 _check_template_diff 内部 lazy import；
    要 patch 必须 patch 源模块（sunday.bootstrap），而非 sunday.cli_doctor。
    """
    settings = _make_settings(tmp_path)
    template_dir = tmp_path.parent / "shadow_template"
    template_dir.mkdir(exist_ok=True)
    (template_dir / "SOUL.md").write_text(
        "# 项目模板\n## 行为边界\n- 不做 X\n- 不做 Y\n", encoding="utf-8",
    )
    # 用户改了 SOUL：删一行 + 加一行
    user_soul = settings.sunday.agent.workspace_dir / "SOUL.md"
    user_soul.write_text(
        "# 项目模板\n## 行为边界\n- 不做 X\n- 自定义：always 用中文\n",
        encoding="utf-8",
    )

    with patch("sunday.bootstrap.project_template_dir", return_value=template_dir):
        result = _check_template_diff(settings.sunday)

    assert result.level == CheckLevel.WARN
    assert "SOUL.md" in result.message
    # 验证 unified diff 出现 +/- 行
    assert "-- 不做 Y" in result.message or "-不做 Y" in result.message or "- 不做 Y" in result.message
    assert "自定义" in result.message  # 用户增加的行被打印


def test_template_diff_truncates_long_diff(tmp_path: Path):
    """单文件 diff 超过 _DIFF_MAX_LINES_PER_FILE 时截断并提示余量。"""
    from sunday.cli_doctor import _DIFF_MAX_LINES_PER_FILE

    settings = _make_settings(tmp_path)
    template_dir = tmp_path.parent / "shadow_template_long"
    template_dir.mkdir(exist_ok=True)
    # 模板含 50 行，每行不同 → diff 行数远超阈值
    (template_dir / "SOUL.md").write_text(
        "\n".join(f"template line {i}" for i in range(50)) + "\n",
        encoding="utf-8",
    )
    (settings.sunday.agent.workspace_dir / "SOUL.md").write_text(
        "\n".join(f"user line {i}" for i in range(50)) + "\n",
        encoding="utf-8",
    )

    with patch("sunday.bootstrap.project_template_dir", return_value=template_dir):
        result = _check_template_diff(settings.sunday)

    assert result.level == CheckLevel.WARN
    assert "已截断" in result.message
    # diff 行数应被裁到上限附近
    diff_line_count = result.message.count("\n    ")
    assert diff_line_count <= _DIFF_MAX_LINES_PER_FILE + 4  # +4 容错（header + 截断提示）


# ── CliRunner 端到端 ─────────────────────────────────────────────────────────
# 注：global `settings` 是 module-level 单例，CliRunner 跨测试时 cached_property
# 不会重置；这里只 smoke-test 命令能跑出来，深度逻辑由上面的单项 check 覆盖。


def test_doctor_command_runs_and_emits_output():
    """sunday doctor 命令能跑、输出包含核心检查项。"""
    runner = CliRunner()
    result = runner.invoke(main, ["doctor", "--skip-llm-ping"])
    # 退出码可能是 0 或 1（取决于全局 settings 状态），但必须有输出
    assert "API KEY" in result.output
    assert "运行时目录" in result.output
    assert "SOUL.md" in result.output
    assert "模板 diff" in result.output
