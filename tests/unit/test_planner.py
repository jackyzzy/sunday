"""T2-2 验证：Planner 单元测试（mock httpx，无真实 API）"""
from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import yaml

from sunday.agent.models import AgentState, StepStatus
from sunday.agent.planner import THINKING_BUDGET, Planner, ThinkingLevel


def _make_settings(tmp_path, provider="anthropic"):
    from sunday.config import Settings
    config_file = tmp_path / "agent.yaml"
    config_file.write_text(yaml.dump({
        "model": {"provider": provider, "id": "claude-test"},
        "reasoning": {"max_steps": 10},
    }))
    with patch.dict(os.environ, {
        "ANTHROPIC_API_KEY": "sk-ant-fake",
        "OPENAI_API_KEY": "sk-openai-fake",
        "SUNDAY_CONFIG_FILE": str(config_file),
    }):
        return Settings()


def _plan_response(goal: str = "完成任务", n_steps: int = 2) -> dict:
    steps = [
        {
            "id": f"step_{i+1}",
            "intent": f"步骤 {i+1}",
            "expected_input": "",
            "expected_output": "结果",
            "success_criteria": "完成",
            "depends_on": [],
        }
        for i in range(n_steps)
    ]
    return {"goal": goal, "steps": steps}


def _anthropic_text_response(text: str) -> dict:
    return {
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
    }


def _mock_client(response_data: dict):
    mock_resp = MagicMock()
    mock_resp.json.return_value = response_data
    mock_resp.raise_for_status.return_value = None
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_resp)
    return mock_client


# ── think_and_plan ────────────────────────────────────────────────────────────

async def test_think_and_plan_returns_plan(tmp_path):
    """think_and_plan 解析 JSON 并返回 Plan"""
    settings = _make_settings(tmp_path)
    planner = Planner(settings)

    plan_json = json.dumps(_plan_response("写一首诗", n_steps=2))
    mock_client = _mock_client(_anthropic_text_response(plan_json))

    state = AgentState(session_id="s1", task="写一首五言绝句", thinking_level=ThinkingLevel.OFF)

    with (
        patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-fake"}),
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        plan = await planner.think_and_plan(state)

    assert plan.goal == "写一首诗"
    assert len(plan.steps) == 2
    assert plan.steps[0].id == "step_1"
    assert plan.steps[1].status.value == "pending"


async def test_think_and_plan_thinking_off_no_budget(tmp_path):
    """thinking_level=OFF 时不在 body 中带 thinking 字段"""
    settings = _make_settings(tmp_path)
    planner = Planner(settings)

    plan_json = json.dumps(_plan_response())
    mock_client = _mock_client(_anthropic_text_response(plan_json))

    state = AgentState(session_id="s1", task="test", thinking_level=ThinkingLevel.OFF)
    with (
        patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-fake"}),
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        await planner.think_and_plan(state)

    body = mock_client.post.call_args.kwargs.get("json", {})
    assert "thinking" not in body


async def test_think_and_plan_thinking_high_budget(tmp_path):
    """thinking_level=HIGH 时 budget_tokens=8192"""
    settings = _make_settings(tmp_path)
    planner = Planner(settings)

    plan_json = json.dumps(_plan_response())
    mock_client = _mock_client(_anthropic_text_response(plan_json))

    state = AgentState(session_id="s1", task="test", thinking_level=ThinkingLevel.HIGH)
    with (
        patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-fake"}),
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        await planner.think_and_plan(state)

    body = mock_client.post.call_args.kwargs.get("json", {})
    assert body.get("thinking", {}).get("budget_tokens") == 8192


async def test_think_and_plan_uses_low_temperature(tmp_path):
    """规划阶段 temperature=0.3"""
    settings = _make_settings(tmp_path)
    planner = Planner(settings)

    plan_json = json.dumps(_plan_response())
    mock_client = _mock_client(_anthropic_text_response(plan_json))

    state = AgentState(session_id="s1", task="test", thinking_level=ThinkingLevel.OFF)
    with (
        patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-fake"}),
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        await planner.think_and_plan(state)

    body = mock_client.post.call_args.kwargs.get("json", {})
    assert body.get("temperature") == 0.3


async def test_think_and_plan_parses_markdown_code_block(tmp_path):
    """Plan JSON 被 markdown 代码块包裹时仍能正确解析"""
    settings = _make_settings(tmp_path)
    planner = Planner(settings)

    plan_json = json.dumps(_plan_response("目标"))
    wrapped = f"```json\n{plan_json}\n```"
    mock_client = _mock_client(_anthropic_text_response(wrapped))

    state = AgentState(session_id="s1", task="test", thinking_level=ThinkingLevel.OFF)
    with (
        patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-fake"}),
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        plan = await planner.think_and_plan(state)

    assert plan.goal == "目标"


async def test_think_and_plan_thinking_block_in_response(tmp_path):
    """响应含 thinking block 时，thinking 被存入 Plan.thinking"""
    settings = _make_settings(tmp_path)
    planner = Planner(settings)

    plan_json = json.dumps(_plan_response("思考后的目标"))
    response_data = {
        "content": [
            {"type": "thinking", "thinking": "这是内部思考"},
            {"type": "text", "text": plan_json},
        ],
        "stop_reason": "end_turn",
    }
    mock_client = _mock_client(response_data)

    state = AgentState(session_id="s1", task="test", thinking_level=ThinkingLevel.MEDIUM)
    with (
        patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-fake"}),
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        plan = await planner.think_and_plan(state)

    assert plan.thinking == "这是内部思考"
    assert plan.goal == "思考后的目标"


# ── thinking budget 映射 ──────────────────────────────────────────────────────

def test_thinking_budget_mapping():
    """THINKING_BUDGET 映射正确"""
    assert THINKING_BUDGET[ThinkingLevel.OFF] == 0
    assert THINKING_BUDGET[ThinkingLevel.MINIMAL] == 512
    assert THINKING_BUDGET[ThinkingLevel.LOW] == 1024
    assert THINKING_BUDGET[ThinkingLevel.MEDIUM] == 4096
    assert THINKING_BUDGET[ThinkingLevel.HIGH] == 8192


# ── replan ────────────────────────────────────────────────────────────────────

async def test_replan_returns_new_steps(tmp_path):
    """replan 返回替代步骤列表"""
    from sunday.agent.models import Plan, Step, StepResult

    settings = _make_settings(tmp_path)
    planner = Planner(settings)

    new_steps = [
        {"id": "step_2_new", "intent": "换个方法", "expected_input": "",
         "expected_output": "", "success_criteria": "", "depends_on": []}
    ]
    replan_json = json.dumps({"steps": new_steps})
    mock_client = _mock_client(_anthropic_text_response(replan_json))

    failed_step = Step(id="step_2", intent="原始步骤2", status=StepStatus.FAILED)
    state = AgentState(session_id="s1", task="test")
    state.plan = Plan(
        goal="目标",
        steps=[Step(id="step_1", intent="步骤1"), failed_step]
    )
    state.step_results = [
        StepResult(step_id="step_1", status=StepStatus.DONE, output="ok")
    ]

    with (
        patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-fake"}),
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        new = await planner.replan(failed_step, "失败原因", state)

    assert len(new) == 1
    assert new[0].id == "step_2_new"
    assert new[0].intent == "换个方法"


# ── 新增：_split_thinking / _strip_code_fence / replan 容错 ──────────────────

def test_split_thinking_handles_thinking_tag():
    """<thinking>...</thinking> 标签被正确剥离"""
    from sunday.agent.llm_client import LLMClient
    raw = "<thinking>内部推理过程</thinking>\n{\"steps\": []}"
    thinking, rest = LLMClient.split_thinking(raw)
    assert thinking == "内部推理过程"
    assert rest == '{"steps": []}'


def test_split_thinking_handles_think_tag():
    """DeepSeek 原生 <think>...</think> 标签被正确剥离"""
    from sunday.agent.llm_client import LLMClient
    raw = "<think>chain of thought</think>\n{\"steps\": []}"
    thinking, rest = LLMClient.split_thinking(raw)
    assert thinking == "chain of thought"
    assert rest == '{"steps": []}'


def test_split_thinking_no_tag_returns_raw():
    """无 thinking 标签时原文返回"""
    from sunday.agent.llm_client import LLMClient
    raw = '{"steps": []}'
    thinking, rest = LLMClient.split_thinking(raw)
    assert thinking is None
    assert rest == raw


def test_strip_code_fence_json_block():
    """去除 ```json...``` 包裹"""
    text = "```json\n{\"key\": 1}\n```"
    assert Planner._strip_code_fence(text) == '{"key": 1}'


def test_strip_code_fence_plain_block():
    """去除 ```...``` 包裹（无语言标识符）"""
    text = "```\n{\"key\": 1}\n```"
    assert Planner._strip_code_fence(text) == '{"key": 1}'


def test_strip_code_fence_no_fence():
    """无代码块时原文返回"""
    text = '{"key": 1}'
    assert Planner._strip_code_fence(text) == text


async def test_replan_handles_markdown_wrapped_json(tmp_path):
    """replan 正确处理 markdown code block 包裹的 JSON"""
    from sunday.agent.models import Plan, Step, StepResult

    settings = _make_settings(tmp_path)
    planner = Planner(settings)

    new_steps = [{"id": "step_new", "intent": "替代方案",
                  "expected_input": "", "expected_output": "",
                  "success_criteria": "", "depends_on": []}]
    replan_json = json.dumps({"steps": new_steps})
    wrapped = f"```json\n{replan_json}\n```"
    mock_client = _mock_client(_anthropic_text_response(wrapped))

    failed_step = Step(id="s1", intent="失败步骤", status=StepStatus.FAILED)
    state = AgentState(session_id="s1", task="test")
    state.plan = Plan(goal="目标", steps=[failed_step])

    with (
        patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-fake"}),
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        new = await planner.replan(failed_step, "原因", state)

    assert len(new) == 1
    assert new[0].id == "step_new"


async def test_replan_handles_empty_response(tmp_path):
    """replan 响应为空时返回空列表，不崩溃"""
    from sunday.agent.models import Plan, Step, StepResult

    settings = _make_settings(tmp_path)
    planner = Planner(settings)

    mock_client = _mock_client(_anthropic_text_response(""))

    failed_step = Step(id="s1", intent="失败步骤", status=StepStatus.FAILED)
    state = AgentState(session_id="s1", task="test")
    state.plan = Plan(goal="目标", steps=[failed_step])

    with (
        patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-fake"}),
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        new = await planner.replan(failed_step, "原因", state)

    assert new == []


async def test_replan_handles_think_tag_before_json(tmp_path):
    """replan 响应含 <think> 标签时，正确剥离后解析 JSON"""
    from sunday.agent.models import Plan, Step

    settings = _make_settings(tmp_path)
    planner = Planner(settings)

    new_steps = [{"id": "step_new", "intent": "替代",
                  "expected_input": "", "expected_output": "",
                  "success_criteria": "", "depends_on": []}]
    plan_json = json.dumps({"steps": new_steps})
    raw_with_think = f"<think>我需要重新规划一下</think>\n{plan_json}"
    mock_client = _mock_client(_anthropic_text_response(raw_with_think))

    failed_step = Step(id="s1", intent="步骤", status=StepStatus.FAILED)
    state = AgentState(session_id="s1", task="test")
    state.plan = Plan(goal="目标", steps=[failed_step])

    with (
        patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-fake"}),
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        new = await planner.replan(failed_step, "原因", state)

    assert len(new) == 1
    assert new[0].id == "step_new"
