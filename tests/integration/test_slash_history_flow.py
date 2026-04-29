"""`/history` slash 命令端到端集成测试。

验证三轮对话后输入 /history，Service 返回的 SLASH_RESULT payload：
- command == "history"
- session_thread 非空
- turns 长度 = 3，且每条含 user_input / plan_goal / output / turn_index
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, patch

import websockets
import yaml

from sunday.service.protocol import EventType, Message


def _prepare_configs(tmp_path):
    configs_dir = tmp_path / "configs"
    prompts_dir = configs_dir / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    (configs_dir / "agent.yaml").write_text(yaml.dump({
        "model": {"provider": "anthropic", "id": "claude-test", "max_tokens": 4096},
        "agent": {
            "workspace_dir": str(tmp_path / "workspace"),
            "sessions_dir": str(tmp_path / "sessions"),
        },
    }))
    real_prompt = Path(__file__).parent.parent.parent / "configs" / "prompts" / "thread_update.md"
    shutil.copy(real_prompt, prompts_dir / "thread_update.md")
    return configs_dir


def _make_settings(tmp_path):
    from sunday.config import Settings

    from tests.conftest import seed_workspace
    configs_dir = _prepare_configs(tmp_path)
    seed_workspace(tmp_path / "workspace")
    with patch.dict(os.environ, {
        "ANTHROPIC_API_KEY": "sk-ant-fake",
        "SUNDAY_CONFIGS_DIR": str(configs_dir),
    }, clear=False):
        s = Settings()
        _ = s.sunday
        return s


async def _start_service(tmp_path, mock_loop_run):
    from sunday.service.server import SundayService
    settings = _make_settings(tmp_path)
    gw = SundayService(settings)
    gw._mock_loop_run = mock_loop_run
    port = await gw.start_test()
    return gw, port


async def _send(ws, msg: Message):
    await ws.send(msg.to_json())


async def _recv_until(ws, target_type: str, timeout: float = 5.0) -> dict:
    async def _loop():
        while True:
            raw = await ws.recv()
            msg = json.loads(raw)
            if msg.get("type") == target_type:
                return msg
    return await asyncio.wait_for(_loop(), timeout=timeout)


async def _wait_task_finished(gw, session_id: str):
    task = gw._running_tasks.get(session_id)
    if task is None:
        return
    try:
        await asyncio.wait_for(task, timeout=5.0)
    except Exception:
        pass


async def test_slash_history_returns_turns_and_thread(tmp_path):
    answers = [
        "DeepSeek V4 发布的回答",
        "AI 算力基础设施的回答",
        "产业链报告的回答",
    ]
    call_iter = iter(answers)

    async def fake_run(state):
        return next(call_iter)

    thread_responses = [
        json.dumps({"summary": "围绕 DeepSeek V4", "key_entities": ["DeepSeek V4"]}),
        json.dumps({"summary": "DeepSeek V4 + 算力", "key_entities": ["DeepSeek V4", "AI 算力"]}),
        json.dumps({
            "summary": "DeepSeek V4 + 算力 + 产业链",
            "key_entities": ["DeepSeek V4", "AI 算力", "产业链"],
        }),
    ]
    call_text = AsyncMock(side_effect=thread_responses)

    questions = [
        "介绍 DeepSeek V4",
        "AI 算力基础设施",
        "产业链上下游报告",
    ]

    with patch("sunday.agent.llm_client.LLMClient.call_text", new=call_text):
        gw, port = await _start_service(tmp_path, fake_run)
        try:
            async with websockets.connect(f"ws://localhost:{port}") as ws:
                sid = "histflow0001"
                for q in questions:
                    await _send(ws, Message(type=EventType.SEND, session_id=sid,
                                            data={"content": q}))
                    await _recv_until(ws, "done", timeout=5.0)
                    await _wait_task_finished(gw, sid)

                # 发送 /history
                await _send(ws, Message(type=EventType.SLASH, session_id=sid,
                                        data={"command": "history", "args": ""}))
                result = await _recv_until(ws, "slash_result", timeout=3.0)
        finally:
            await gw.stop()

    payload = result["data"]
    assert payload["command"] == "history"
    assert payload["session_id"] == "histflow0001"

    thread = payload.get("session_thread")
    assert thread is not None
    assert "DeepSeek V4" in thread["key_entities"]
    assert "产业链" in thread["key_entities"]

    turns = payload.get("turns") or []
    assert len(turns) == 3
    # 每条 turn 含核心字段
    for i, t in enumerate(turns):
        assert t["turn_index"] >= 1
        assert t["user_input"] == questions[i]
        # output 含 AgentLoop 追加的 report footer，仅检查前缀
        assert t["output"].startswith(answers[i])
        assert t["outcome"] == "success"

    # message 字段人类可读
    assert "3" in payload["message"]
