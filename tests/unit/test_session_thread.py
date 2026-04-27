"""update_session_thread 单元测试 — 通过 MemoryClient 读写 thread。"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import yaml

from sunday.memory.local import LocalMemoryClient
from sunday.memory.session_thread import update_session_thread


def _make_config(tmp_path):
    from sunday.config import Settings
    config_file = tmp_path / "agent.yaml"
    config_file.write_text(yaml.dump({
        "model": {"provider": "anthropic", "id": "claude-test"},
    }))
    with patch.dict(os.environ, {
        "ANTHROPIC_API_KEY": "sk-ant-fake",
        "SUNDAY_CONFIGS_DIR": str(Path(__file__).parent.parent.parent / "configs"),
    }):
        s = Settings(_env_file=str(config_file))
        return s.sunday


@pytest.fixture
async def client(tmp_path):
    c = LocalMemoryClient(
        sessions_dir=tmp_path / "sessions",
        memory_dir=tmp_path / "memory",
        log_dir=tmp_path / "logs",
        workspace_dir=tmp_path / "workspace",
        run_janitor=False,
    )
    yield c
    await c.aclose()


async def test_update_skipped_when_outcome_not_success(client, tmp_path):
    cfg = _make_config(tmp_path)
    meta = await client.sessions.create()
    sid = meta.session_id
    turn = {"turn_id": "t1", "outcome": "error", "user_input": "x", "output": "y"}

    with patch("sunday.agent.llm_client.LLMClient.call_text",
               new=AsyncMock(return_value="{}")) as mock_call:
        await update_session_thread(sid, turn, client, cfg)

    mock_call.assert_not_called()
    assert await client.sessions.get_thread(sid) is None


async def test_update_first_turn_creates_thread(client, tmp_path):
    cfg = _make_config(tmp_path)
    meta = await client.sessions.create()
    sid = meta.session_id
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
        await update_session_thread(sid, turn, client, cfg)

    thread = await client.sessions.get_thread(sid)
    assert thread is not None
    assert thread.summary == "围绕 DeepSeek V4 发布展开"
    assert thread.key_entities == ["DeepSeek V4"]
    assert thread.updated_at_turn == "t1"


async def test_update_incremental_merge_dedupes_entities(client, tmp_path):
    from sunday.agent.models import SessionThread
    cfg = _make_config(tmp_path)
    meta = await client.sessions.create()
    sid = meta.session_id
    await client.sessions.save_thread(sid, SessionThread(
        summary="围绕 DeepSeek V4 展开",
        key_entities=["DeepSeek V4"],
        updated_at_turn="t1",
    ))

    turn = {
        "turn_id": "t2",
        "outcome": "success",
        "user_input": "AI 算力基础设施",
        "plan": {"goal": "讨论 DeepSeek V4 对算力基础设施的影响"},
        "output": "算力层 ...",
    }
    fake_resp = json.dumps({
        "summary": "围绕 DeepSeek V4 展开，扩展到 AI 算力基础设施",
        "key_entities": ["DeepSeek V4", "AI 算力基础设施", "DeepSeek V4"],
    })
    with patch("sunday.agent.llm_client.LLMClient.call_text",
               new=AsyncMock(return_value=fake_resp)):
        await update_session_thread(sid, turn, client, cfg)

    thread = await client.sessions.get_thread(sid)
    assert thread.key_entities == ["DeepSeek V4", "AI 算力基础设施"]
    assert "算力" in thread.summary
    assert thread.updated_at_turn == "t2"


async def test_update_llm_failure_is_silent(client, tmp_path):
    cfg = _make_config(tmp_path)
    meta = await client.sessions.create()
    sid = meta.session_id
    turn = {
        "turn_id": "t1", "outcome": "success",
        "user_input": "q", "plan": {"goal": "g"}, "output": "a",
    }
    with patch("sunday.agent.llm_client.LLMClient.call_text",
               new=AsyncMock(side_effect=RuntimeError("network"))):
        await update_session_thread(sid, turn, client, cfg)

    assert await client.sessions.get_thread(sid) is None


async def test_update_parses_fenced_json(client, tmp_path):
    cfg = _make_config(tmp_path)
    meta = await client.sessions.create()
    sid = meta.session_id
    turn = {
        "turn_id": "t1", "outcome": "success",
        "user_input": "x", "plan": {"goal": "g"}, "output": "o",
    }
    fake_resp = "```json\n" + json.dumps({
        "summary": "s", "key_entities": ["E1"]
    }) + "\n```"
    with patch("sunday.agent.llm_client.LLMClient.call_text",
               new=AsyncMock(return_value=fake_resp)):
        await update_session_thread(sid, turn, client, cfg)

    thread = await client.sessions.get_thread(sid)
    assert thread.summary == "s"
    assert thread.key_entities == ["E1"]


async def test_update_invalid_json_keeps_meta_unchanged(client, tmp_path):
    cfg = _make_config(tmp_path)
    meta = await client.sessions.create()
    sid = meta.session_id
    turn = {
        "turn_id": "t1", "outcome": "success",
        "user_input": "x", "plan": {"goal": "g"}, "output": "o",
    }
    with patch("sunday.agent.llm_client.LLMClient.call_text",
               new=AsyncMock(return_value="这不是 JSON，也没有花括号")):
        await update_session_thread(sid, turn, client, cfg)

    assert await client.sessions.get_thread(sid) is None
