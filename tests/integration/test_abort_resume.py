"""S2-G 验证：abort → resume 端到端。

场景：
1. 用户发起任务 A
2. 任务执行到一半被 abort（CancelledError 写入 stream）
3. 用户在同一 session 发送"请继续"
4. continuation 解析旧 stream，重写 task 为 "<原任务> + 说明"
5. AgentLoop.run 被调用，state.task 包含原任务

注：同一 session 串行执行（service 端 lock），下一轮 SEND 必须等上一轮
关闭对应 task 才能开跑；测试中通过 ABORT + 等待 task 完成实现。
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import websockets
import yaml

from sunday.service.protocol import EventType, Message


def _prepare_configs(tmp_path: Path) -> Path:
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
    real_prompt = (
        Path(__file__).parent.parent.parent / "configs" / "prompts" / "thread_update.md"
    )
    shutil.copy(real_prompt, prompts_dir / "thread_update.md")
    return configs_dir


def _make_settings(tmp_path: Path):
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


async def _start_service(tmp_path: Path, mock_loop_run):
    from sunday.service.server import SundayService
    settings = _make_settings(tmp_path)
    svc = SundayService(settings)
    svc._mock_loop_run = mock_loop_run
    port = await svc.start_test()
    return svc, port


async def _recv_until(ws, target_type: str, timeout: float = 3.0) -> dict:
    async def _loop():
        while True:
            raw = await ws.recv()
            msg = json.loads(raw)
            if msg.get("type") == target_type:
                return msg
    return await asyncio.wait_for(_loop(), timeout=timeout)


async def _wait_task_finished(svc, session_id: str):
    task = svc._running_tasks.get(session_id)
    if task is None:
        return
    try:
        await asyncio.wait_for(task, timeout=5.0)
    except (Exception, asyncio.CancelledError):
        # 任务被 abort 时会以 CancelledError 退出（CancelledError 不继承自 Exception）
        pass


@pytest.mark.asyncio
async def test_abort_then_resume_continues_original_task(tmp_path: Path):
    """完整 abort → resume 链路：原任务被识别并衔接。"""
    captured: dict = {"calls": []}

    async def slow_run(state):
        """模拟长任务：第一次卡 5s 直到被 cancel；第二次直接返回。"""
        captured["calls"].append({
            "task": state.task,
            "session_thread": state.session_thread,
        })
        if len(captured["calls"]) == 1:
            # 第一次：长 sleep 模拟运行中，等 abort 取消
            await asyncio.sleep(5.0)
            return "不应到达"
        # 第二次（resume）：立即返回
        return "（已继续完成）"

    # mock thread_update LLM（避免真实调用）
    thread_mock = AsyncMock(return_value="")
    sid = "abortresume01"
    original_task = "调研一下深圳新能源车销量趋势"

    with patch("sunday.agent.llm_client.LLMClient.call_text", new=thread_mock):
        svc, port = await _start_service(tmp_path, slow_run)
        try:
            # ── Round 1：发任务 → abort
            async with websockets.connect(f"ws://localhost:{port}") as ws1:
                await ws1.send(Message(
                    type=EventType.SEND,
                    session_id=sid,
                    data={"content": original_task},
                ).to_json())
                # 等任务实际开始（任何 status / status:* 信号都行）
                await asyncio.sleep(0.2)
                # 发 abort
                await ws1.send(Message(
                    type=EventType.ABORT, session_id=sid,
                ).to_json())
                # 等服务端处理完 abort（task.cancel 后 finally 写 turn）
                await _wait_task_finished(svc, sid)

            # 验证：第一轮 run 被调用了，task=原任务
            assert len(captured["calls"]) == 1
            assert captured["calls"][0]["task"] == original_task

            # ── Round 2：发 "请继续"，应触发 continuation 重写
            async with websockets.connect(f"ws://localhost:{port}") as ws2:
                await ws2.send(Message(
                    type=EventType.SEND,
                    session_id=sid,
                    data={"content": "请继续"},
                ).to_json())
                await _recv_until(ws2, "done", timeout=5.0)
                await _wait_task_finished(svc, sid)
        finally:
            await svc.stop()

    # 验证：第二轮 run 被调用，task 被改写为含原任务的"继续"语义
    assert len(captured["calls"]) == 2
    resumed_task = captured["calls"][1]["task"]
    assert original_task in resumed_task, (
        f"resume 后的 task 应包含原任务，实际：{resumed_task!r}"
    )
    assert "继续" in resumed_task or "未完成" in resumed_task, (
        f"resume task 应有连续语义提示：{resumed_task!r}"
    )
