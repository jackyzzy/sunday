"""T3-1 验证：MemoryManager 单元测试（真实文件系统，mock httpx）"""
from __future__ import annotations

import os
from datetime import date
from unittest.mock import patch

import yaml

from sunday.memory.manager import MemoryManager


def _make_settings(tmp_path, provider="anthropic"):
    from sunday.config import Settings
    config_file = tmp_path / "agent.yaml"
    config_file.write_text(yaml.dump({
        "model": {"provider": provider, "id": "claude-test", "max_tokens": 4096},
    }))
    with patch.dict(os.environ, {
        "ANTHROPIC_API_KEY": "sk-ant-fake",
        "SUNDAY_CONFIG_FILE": str(config_file),
    }):
        return Settings()


# ── 目录初始化 ────────────────────────────────────────────────────────────────

async def test_init_creates_directories(tmp_path):
    """MemoryManager 初始化时自动创建目录"""
    workspace = tmp_path / "workspace"
    MemoryManager(workspace)
    assert workspace.exists()
    assert (workspace / "memory").exists()


# ── append_daily_log ──────────────────────────────────────────────────────────

async def test_append_daily_log_creates_file(tmp_path):
    """append_daily_log 创建今日日志文件"""
    mm = MemoryManager(tmp_path)
    await mm.append_daily_log("第一条日志\n")
    today = date.today().isoformat()
    log_path = tmp_path / "memory" / f"{today}.md"
    assert log_path.exists()
    assert "第一条日志" in log_path.read_text(encoding="utf-8")


async def test_append_daily_log_appends(tmp_path):
    """多次调用 append_daily_log 不覆盖之前内容"""
    mm = MemoryManager(tmp_path)
    await mm.append_daily_log("第一条\n")
    await mm.append_daily_log("第二条\n")
    today = date.today().isoformat()
    content = (tmp_path / "memory" / f"{today}.md").read_text(encoding="utf-8")
    assert "第一条" in content
    assert "第二条" in content


async def test_append_daily_log_adds_newline_if_missing(tmp_path):
    """没有末尾换行时自动补充"""
    mm = MemoryManager(tmp_path)
    await mm.append_daily_log("没有换行")
    today = date.today().isoformat()
    content = (tmp_path / "memory" / f"{today}.md").read_text(encoding="utf-8")
    assert content.endswith("\n")


# ── update_memory ─────────────────────────────────────────────────────────────

async def test_update_memory_creates_section(tmp_path):
    """update_memory 在 MEMORY.md 中创建新 section"""
    mm = MemoryManager(tmp_path)
    await mm.update_memory("用户偏好", "语言", "中文")
    content = (tmp_path / "MEMORY.md").read_text(encoding="utf-8")
    assert "## 用户偏好" in content
    assert "语言：中文" in content


async def test_update_memory_upsert_existing_key(tmp_path):
    """update_memory 对已有 key 执行 upsert（不重复插入）"""
    mm = MemoryManager(tmp_path)
    await mm.update_memory("用户偏好", "语言", "中文")
    await mm.update_memory("用户偏好", "语言", "英文")
    content = (tmp_path / "MEMORY.md").read_text(encoding="utf-8")
    # 只有一行含 "语言："
    assert content.count("语言：") == 1
    assert "英文" in content


async def test_update_memory_multiple_sections(tmp_path):
    """update_memory 支持多个不同 section"""
    mm = MemoryManager(tmp_path)
    await mm.update_memory("偏好", "颜色", "蓝色")
    await mm.update_memory("任务历史", "最近任务", "写代码")
    content = (tmp_path / "MEMORY.md").read_text(encoding="utf-8")
    assert "## 偏好" in content
    assert "## 任务历史" in content


# ── update_user_profile ───────────────────────────────────────────────────────

async def test_update_user_profile_creates_entry(tmp_path):
    """update_user_profile 在 USER.md 中写入条目"""
    mm = MemoryManager(tmp_path)
    await mm.update_user_profile("姓名", "张三")
    content = (tmp_path / "USER.md").read_text(encoding="utf-8")
    assert "姓名：张三" in content


async def test_update_user_profile_upsert(tmp_path):
    """update_user_profile 对已有 key 执行 upsert"""
    mm = MemoryManager(tmp_path)
    await mm.update_user_profile("姓名", "张三")
    await mm.update_user_profile("姓名", "李四")
    content = (tmp_path / "USER.md").read_text(encoding="utf-8")
    assert content.count("姓名：") == 1
    assert "李四" in content


# ── consolidate_session ───────────────────────────────────────────────────────

async def test_consolidate_session_writes_log(tmp_path):
    """consolidate_session 同步写入今日日志"""
    from sunday.agent.models import AgentState, StepResult, StepStatus
    mm = MemoryManager(tmp_path)
    state = AgentState(session_id="test123", task="写单元测试")
    state.step_results = [
        StepResult(step_id="step_1", status=StepStatus.DONE, output="完成了"),
    ]
    await mm.consolidate_session(state)
    today = date.today().isoformat()
    log_path = tmp_path / "memory" / f"{today}.md"
    content = log_path.read_text(encoding="utf-8")
    assert "写单元测试" in content
    assert "test123" in content


async def test_consolidate_session_no_settings_skips_ai(tmp_path):
    """consolidate_session 在无 settings 时跳过 AI 整合（不报错）"""
    from sunday.agent.models import AgentState
    mm = MemoryManager(tmp_path, settings=None)
    state = AgentState(session_id="s", task="任务")
    await mm.consolidate_session(state)  # 不应抛出异常
