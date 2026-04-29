"""S1-D 验证：LocalWorkspaceClient.ensure_seeded 复制 L0 模板 + 创建用户扩展点。"""
from __future__ import annotations

from pathlib import Path

import pytest

from sunday.memory.local.workspace import LocalWorkspaceClient


def _make_template_dir(tmp_path: Path) -> Path:
    src = tmp_path / "project_workspace"
    src.mkdir()
    (src / "SOUL.md").write_text("soul template\n", encoding="utf-8")
    (src / "AGENTS.md").write_text("agents template\n", encoding="utf-8")
    (src / "TOOLS.md").write_text("tools template\n", encoding="utf-8")
    (src / "RUNTIME_RULES.md").write_text("rules template\n", encoding="utf-8")
    return src


@pytest.mark.asyncio
async def test_ensure_seeded_copies_missing_files(tmp_path: Path) -> None:
    template = _make_template_dir(tmp_path)
    workspace = tmp_path / "user_workspace"
    client = LocalWorkspaceClient(workspace)

    seeded = await client.ensure_seeded(template)

    assert set(seeded) == {"SOUL.md", "AGENTS.md", "TOOLS.md", "RUNTIME_RULES.md"}
    assert (workspace / "SOUL.md").read_text(encoding="utf-8") == "soul template\n"
    # 用户扩展点目录被创建
    assert (workspace / "templates").is_dir()
    assert (workspace / "skills").is_dir()


@pytest.mark.asyncio
async def test_ensure_seeded_is_idempotent(tmp_path: Path) -> None:
    template = _make_template_dir(tmp_path)
    workspace = tmp_path / "user_workspace"
    client = LocalWorkspaceClient(workspace)

    await client.ensure_seeded(template)
    # 第二次调用：什么都不该复制（已存在）
    seeded_again = await client.ensure_seeded(template)
    assert seeded_again == []


@pytest.mark.asyncio
async def test_ensure_seeded_does_not_overwrite_user_edits(tmp_path: Path) -> None:
    template = _make_template_dir(tmp_path)
    workspace = tmp_path / "user_workspace"
    workspace.mkdir()
    (workspace / "SOUL.md").write_text("user customized\n", encoding="utf-8")
    client = LocalWorkspaceClient(workspace)

    seeded = await client.ensure_seeded(template)
    assert "SOUL.md" not in seeded
    assert (workspace / "SOUL.md").read_text(encoding="utf-8") == "user customized\n"


@pytest.mark.asyncio
async def test_ensure_seeded_handles_missing_template_dir(tmp_path: Path) -> None:
    nonexistent = tmp_path / "no_such_template"
    workspace = tmp_path / "user_workspace"
    client = LocalWorkspaceClient(workspace)

    seeded = await client.ensure_seeded(nonexistent)
    assert seeded == []
    # 即使无模板，扩展点目录仍被创建
    assert (workspace / "templates").is_dir()
    assert (workspace / "skills").is_dir()
