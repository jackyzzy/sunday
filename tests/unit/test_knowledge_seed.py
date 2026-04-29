"""S1-D 验证：LocalKnowledgeClient.ensure_seeded 复制 L1 模板（MEMORY.md / USER.md）。"""
from __future__ import annotations

from pathlib import Path

import pytest

from sunday.memory.local.knowledge import LocalKnowledgeClient


def _make_template_dir(tmp_path: Path) -> Path:
    src = tmp_path / "project_workspace"
    src.mkdir()
    (src / "MEMORY.md").write_text("# Sunday 记忆\n", encoding="utf-8")
    (src / "USER.md").write_text("# 用户画像\n", encoding="utf-8")
    return src


@pytest.mark.asyncio
async def test_ensure_seeded_copies_missing_files(tmp_path: Path) -> None:
    template = _make_template_dir(tmp_path)
    memory_dir = tmp_path / "user_memory"
    client = LocalKnowledgeClient(memory_dir, run_janitor=False)

    seeded = await client.ensure_seeded(template)

    assert set(seeded) == {"MEMORY.md", "USER.md"}
    assert (memory_dir / "MEMORY.md").read_text(encoding="utf-8") == "# Sunday 记忆\n"
    assert (memory_dir / "USER.md").read_text(encoding="utf-8") == "# 用户画像\n"
    # daily 目录已创建
    assert (memory_dir / "daily").is_dir()


@pytest.mark.asyncio
async def test_ensure_seeded_is_idempotent(tmp_path: Path) -> None:
    template = _make_template_dir(tmp_path)
    memory_dir = tmp_path / "user_memory"
    client = LocalKnowledgeClient(memory_dir, run_janitor=False)

    await client.ensure_seeded(template)
    seeded_again = await client.ensure_seeded(template)
    assert seeded_again == []


@pytest.mark.asyncio
async def test_ensure_seeded_does_not_overwrite_user_edits(tmp_path: Path) -> None:
    template = _make_template_dir(tmp_path)
    memory_dir = tmp_path / "user_memory"
    memory_dir.mkdir()
    (memory_dir / "MEMORY.md").write_text("user accumulated facts\n", encoding="utf-8")
    client = LocalKnowledgeClient(memory_dir, run_janitor=False)

    seeded = await client.ensure_seeded(template)
    assert "MEMORY.md" not in seeded
    assert (memory_dir / "MEMORY.md").read_text(encoding="utf-8") == "user accumulated facts\n"


@pytest.mark.asyncio
async def test_ensure_seeded_handles_missing_template_dir(tmp_path: Path) -> None:
    nonexistent = tmp_path / "no_such_template"
    memory_dir = tmp_path / "user_memory"
    client = LocalKnowledgeClient(memory_dir, run_janitor=False)

    seeded = await client.ensure_seeded(nonexistent)
    assert seeded == []
    # 目录结构仍存在（在构造时已创建）
    assert memory_dir.is_dir()
    assert (memory_dir / "daily").is_dir()
