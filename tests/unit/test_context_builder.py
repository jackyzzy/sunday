"""ContextBuilder 单元测试 — 通过 LocalMemoryClient 注入。"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from sunday.memory.context import ContextBuilder
from sunday.memory.local import LocalMemoryClient


@pytest.fixture
async def workspace_client(tmp_path):
    """构造 client 并预填默认 workspace + L1/L2 内容。"""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    (memory_dir / "daily").mkdir()
    (workspace / "SOUL.md").write_text("# Soul\n你是 Sunday。", encoding="utf-8")
    (workspace / "AGENTS.md").write_text("# Agents\n多 Agent 规范。", encoding="utf-8")
    (memory_dir / "MEMORY.md").write_text("# Memory\n- [P1] 偏好：简洁。", encoding="utf-8")
    (memory_dir / "USER.md").write_text("# User\n- 姓名：张三", encoding="utf-8")
    (workspace / "TOOLS.md").write_text("# Tools\n可用工具列表。", encoding="utf-8")

    client = LocalMemoryClient(
        sessions_dir=tmp_path / "s",
        memory_dir=memory_dir,
        log_dir=tmp_path / "l",
        workspace_dir=workspace,
        run_janitor=False,
    )
    yield client, workspace, memory_dir
    await client.aclose()


# ── 基本构建 ──────────────────────────────────────────────────────────────────

async def test_build_returns_nonempty_prompt(workspace_client):
    client, _, _ = workspace_client
    ctx = await ContextBuilder(client).build()
    assert ctx.system_prompt.strip()


async def test_build_includes_soul_content(workspace_client):
    client, _, _ = workspace_client
    ctx = await ContextBuilder(client).build()
    assert "你是 Sunday" in ctx.system_prompt


async def test_build_includes_current_date(workspace_client):
    client, _, _ = workspace_client
    ctx = await ContextBuilder(client).build()
    today = date.today().isoformat()
    assert today in ctx.system_prompt


async def test_build_token_estimate(workspace_client):
    client, _, _ = workspace_client
    ctx = await ContextBuilder(client).build()
    assert ctx.token_estimate == len(ctx.system_prompt) // 4


async def test_build_missing_file_silently_skipped(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "SOUL.md").write_text("# Soul")
    client = LocalMemoryClient(
        sessions_dir=tmp_path / "s",
        memory_dir=tmp_path / "m",
        log_dir=tmp_path / "l",
        workspace_dir=workspace,
        run_janitor=False,
    )
    try:
        ctx = await ContextBuilder(client).build()
    finally:
        await client.aclose()
    assert ctx.system_prompt.strip()


# ── MEMORY.md 截断 ────────────────────────────────────────────────────────────

async def test_build_memory_md_tail_truncated(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    lines = [f"行{i:03d}" for i in range(200)]
    (memory_dir / "MEMORY.md").write_text("\n".join(lines))
    client = LocalMemoryClient(
        sessions_dir=tmp_path / "s",
        memory_dir=memory_dir,
        log_dir=tmp_path / "l",
        workspace_dir=workspace,
        run_janitor=False,
    )
    try:
        ctx = await ContextBuilder(client, l0_max_lines=10).build()
    finally:
        await client.aclose()
    assert "行199" in ctx.system_prompt
    assert "行000" not in ctx.system_prompt


# ── 日志文件注入 ──────────────────────────────────────────────────────────────

async def test_build_includes_today_log(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    daily = memory_dir / "daily"
    daily.mkdir()
    today = date.today().isoformat()
    (daily / f"{today}.md").write_text("今日任务：写测试")
    client = LocalMemoryClient(
        sessions_dir=tmp_path / "s",
        memory_dir=memory_dir,
        log_dir=tmp_path / "l",
        workspace_dir=workspace,
        run_janitor=False,
    )
    try:
        ctx = await ContextBuilder(client).build()
    finally:
        await client.aclose()
    assert "今日任务" in ctx.system_prompt


async def test_build_includes_yesterday_log(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    daily = memory_dir / "daily"
    daily.mkdir()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    (daily / f"{yesterday}.md").write_text("昨日完成：写代码")
    client = LocalMemoryClient(
        sessions_dir=tmp_path / "s",
        memory_dir=memory_dir,
        log_dir=tmp_path / "l",
        workspace_dir=workspace,
        run_janitor=False,
    )
    try:
        ctx = await ContextBuilder(client).build()
    finally:
        await client.aclose()
    assert "昨日完成" in ctx.system_prompt


# ── 技能摘要 ──────────────────────────────────────────────────────────────────

async def test_build_with_skill_loader(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()

    class FakeLoader:
        def get_summary_list(self):
            return "- web_search: 搜索网络"

    client = LocalMemoryClient(
        sessions_dir=tmp_path / "s",
        memory_dir=memory_dir,
        log_dir=tmp_path / "l",
        workspace_dir=workspace,
        run_janitor=False,
    )
    try:
        ctx = await ContextBuilder(client, skill_loader=FakeLoader()).build()
    finally:
        await client.aclose()
    assert "web_search" in ctx.system_prompt
