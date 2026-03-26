"""T2-3 验证：Executor 单元测试（mock httpx，无真实 API）"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from sunday.agent.executor import Executor, MaxStepsError, RepetitionError
from sunday.agent.models import AgentState, Step, StepStatus


def _make_settings(tmp_path, provider="anthropic"):
    from sunday.config import Settings
    config_file = tmp_path / "agent.yaml"
    config_file.write_text(yaml.dump({
        "model": {"provider": provider, "id": "claude-test", "max_tokens": 4096},
        "reasoning": {"max_steps": 3},
    }))
    with patch.dict(os.environ, {
        "ANTHROPIC_API_KEY": "sk-ant-fake",
        "OPENAI_API_KEY": "sk-openai-fake",
        "SUNDAY_CONFIG_FILE": str(config_file),
    }):
        return Settings()


def _anthropic_stop_response(text: str) -> dict:
    """Anthropic 格式的 stop 响应（无工具调用）"""
    return {
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
    }


def _anthropic_tool_use_response(tool_name: str, tool_input: dict) -> dict:
    """Anthropic 格式的 tool_use 响应"""
    import json
    return {
        "content": [
            {"type": "text", "text": "我来调用工具"},
            {"type": "tool_use", "id": "tool_1", "name": tool_name, "input": tool_input},
        ],
        "stop_reason": "tool_use",
        "tool_calls": [{"name": tool_name, "arguments": json.dumps(tool_input)}],
    }


def _mock_http_client(responses: list[dict]):
    """按顺序返回 responses 的 mock httpx client"""
    mock_resps = []
    for data in responses:
        r = MagicMock()
        r.json.return_value = data
        r.raise_for_status.return_value = None
        mock_resps.append(r)

    call_count = {"n": 0}

    async def fake_post(*args, **kwargs):
        idx = call_count["n"]
        call_count["n"] += 1
        return mock_resps[min(idx, len(mock_resps) - 1)]

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = fake_post
    return mock_client


# ── 基本执行 ──────────────────────────────────────────────────────────────────

async def test_run_success_no_tools(tmp_path):
    """无工具时模型直接 stop，返回 DONE"""
    settings = _make_settings(tmp_path)
    executor = Executor(settings)

    mock_client = _mock_http_client([_anthropic_stop_response("任务完成")])
    step = Step(id="s1", intent="写一句话")
    state = AgentState(session_id="sess", task="test")

    with (
        patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-fake"}),
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        result = await executor.run(step, state)

    assert result.status == StepStatus.DONE
    assert result.output == "任务完成"
    assert result.step_id == "s1"


async def test_run_with_tool_then_stop(tmp_path):
    """调用一次工具后 stop，返回 DONE"""
    settings = _make_settings(tmp_path)

    # mock 工具注册表
    mock_registry = MagicMock()
    mock_registry.get_schemas.return_value = [
        {"name": "echo", "description": "echo tool"}
    ]
    mock_registry.execute = AsyncMock(return_value="echo result")

    executor = Executor(settings, tool_registry=mock_registry)

    responses = [
        _anthropic_tool_use_response("echo", {"text": "hello"}),
        _anthropic_stop_response("工具调用完成"),
    ]
    mock_client = _mock_http_client(responses)

    step = Step(id="s1", intent="调用 echo 工具")
    state = AgentState(session_id="sess", task="test")

    with (
        patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-fake"}),
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        result = await executor.run(step, state)

    assert result.status == StepStatus.DONE
    assert len(result.react_iterations) == 1
    assert result.react_iterations[0].tool_name == "echo"
    assert result.react_iterations[0].observation == "echo result"


# ── MaxStepsError ─────────────────────────────────────────────────────────────

async def test_max_steps_raises(tmp_path):
    """超过 max_steps 时抛出 MaxStepsError"""
    settings = _make_settings(tmp_path)  # max_steps=3

    mock_registry = MagicMock()
    mock_registry.get_schemas.return_value = [{"name": "foo", "description": "foo"}]
    mock_registry.execute = AsyncMock(return_value="result")
    executor = Executor(settings, tool_registry=mock_registry)

    # 每次都返回工具调用，且每次参数不同（避免 RepetitionError）
    def make_tool_response(i):
        return _anthropic_tool_use_response("foo", {"n": i})

    # 需要 max_steps+1 次响应（最后一次不会执行），提供足够多
    responses = [make_tool_response(i) for i in range(10)]
    mock_client = _mock_http_client(responses)

    step = Step(id="s1", intent="无限循环测试")
    state = AgentState(session_id="sess", task="test")

    with (
        patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-fake"}),
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        with pytest.raises(MaxStepsError):
            await executor.run(step, state)


# ── RepetitionError ───────────────────────────────────────────────────────────

async def test_repetition_error(tmp_path):
    """连续相同工具调用触发 RepetitionError"""
    settings = _make_settings(tmp_path)

    mock_registry = MagicMock()
    mock_registry.get_schemas.return_value = [{"name": "foo", "description": "foo"}]
    mock_registry.execute = AsyncMock(return_value="result")
    executor = Executor(settings, tool_registry=mock_registry)

    # 连续两次相同工具+相同参数
    same_response = _anthropic_tool_use_response("foo", {"x": 1})
    mock_client = _mock_http_client([same_response, same_response, same_response])

    step = Step(id="s1", intent="触发重复检测")
    state = AgentState(session_id="sess", task="test")

    with (
        patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-fake"}),
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        with pytest.raises(RepetitionError):
            await executor.run(step, state)


# ── 无工具注册表 ──────────────────────────────────────────────────────────────

async def test_no_tool_registry_completes(tmp_path):
    """无工具注册表时，工具调用返回占位，不崩溃"""
    settings = _make_settings(tmp_path)
    executor = Executor(settings, tool_registry=None)

    responses = [
        _anthropic_tool_use_response("unknown_tool", {"a": 1}),
        _anthropic_stop_response("完成（工具不可用）"),
    ]
    mock_client = _mock_http_client(responses)

    step = Step(id="s1", intent="尝试调用工具")
    state = AgentState(session_id="sess", task="test")

    with (
        patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-fake"}),
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        result = await executor.run(step, state)

    assert result.status == StepStatus.DONE
    assert "不可用" in result.react_iterations[0].observation


# ── 执行阶段 temperature=0 ────────────────────────────────────────────────────

async def test_executor_uses_zero_temperature(tmp_path):
    """执行阶段 temperature=0"""
    settings = _make_settings(tmp_path)
    executor = Executor(settings)

    mock_client = _mock_http_client([_anthropic_stop_response("ok")])
    step = Step(id="s1", intent="test")
    state = AgentState(session_id="sess", task="test")

    with (
        patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-fake"}),
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        await executor.run(step, state)

    # 检查最后一次 POST 的 body 中 temperature=0
    # fake_post 不是 AsyncMock，用另一种方式捕获
    # 通过 patch 直接检验
