"""S2-C 验证：build_memory_client 按 cfg.memory.backend 派发。
S2-E 一并验证：janitor 由 mode 决定（service=on，其他=off）。"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from sunday.bootstrap import build_memory_client
from sunday.memory.local import LocalMemoryClient


def _make_settings(tmp_path: Path, **memory_overrides):
    """构造 Settings，可注入 memory backend 覆盖。"""
    from sunday.config import Settings

    config_file = tmp_path / "agent.yaml"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "SOUL.md").write_text("# Sunday\n", encoding="utf-8")

    memory_section = {"log_retention_days": 30, **memory_overrides}
    config_file.write_text(yaml.dump({
        "agent": {
            "workspace_dir": str(workspace),
            "memory_dir": str(tmp_path / "memory"),
            "sessions_dir": str(tmp_path / "sessions"),
            "log_dir": str(tmp_path / "logs"),
        },
        "model": {"provider": "anthropic", "id": "claude-test"},
        "memory": memory_section,
    }))
    with patch.dict(os.environ, {
        "ANTHROPIC_API_KEY": "sk-ant-fake",
        "SUNDAY_CONFIGS_DIR": str(tmp_path),
    }):
        s = Settings()
        _ = s.sunday
        return s


def test_default_backend_is_local(tmp_path: Path):
    settings = _make_settings(tmp_path)
    assert settings.sunday.memory.backend == "local"


def test_build_memory_client_local_returns_local_impl(tmp_path: Path):
    settings = _make_settings(tmp_path)
    client = build_memory_client(settings.sunday, mode="cli")
    try:
        assert isinstance(client, LocalMemoryClient)
    finally:
        import asyncio
        asyncio.run(client.aclose())


def test_build_memory_client_http_raises_not_implemented(tmp_path: Path):
    settings = _make_settings(tmp_path, backend="http")
    with pytest.raises(NotImplementedError, match="HTTP"):
        build_memory_client(settings.sunday)


def test_unknown_backend_raises_value_error(tmp_path: Path, monkeypatch):
    """未知 backend 值（绕过 Pydantic 校验）抛 ValueError。"""
    settings = _make_settings(tmp_path)
    # 直接 monkeypatch 字段（pydantic 默认禁止 assignment，但这里我们绕过 _build 逻辑）
    monkeypatch.setattr(
        type(settings.sunday.memory),
        "model_config",
        {**settings.sunday.memory.model_config, "frozen": False},
        raising=False,
    )
    object.__setattr__(settings.sunday.memory, "backend", "alien")
    with pytest.raises(ValueError, match="memory.backend"):
        build_memory_client(settings.sunday)


def _captured_run_janitor(tmp_path: Path, **build_kwargs) -> bool:
    """构造 client 并捕获 LocalKnowledgeClient 收到的 run_janitor 参数。

    janitor 任务是否真的启动取决于运行时是否有 event loop（同步测试上下文下不启动），
    因此只验证参数透传是否符合 mode 派发预期。
    """
    settings = _make_settings(tmp_path)
    captured = {}
    real_init = LocalMemoryClient.__init__

    def fake_init(self, *, run_janitor: bool, **kwargs):
        captured["run_janitor"] = run_janitor
        real_init(self, run_janitor=run_janitor, **kwargs)

    with patch.object(LocalMemoryClient, "__init__", fake_init):
        client = build_memory_client(settings.sunday, **build_kwargs)
    import asyncio
    asyncio.run(client.aclose())
    return captured["run_janitor"]


def test_service_mode_enables_janitor(tmp_path: Path):
    """mode="service" → run_janitor=True。"""
    assert _captured_run_janitor(tmp_path, mode="service") is True


def test_cli_mode_disables_janitor(tmp_path: Path):
    """mode="cli" → run_janitor=False。"""
    assert _captured_run_janitor(tmp_path, mode="cli") is False


def test_no_mode_defaults_to_no_janitor(tmp_path: Path):
    """未指定 mode → run_janitor=False（方向 1：默认不长驻）。"""
    assert _captured_run_janitor(tmp_path) is False


def test_explicit_run_janitor_overrides_mode(tmp_path: Path):
    """显式 run_janitor=True 强制开启，即使 mode=cli。"""
    assert _captured_run_janitor(tmp_path, mode="cli", run_janitor=True) is True
