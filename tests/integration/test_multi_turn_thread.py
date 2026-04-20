"""多轮会话 session_thread 端到端集成测试。

验证三轮 send 消息后：
1. 每轮结束写入 session_thread 到 meta.json
2. 后续轮 Planner 收到的 state 带有前序轮累积的 session_thread
3. 第 3 轮的 thread 含前两轮关键实体
"""
from __future__ import annotations

import asyncio
import json
import os
from unittest.mock import AsyncMock, patch

import websockets
import yaml

from sunday.gateway.protocol import EventType, Message


def _prepare_configs(tmp_path):
    """在 tmp_path 下构造完整 configs/（含 agent.yaml 与 prompts/thread_update.md）。

    update_session_thread 调用 `config.load_prompt("thread_update")` 会走 SUNDAY_CONFIGS_DIR，
    所以必须与 agent.yaml 放同一目录。
    """
    from pathlib import Path
    import shutil

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

    # 复制真实的 thread_update.md，保证 update_session_thread 的 prompt 可用
    real_prompt = Path(__file__).parent.parent.parent / "configs" / "prompts" / "thread_update.md"
    shutil.copy(real_prompt, prompts_dir / "thread_update.md")
    return configs_dir


def _make_settings(tmp_path):
    from sunday.config import Settings
    configs_dir = _prepare_configs(tmp_path)
    with patch.dict(os.environ, {
        "ANTHROPIC_API_KEY": "sk-ant-fake",
        "SUNDAY_CONFIGS_DIR": str(configs_dir),
    }, clear=False):
        s = Settings()
        _ = s.sunday  # 强制触发 cached_property，固化配置加载路径
        return s


async def _start_gateway(tmp_path, mock_loop_run):
    from sunday.gateway.server import Gateway
    settings = _make_settings(tmp_path)
    gw = Gateway(settings)
    gw._mock_loop_run = mock_loop_run
    port = await gw.start_test()
    return gw, port


async def _send(ws, msg: Message):
    await ws.send(msg.to_json())


async def _recv_until(ws, target_type: str, timeout: float = 5.0) -> dict:
    """一直读消息直到 type 匹配 target_type，返回该消息。"""
    async def _loop():
        while True:
            raw = await ws.recv()
            msg = json.loads(raw)
            if msg.get("type") == target_type:
                return msg
    return await asyncio.wait_for(_loop(), timeout=timeout)


async def _wait_task_finished(gw, session_id: str):
    """等待 Gateway 内 running_task 彻底结束（含 finally 的 write_turn + update_thread）。"""
    task = gw._running_tasks.get(session_id)
    if task is None:
        return
    try:
        await asyncio.wait_for(task, timeout=5.0)
    except asyncio.CancelledError:
        pass
    except Exception:
        pass


async def test_three_turns_build_up_session_thread(tmp_path):
    """三轮对话：thread 应逐轮合并，state.session_thread 在第 2/3 轮可见。"""
    # mock Planner 调用入口的 state 捕获
    captured_states: list[dict] = []

    async def fake_loop_run(state):
        captured_states.append({
            "turn_id": state.turn_id,
            "task": state.task,
            "history_len": len(state.history),
            "session_thread": (
                state.session_thread.model_dump() if state.session_thread else None
            ),
        })
        # 模拟 plan 事件落盘（让 _extract_conversation 的 plan_goal 路径有数据）
        return f"针对「{state.task}」的回答"

    # 每轮 LLM 返回不同的 thread JSON
    thread_responses = [
        json.dumps({
            "summary": "围绕 DeepSeek V4 发布展开",
            "key_entities": ["DeepSeek V4"],
        }),
        json.dumps({
            "summary": "围绕 DeepSeek V4 发布，扩展到 AI 算力基础设施",
            "key_entities": ["DeepSeek V4", "AI 算力基础设施"],
        }),
        json.dumps({
            "summary": "围绕 DeepSeek V4 发布，覆盖算力基础设施与产业链",
            "key_entities": ["DeepSeek V4", "AI 算力基础设施", "产业链"],
        }),
    ]
    call_text = AsyncMock(side_effect=thread_responses)

    with patch("sunday.agent.llm_client.LLMClient.call_text", new=call_text):
        gw, port = await _start_gateway(tmp_path, fake_loop_run)
        try:
            async with websockets.connect(f"ws://localhost:{port}") as ws:
                sid = "multiturn01"
                questions = [
                    "介绍 DeepSeek V4 发布",
                    "谈谈 AI 算力基础设施",
                    "从产业链上下游角度给出报告",
                ]
                for q in questions:
                    await _send(ws, Message(type=EventType.SEND, session_id=sid,
                                            data={"content": q}))
                    await _recv_until(ws, "done", timeout=5.0)
                    await _wait_task_finished(gw, sid)
        finally:
            await gw.stop()

    # 三轮都进入了 fake_loop_run
    assert len(captured_states) == 3

    # 第 1 轮：无 thread（首轮）
    assert captured_states[0]["session_thread"] is None

    # 第 2 轮：看到第 1 轮保存的 thread
    t2_thread = captured_states[1]["session_thread"]
    assert t2_thread is not None
    assert "DeepSeek V4" in t2_thread["key_entities"]

    # 第 3 轮：看到增量合并后的 thread（含前两轮实体）
    t3_thread = captured_states[2]["session_thread"]
    assert t3_thread is not None
    assert "DeepSeek V4" in t3_thread["key_entities"]
    assert "AI 算力基础设施" in t3_thread["key_entities"]

    # 第 2/3 轮 state 应含历史对话
    assert captured_states[1]["history_len"] >= 2  # turn 1 的 user+assistant
    assert captured_states[2]["history_len"] >= 4  # turn 1+2 的 user+assistant

    # meta.json 最终 thread 含三轮的累积实体
    meta_path = tmp_path / "sessions" / "multiturn01" / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    final_thread = meta.get("session_thread")
    assert final_thread is not None
    assert set(final_thread["key_entities"]) >= {
        "DeepSeek V4", "AI 算力基础设施", "产业链",
    }

    # thread_update LLM 被调用 3 次
    assert call_text.call_count == 3


async def test_aborted_turn_does_not_pollute_thread(tmp_path):
    """aborted outcome 不应触发 update_session_thread。"""

    async def aborting_run(state):
        # 模拟被 abort 时的行为
        raise asyncio.CancelledError()

    call_text = AsyncMock(return_value=json.dumps({"summary": "x", "key_entities": []}))

    with patch("sunday.agent.llm_client.LLMClient.call_text", new=call_text):
        gw, port = await _start_gateway(tmp_path, aborting_run)
        try:
            async with websockets.connect(f"ws://localhost:{port}") as ws:
                sid = "abortsession1"
                await _send(ws, Message(type=EventType.SEND, session_id=sid,
                                        data={"content": "测试"}))
                await _wait_task_finished(gw, sid)
        finally:
            await gw.stop()

    # update_session_thread 应跳过（outcome=aborted）
    call_text.assert_not_called()

    # meta.json 不应有 session_thread
    meta_path = tmp_path / "sessions" / "abortsession1" / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert "session_thread" not in meta
