"""T3-2 验证：ContextBuilder 单元测试（真实文件系统）"""
from __future__ import annotations

from datetime import date, timedelta

from sunday.memory.context import ContextBuilder


def _make_workspace(tmp_path, files: dict[str, str] | None = None) -> None:
    """创建标准 workspace 目录结构"""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "memory").mkdir()
    defaults = {
        "SOUL.md": "# Soul\n你是 Sunday。",
        "AGENTS.md": "# Agents\n多 Agent 规范。",
        "MEMORY.md": "# Memory\n- [P1] 偏好：简洁。",
        "USER.md": "# User\n- 姓名：张三",
        "TOOLS.md": "# Tools\n可用工具列表。",
    }
    for name, content in (files or defaults).items():
        (workspace / name).write_text(content, encoding="utf-8")
    return workspace


# ── 基本构建 ──────────────────────────────────────────────────────────────────

def test_build_returns_nonempty_prompt(tmp_path):
    """build() 返回非空 system_prompt"""
    workspace = _make_workspace(tmp_path)
    cb = ContextBuilder(workspace)
    ctx = cb.build()
    assert ctx.system_prompt.strip()


def test_build_includes_soul_content(tmp_path):
    """system_prompt 包含 SOUL.md 内容"""
    workspace = _make_workspace(tmp_path)
    cb = ContextBuilder(workspace)
    ctx = cb.build()
    assert "你是 Sunday" in ctx.system_prompt


def test_build_includes_current_date(tmp_path):
    """system_prompt 包含当前日期"""
    workspace = _make_workspace(tmp_path)
    cb = ContextBuilder(workspace)
    ctx = cb.build()
    today = date.today().isoformat()
    assert today in ctx.system_prompt


def test_build_token_estimate(tmp_path):
    """token_estimate = len(system_prompt) // 4"""
    workspace = _make_workspace(tmp_path)
    cb = ContextBuilder(workspace)
    ctx = cb.build()
    assert ctx.token_estimate == len(ctx.system_prompt) // 4


def test_build_missing_file_silently_skipped(tmp_path):
    """不存在的文件静默跳过，不报错"""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "memory").mkdir()
    (workspace / "SOUL.md").write_text("# Soul")
    cb = ContextBuilder(workspace)
    ctx = cb.build()  # AGENTS.md MEMORY.md 等不存在
    assert ctx.system_prompt.strip()


# ── MEMORY.md 截断 ────────────────────────────────────────────────────────────

def test_build_memory_md_tail_truncated(tmp_path):
    """MEMORY.md 超过 l0_max_lines 时只取末尾行"""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "memory").mkdir()
    # 写 200 行，每行唯一
    lines = [f"行{i:03d}" for i in range(200)]
    (workspace / "MEMORY.md").write_text("\n".join(lines))
    cb = ContextBuilder(workspace, l0_max_lines=10)
    ctx = cb.build()
    # 只取最后 10 行 (行190~199)
    assert "行199" in ctx.system_prompt
    assert "行000" not in ctx.system_prompt


# ── 日志文件注入 ──────────────────────────────────────────────────────────────

def test_build_includes_today_log(tmp_path):
    """存在今日日志时包含在 system_prompt 中"""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    memory_dir = workspace / "memory"
    memory_dir.mkdir()
    today = date.today().isoformat()
    (memory_dir / f"{today}.md").write_text("今日任务：写测试")
    cb = ContextBuilder(workspace)
    ctx = cb.build()
    assert "今日任务" in ctx.system_prompt


def test_build_includes_yesterday_log(tmp_path):
    """存在昨日日志时包含在 system_prompt 中"""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    memory_dir = workspace / "memory"
    memory_dir.mkdir()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    (memory_dir / f"{yesterday}.md").write_text("昨日完成：写代码")
    cb = ContextBuilder(workspace)
    ctx = cb.build()
    assert "昨日完成" in ctx.system_prompt


# ── 技能摘要 ──────────────────────────────────────────────────────────────────

def test_build_with_skill_loader(tmp_path):
    """提供 skill_loader 时包含技能摘要"""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "memory").mkdir()

    class FakeLoader:
        def get_summary_list(self):
            return "- web_search: 搜索网络"

    cb = ContextBuilder(workspace, skill_loader=FakeLoader())
    ctx = cb.build()
    assert "web_search" in ctx.system_prompt
