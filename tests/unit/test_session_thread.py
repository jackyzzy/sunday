"""update_session_thread 单元测试 —— 轻量 LLM 合并主线。"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import yaml

from sunday.gateway.session import SessionManager
from sunday.memory.session_thread import update_session_thread


def _make_config(tmp_path):
    from sunday.config import Settings
    config_file = tmp_path / "agent.yaml"
    config_file.write_text(yaml.dump({
        "model": {"provider": "anthropic", "id": "claude-test"},
    }))
    # thread_update.md 必须可加载：复用工作区里的真 prompt 文件
    with patch.dict(os.environ, {
        "ANTHROPIC_API_KEY": "sk-ant-fake",
        "SUNDAY_CONFIGS_DIR": str(Path(__file__).parent.parent.parent / "configs"),
    }):
        s = Settings(_env_file=str(config_file))
        return s.sunday


@pytest.fixture
def session_mgr(tmp_path):
    return SessionManager(tmp_path / "sessions")


async def test_update_skipped_when_outcome_not_success(session_mgr, tmp_path):
    """outcome != success 时应直接跳过，不触达 LLM，不改 meta.json。"""
    cfg = _make_config(tmp_path)
    sid = session_mgr.new_session()
    turn = {"turn_id": "t1", "outcome": "error", "user_input": "x", "output": "y"}

    with patch("sunday.agent.llm_client.LLMClient.call_text",
               new=AsyncMock(return_value="{}")) as mock_call:
        await update_session_thread(sid, turn, session_mgr, cfg)

    mock_call.assert_not_called()
    assert session_mgr.get_session_thread(sid) is None


async def test_update_first_turn_creates_thread(session_mgr, tmp_path):
    """首轮：无前序 thread，LLM 返回新 summary + entities → 写入 meta.json。"""
    cfg = _make_config(tmp_path)
    sid = session_mgr.new_session()
    turn = {
        "turn_id": "t1",
        "outcome": "success",
        "user_input": "介绍 DeepSeek V4 发布",
        "plan": {"goal": "梳理 DeepSeek V4 的核心模型能力"},
        "output": "DeepSeek V4 是 ...",
    }
    fake_resp = json.dumps({
        "summary": "围绕 DeepSeek V4 发布展开",
        "key_entities": ["DeepSeek V4"],
    })

    with patch("sunday.agent.llm_client.LLMClient.call_text",
               new=AsyncMock(return_value=fake_resp)):
        await update_session_thread(sid, turn, session_mgr, cfg)

    thread = session_mgr.get_session_thread(sid)
    assert thread is not None
    assert thread["summary"] == "围绕 DeepSeek V4 发布展开"
    assert thread["key_entities"] == ["DeepSeek V4"]
    assert thread["updated_at_turn"] == "t1"


async def test_update_incremental_merge_dedupes_entities(session_mgr, tmp_path):
    """已有 thread + 新轮次：实体去重，保留旧条目。"""
    cfg = _make_config(tmp_path)
    sid = session_mgr.new_session()
    # 先埋入已有 thread
    await session_mgr.save_session_thread(sid, {
        "summary": "围绕 DeepSeek V4 展开",
        "key_entities": ["DeepSeek V4"],
        "updated_at_turn": "t1",
    })

    turn = {
        "turn_id": "t2",
        "outcome": "success",
        "user_input": "AI 算力基础设施",
        "plan": {"goal": "讨论 DeepSeek V4 对算力基础设施的影响"},
        "output": "算力层 ...",
    }
    # LLM 返回含重复项，应去重保序
    fake_resp = json.dumps({
        "summary": "围绕 DeepSeek V4 展开，扩展到 AI 算力基础设施",
        "key_entities": ["DeepSeek V4", "AI 算力基础设施", "DeepSeek V4"],
    })
    with patch("sunday.agent.llm_client.LLMClient.call_text",
               new=AsyncMock(return_value=fake_resp)):
        await update_session_thread(sid, turn, session_mgr, cfg)

    thread = session_mgr.get_session_thread(sid)
    assert thread["key_entities"] == ["DeepSeek V4", "AI 算力基础设施"]
    assert "算力" in thread["summary"]
    assert thread["updated_at_turn"] == "t2"


async def test_update_llm_failure_is_silent(session_mgr, tmp_path):
    """LLM 调用抛异常时，用户主路径不受影响，meta.json 保持不变。"""
    cfg = _make_config(tmp_path)
    sid = session_mgr.new_session()
    turn = {
        "turn_id": "t1",
        "outcome": "success",
        "user_input": "q", "plan": {"goal": "g"}, "output": "a",
    }
    with patch("sunday.agent.llm_client.LLMClient.call_text",
               new=AsyncMock(side_effect=RuntimeError("network"))):
        await update_session_thread(sid, turn, session_mgr, cfg)

    assert session_mgr.get_session_thread(sid) is None


async def test_update_parses_fenced_json(session_mgr, tmp_path):
    """LLM 返回 ```json 代码块时也能解析。"""
    cfg = _make_config(tmp_path)
    sid = session_mgr.new_session()
    turn = {
        "turn_id": "t1",
        "outcome": "success",
        "user_input": "x", "plan": {"goal": "g"}, "output": "o",
    }
    fake_resp = "```json\n" + json.dumps({
        "summary": "s", "key_entities": ["E1"]
    }) + "\n```"
    with patch("sunday.agent.llm_client.LLMClient.call_text",
               new=AsyncMock(return_value=fake_resp)):
        await update_session_thread(sid, turn, session_mgr, cfg)

    thread = session_mgr.get_session_thread(sid)
    assert thread["summary"] == "s"
    assert thread["key_entities"] == ["E1"]


async def test_update_invalid_json_keeps_meta_unchanged(session_mgr, tmp_path):
    """LLM 返回非 JSON 文本时，meta 不被污染。"""
    cfg = _make_config(tmp_path)
    sid = session_mgr.new_session()
    turn = {
        "turn_id": "t1",
        "outcome": "success",
        "user_input": "x", "plan": {"goal": "g"}, "output": "o",
    }
    with patch("sunday.agent.llm_client.LLMClient.call_text",
               new=AsyncMock(return_value="这不是 JSON，也没有花括号")):
        await update_session_thread(sid, turn, session_mgr, cfg)

    assert session_mgr.get_session_thread(sid) is None
