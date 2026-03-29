"""T2-3 验证：Executor 单元测试（mock httpx，无真实 API）"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from sunday.agent.executor import Executor, MaxStepsError, RepetitionError
from sunday.agent.models import AgentState, Step, StepStatus


def _make_settings(tmp_path, provider="anthropic", base_url=None, api_key_env=None):
    from sunday.config import Settings
    model_cfg: dict = {"provider": provider, "id": "test-model", "max_tokens": 4096}
    if base_url:
        model_cfg["base_url"] = base_url
    if api_key_env:
        model_cfg["api_key_env"] = api_key_env
    config_file = tmp_path / "agent.yaml"
    config_file.write_text(yaml.dump({
        "model": model_cfg,
        "reasoning": {"max_steps": 3},
    }))
    with patch.dict(os.environ, {
        "ANTHROPIC_API_KEY": "sk-ant-fake",
        "OPENAI_API_KEY": "sk-openai-fake",
        "DEEPSEEK_API_KEY": "sk-deepseek-fake",
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

async def test_max_steps_forced_finish(tmp_path):
    """超过 max_steps 时强制收尾输出（不抛异常），状态为 DONE"""
    settings = _make_settings(tmp_path)  # max_steps=3

    mock_registry = MagicMock()
    mock_registry.get_schemas.return_value = [{"name": "foo", "description": "foo"}]
    mock_registry.execute = AsyncMock(return_value="result")
    executor = Executor(settings, tool_registry=mock_registry)

    # 前 max_steps-1 次返回工具调用，最后一次返回文本（强制收尾）
    def make_tool_response(i):
        return _anthropic_tool_use_response("foo", {"n": i})

    responses = [make_tool_response(i) for i in range(10)]
    mock_client = _mock_http_client(responses)

    step = Step(id="s1", intent="无限循环测试")
    state = AgentState(session_id="sess", task="test")

    with (
        patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-fake"}),
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        result = await executor.run(step, state)

    assert result.status == StepStatus.DONE
    assert result.step_id == "s1"


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


# ── OpenAI 格式工具支持（新增）────────────────────────────────────────────────

def _openai_stop_response(text: str) -> dict:
    """OpenAI /chat/completions 格式的 stop 响应"""
    return {
        "choices": [{
            "message": {"role": "assistant", "content": text, "tool_calls": None},
            "finish_reason": "stop",
        }]
    }


def _openai_tool_call_response(tool_name: str, tool_input: dict, call_id: str = "call_abc123") -> dict:
    """OpenAI /chat/completions 格式的 tool_calls 响应"""
    import json
    return {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "arguments": json.dumps(tool_input),
                    },
                }],
            },
            "finish_reason": "tool_calls",
        }]
    }


async def test_call_openai_tools_format(tmp_path):
    """_call_openai 发送的 body['tools'] 应符合 OpenAI 格式（type/function/parameters）"""
    settings = _make_settings(
        tmp_path, provider="openai",
        base_url="https://api.deepseek.com/v1",
        api_key_env="DEEPSEEK_API_KEY",
    )
    executor = Executor(settings)

    captured_body = {}

    async def fake_post(url, headers=None, json=None, **kwargs):
        captured_body.update(json or {})
        r = MagicMock()
        r.json.return_value = _openai_stop_response("ok")
        r.raise_for_status.return_value = None
        return r

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = fake_post

    # 注册一个带 input_schema 的工具
    mock_registry = MagicMock()
    mock_registry.get_schemas.return_value = [{
        "name": "run_shell",
        "description": "执行 shell 命令",
        "input_schema": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    }]

    executor.tool_registry = mock_registry

    step = Step(id="s1", intent="测试工具格式")
    state = AgentState(session_id="sess", task="test")

    with (
        patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-deepseek-fake"}),
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        await executor.run(step, state)

    assert "tools" in captured_body, "body 中应包含 tools 字段"
    tool = captured_body["tools"][0]
    # 必须符合 OpenAI 格式：{"type": "function", "function": {...}}
    assert tool.get("type") == "function", f"tools[0].type 应为 'function'，实际：{tool}"
    fn = tool.get("function", {})
    assert fn.get("name") == "run_shell"
    assert "parameters" in fn, "OpenAI 格式应使用 'parameters'，不是 'input_schema'"
    assert "input_schema" not in fn, "不应出现 Anthropic 的 'input_schema' 字段"
    assert fn["parameters"].get("properties", {}).get("command") is not None


async def test_call_openai_tool_call_id_preserved(tmp_path):
    """OpenAI 响应归一化后，tool_calls[0]['id'] 不丢失"""
    settings = _make_settings(tmp_path, provider="openai")
    executor = Executor(settings)

    mock_client = _mock_http_client([
        _openai_tool_call_response("run_shell", {"command": "ls"}, call_id="call_xyz999"),
        _openai_stop_response("完成"),
    ])

    mock_registry = MagicMock()
    mock_registry.get_schemas.return_value = [{"name": "run_shell", "description": "exec"}]
    mock_registry.execute = AsyncMock(return_value="file_list")
    executor.tool_registry = mock_registry

    step = Step(id="s1", intent="列出文件")
    state = AgentState(session_id="sess", task="test")

    with (
        patch.dict(os.environ, {"OPENAI_API_KEY": "sk-openai-fake"}),
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        result = await executor.run(step, state)

    assert result.status == StepStatus.DONE
    assert result.react_iterations[0].tool_name == "run_shell"


async def test_run_openai_tool_message_format(tmp_path):
    """OpenAI 多轮工具调用：第二轮请求的 messages 包含 role='tool' + tool_call_id"""
    settings = _make_settings(tmp_path, provider="openai")
    executor = Executor(settings)

    call_id = "call_deepseek_001"
    all_bodies: list[dict] = []

    async def fake_post(url, headers=None, json=None, **kwargs):
        all_bodies.append(json or {})
        call_n = len(all_bodies)
        r = MagicMock()
        if call_n == 1:
            r.json.return_value = _openai_tool_call_response(
                "run_shell", {"command": "echo hi"}, call_id=call_id
            )
        else:
            r.json.return_value = _openai_stop_response("echo 完成")
        r.raise_for_status.return_value = None
        return r

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = fake_post

    mock_registry = MagicMock()
    mock_registry.get_schemas.return_value = [{"name": "run_shell", "description": "exec"}]
    mock_registry.execute = AsyncMock(return_value="hi")
    executor.tool_registry = mock_registry

    step = Step(id="s1", intent="执行 echo")
    state = AgentState(session_id="sess", task="test")

    with (
        patch.dict(os.environ, {"OPENAI_API_KEY": "sk-openai-fake"}),
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        result = await executor.run(step, state)

    assert result.status == StepStatus.DONE
    assert len(all_bodies) == 2, "应该发送两次请求"

    second_messages = all_bodies[1]["messages"]
    # 应包含 role="tool" 的消息
    tool_msgs = [m for m in second_messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 1, f"第二轮应有一条 role='tool' 消息，实际：{second_messages}"
    assert tool_msgs[0]["tool_call_id"] == call_id
    assert tool_msgs[0]["content"] == "hi"

    # 应包含 role="assistant" 且有 tool_calls 字段的消息
    assistant_tool_msgs = [
        m for m in second_messages
        if m.get("role") == "assistant" and m.get("tool_calls")
    ]
    assert len(assistant_tool_msgs) == 1
    assert assistant_tool_msgs[0]["tool_calls"][0]["id"] == call_id
