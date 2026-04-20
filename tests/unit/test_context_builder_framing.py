"""ContextBuilder L1/L2 跨会话背景节标题注入测试。"""
from __future__ import annotations

from datetime import date

from sunday.memory.context import ContextBuilder


def _make_workspace(tmp_path, with_l1_l2: bool = True):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    (memory_dir / "daily").mkdir()
    (workspace / "SOUL.md").write_text("# Soul", encoding="utf-8")
    if with_l1_l2:
        (memory_dir / "MEMORY.md").write_text("# Memory\n- 历史会话A", encoding="utf-8")
        today = date.today().isoformat()
        (memory_dir / "daily" / f"{today}.md").write_text(
            "# 今日\n- 工作内容", encoding="utf-8"
        )
    return workspace, memory_dir


def test_framing_header_present_when_l1_l2_exists(tmp_path):
    workspace, memory_dir = _make_workspace(tmp_path, with_l1_l2=True)
    cb = ContextBuilder(workspace, memory_dir=memory_dir)
    ctx = cb.build()
    assert "# 跨会话长期背景（仅供参考，非当前会话历史）" in ctx.system_prompt
    # 节标题应出现在 L1 内容之前
    header_idx = ctx.system_prompt.index("# 跨会话长期背景")
    memory_idx = ctx.system_prompt.index("历史会话A")
    assert header_idx < memory_idx


def test_framing_header_absent_when_no_l1_l2(tmp_path):
    workspace, memory_dir = _make_workspace(tmp_path, with_l1_l2=False)
    cb = ContextBuilder(workspace, memory_dir=memory_dir)
    ctx = cb.build()
    assert "# 跨会话长期背景" not in ctx.system_prompt
