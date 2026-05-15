"""executor.run() 捕获 LLMAPIError 测试

验证 HTTP 4xx API 错误（如 DeepSeek/Qwen 内容过滤 HTTP 400）被转为
StepStatus.FAILED 而不是向上抛出导致 session crash。
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sunday.agent.executor import Executor
from sunday.agent.models import AgentState, Step, StepStatus
from sunday.agent.providers.base import LLMAPIError
from sunday.config import SundayConfig


def _make_executor():
    config = SundayConfig()
    tool_registry = MagicMock()
    tool_registry.get_schemas.return_value = []
    return Executor(config, tool_registry=tool_registry)


def _make_step():
    return Step(
        id="step_test",
        intent="测试步骤",
        step_type="generic",
        expected_output="",
        success_criteria="",
    )


def _make_state():
    return AgentState(session_id="test-session", task="测试")


@pytest.mark.asyncio
async def test_llm_api_error_returns_failed_result():
    """LLMAPIError 被 executor.run() 捕获，返回 StepStatus.FAILED（不上抛）。"""
    executor = _make_executor()
    step = _make_step()
    state = _make_state()

    executor._run_inner = AsyncMock(
        side_effect=LLMAPIError("[openai] HTTP 401: Unauthorized")
    )

    result = await executor.run(step, state)

    assert result.status == StepStatus.FAILED
    assert "LLM API 错误" in result.output
    assert "401" in result.output


@pytest.mark.asyncio
async def test_http_400_content_exists_risk_captured():
    """HTTP 400 'Content Exists Risk'（中国 LLM 内容过滤）被捕获，session 不 crash。"""
    executor = _make_executor()
    step = _make_step()
    state = _make_state()

    executor._run_inner = AsyncMock(
        side_effect=LLMAPIError("[openai] HTTP 400: Content Exists Risk")
    )

    result = await executor.run(step, state)

    assert result.status == StepStatus.FAILED
    assert result.step_id == step.id
    assert "Content Exists Risk" in result.output or "400" in result.output


@pytest.mark.asyncio
async def test_other_exception_still_propagates():
    """非网络/API 错误仍正常上抛（不被错误地吞掉）。"""
    executor = _make_executor()
    step = _make_step()
    state = _make_state()

    executor._run_inner = AsyncMock(side_effect=ValueError("编程错误，应上抛"))

    with pytest.raises(ValueError, match="编程错误"):
        await executor.run(step, state)
