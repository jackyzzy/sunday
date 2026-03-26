"""T3-3 验证：MemoryJanitor 单元测试（真实文件系统）"""
from __future__ import annotations

from datetime import date, timedelta

from sunday.memory.janitor import MemoryJanitor


def _make_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    memory_dir = workspace / "memory"
    memory_dir.mkdir()
    return workspace, memory_dir


def test_run_deletes_expired_logs(tmp_path):
    """超过保留期的日志文件被删除"""
    workspace, memory_dir = _make_workspace(tmp_path)
    old_date = (date.today() - timedelta(days=40)).isoformat()
    old_file = memory_dir / f"{old_date}.md"
    old_file.write_text("旧日志")

    janitor = MemoryJanitor(workspace, retention_days=30)
    stats = janitor.run()

    assert not old_file.exists()
    assert stats["deleted"] == 1
    assert stats["kept"] == 0


def test_run_keeps_recent_logs(tmp_path):
    """保留期内的日志文件不被删除"""
    workspace, memory_dir = _make_workspace(tmp_path)
    recent_date = (date.today() - timedelta(days=5)).isoformat()
    recent_file = memory_dir / f"{recent_date}.md"
    recent_file.write_text("近期日志")

    janitor = MemoryJanitor(workspace, retention_days=30)
    stats = janitor.run()

    assert recent_file.exists()
    assert stats["kept"] == 1
    assert stats["deleted"] == 0


def test_run_mixed_files(tmp_path):
    """同时存在新旧文件时，分别保留/删除"""
    workspace, memory_dir = _make_workspace(tmp_path)
    old = memory_dir / f"{(date.today() - timedelta(days=60)).isoformat()}.md"
    recent = memory_dir / f"{(date.today() - timedelta(days=1)).isoformat()}.md"
    old.write_text("旧")
    recent.write_text("新")

    janitor = MemoryJanitor(workspace, retention_days=30)
    stats = janitor.run()

    assert not old.exists()
    assert recent.exists()
    assert stats["deleted"] == 1
    assert stats["kept"] == 1


def test_run_ignores_non_date_files(tmp_path):
    """非日期格式文件不被处理"""
    workspace, memory_dir = _make_workspace(tmp_path)
    non_date_file = memory_dir / "notes.md"
    non_date_file.write_text("笔记")

    janitor = MemoryJanitor(workspace, retention_days=30)
    stats = janitor.run()

    assert non_date_file.exists()
    assert stats["deleted"] == 0
    assert stats["kept"] == 0


def test_run_missing_memory_dir(tmp_path):
    """memory/ 目录不存在时返回 {deleted: 0, kept: 0}"""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    janitor = MemoryJanitor(workspace)
    stats = janitor.run()
    assert stats == {"deleted": 0, "kept": 0}
