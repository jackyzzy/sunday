"""ContextBuilder L1/L2 跨会话背景节标题注入测试。"""
from __future__ import annotations

from datetime import date

import pytest

from sunday.memory.context import ContextBuilder
from sunday.memory.local import LocalMemoryClient


def _populate(workspace, memory_dir, with_l1_l2: bool) -> None:
    workspace.mkdir()
    memory_dir.mkdir()
    (memory_dir / "daily").mkdir()
    (workspace / "SOUL.md").write_text("# Soul", encoding="utf-8")
    if with_l1_l2:
        (memory_dir / "MEMORY.md").write_text("# Memory\n- 历史会话A", encoding="utf-8")
        today = date.today().isoformat()
        (memory_dir / "daily" / f"{today}.md").write_text(
            "# 今日\n- 工作内容", encoding="utf-8"
        )


def _make_client(tmp_path, with_l1_l2: bool):
    workspace = tmp_path / "workspace"
    memory_dir = tmp_path / "memory"
    _populate(workspace, memory_dir, with_l1_l2)
    return LocalMemoryClient(
        sessions_dir=tmp_path / "s",
        memory_dir=memory_dir,
        log_dir=tmp_path / "l",
        workspace_dir=workspace,
        run_janitor=False,
    )


@pytest.mark.asyncio
async def test_framing_header_present_when_l1_l2_exists(tmp_path):
    client = _make_client(tmp_path, with_l1_l2=True)
    try:
        ctx = await ContextBuilder(client).build()
    finally:
        await client.aclose()
    assert "# 跨会话长期背景（仅供参考，非当前会话历史）" in ctx.system_prompt
    header_idx = ctx.system_prompt.index("# 跨会话长期背景")
    memory_idx = ctx.system_prompt.index("历史会话A")
    assert header_idx < memory_idx


@pytest.mark.asyncio
async def test_framing_header_absent_when_no_l1_l2(tmp_path):
    client = _make_client(tmp_path, with_l1_l2=False)
    try:
        ctx = await ContextBuilder(client).build()
    finally:
        await client.aclose()
    assert "# 跨会话长期背景" not in ctx.system_prompt
