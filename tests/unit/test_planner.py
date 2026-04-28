"""T2-2 验证：Planner 单元测试（mock httpx，无真实 API）"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import yaml

from sunday.agent.models import THINKING_BUDGET, AgentState, StepStatus, ThinkingLevel
from sunday.agent.planner import Planner
from sunday.agent.utils import strip_code_fence
from sunday.templates.loader import TemplateLoader


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
        "SUNDAY_CONFIGS_DIR": str(tmp_path),
    }):
        s = Settings()
        _ = s.sunday  # 触发 cached_property，确保在 patch.dict 上下文内读取正确配置
        return s


def _load_builtin_templates() -> TemplateLoader:
    """加载项目内置任务模板（用于依赖 synthesis 注入的测试）"""
    project_root = Path(__file__).parent.parent.parent
    loader = TemplateLoader(builtin_dir=project_root / "configs" / "templates")
    loader.discover()
    return loader


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
    planner = Planner(settings.sunday)

    plan_json = json.dumps(_plan_response("写一首诗", n_steps=2))
    mock_client = _mock_client(_anthropic_text_response(plan_json))

    state = AgentState(session_id="s1", task="写一首五言绝句", thinking_level=ThinkingLevel.OFF)

    with (
        patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-fake"}),
        patch("sunday.agent.llm_client._get_http_client", return_value=mock_client),
    ):
        plan = await planner.think_and_plan(state)

    assert plan.goal == "写一首诗"
    assert len(plan.steps) == 2
    assert plan.steps[0].id == "step_1"
    assert plan.steps[1].status.value == "pending"


async def test_think_and_plan_thinking_off_no_budget(tmp_path):
    """thinking_level=OFF 时不在 body 中带 thinking 字段"""
    settings = _make_settings(tmp_path)
    planner = Planner(settings.sunday)

    plan_json = json.dumps(_plan_response())
    mock_client = _mock_client(_anthropic_text_response(plan_json))

    state = AgentState(session_id="s1", task="test", thinking_level=ThinkingLevel.OFF)
    with (
        patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-fake"}),
        patch("sunday.agent.llm_client._get_http_client", return_value=mock_client),
    ):
        await planner.think_and_plan(state)

    body = mock_client.post.call_args.kwargs.get("json", {})
    assert "thinking" not in body


async def test_think_and_plan_thinking_high_budget(tmp_path):
    """thinking_level=HIGH 时 budget_tokens=8192"""
    settings = _make_settings(tmp_path)
    planner = Planner(settings.sunday)

    plan_json = json.dumps(_plan_response())
    mock_client = _mock_client(_anthropic_text_response(plan_json))

    state = AgentState(session_id="s1", task="test", thinking_level=ThinkingLevel.HIGH)
    with (
        patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-fake"}),
        patch("sunday.agent.llm_client._get_http_client", return_value=mock_client),
    ):
        await planner.think_and_plan(state)

    body = mock_client.post.call_args.kwargs.get("json", {})
    assert body.get("thinking", {}).get("budget_tokens") == 8192


async def test_think_and_plan_uses_low_temperature(tmp_path):
    """规划阶段 temperature=0.3"""
    settings = _make_settings(tmp_path)
    planner = Planner(settings.sunday)

    plan_json = json.dumps(_plan_response())
    mock_client = _mock_client(_anthropic_text_response(plan_json))

    state = AgentState(session_id="s1", task="test", thinking_level=ThinkingLevel.OFF)
    with (
        patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-fake"}),
        patch("sunday.agent.llm_client._get_http_client", return_value=mock_client),
    ):
        await planner.think_and_plan(state)

    body = mock_client.post.call_args.kwargs.get("json", {})
    assert body.get("temperature") == 0.3


async def test_think_and_plan_parses_markdown_code_block(tmp_path):
    """Plan JSON 被 markdown 代码块包裹时仍能正确解析"""
    settings = _make_settings(tmp_path)
    planner = Planner(settings.sunday)

    plan_json = json.dumps(_plan_response("目标"))
    wrapped = f"```json\n{plan_json}\n```"
    mock_client = _mock_client(_anthropic_text_response(wrapped))

    state = AgentState(session_id="s1", task="test", thinking_level=ThinkingLevel.OFF)
    with (
        patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-fake"}),
        patch("sunday.agent.llm_client._get_http_client", return_value=mock_client),
    ):
        plan = await planner.think_and_plan(state)

    assert plan.goal == "目标"


async def test_think_and_plan_thinking_block_in_response(tmp_path):
    """响应含 thinking block 时，thinking 被存入 Plan.thinking"""
    settings = _make_settings(tmp_path)
    planner = Planner(settings.sunday)

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
        patch("sunday.agent.llm_client._get_http_client", return_value=mock_client),
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
    planner = Planner(settings.sunday)

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
        patch("sunday.agent.llm_client._get_http_client", return_value=mock_client),
    ):
        new = await planner.replan(failed_step, "失败原因", state)

    assert len(new) == 1
    assert new[0].id == "step_2_new"
    assert new[0].intent == "换个方法"


# ── 新增：_split_thinking / _strip_code_fence / replan 容错 ──────────────────

def test_split_thinking_handles_thinking_tag():
    """<thinking>...</thinking> 标签被正确剥离"""
    from sunday.agent.providers.utils import split_thinking
    raw = "<thinking>内部推理过程</thinking>\n{\"steps\": []}"
    thinking, rest = split_thinking(raw)
    assert thinking == "内部推理过程"
    assert rest == '{"steps": []}'


def test_split_thinking_handles_think_tag():
    """DeepSeek 原生 <think>...</think> 标签被正确剥离"""
    from sunday.agent.providers.utils import split_thinking
    raw = "<think>chain of thought</think>\n{\"steps\": []}"
    thinking, rest = split_thinking(raw)
    assert thinking == "chain of thought"
    assert rest == '{"steps": []}'


def test_split_thinking_no_tag_returns_raw():
    """无 thinking 标签时原文返回"""
    from sunday.agent.providers.utils import split_thinking
    raw = '{"steps": []}'
    thinking, rest = split_thinking(raw)
    assert thinking is None
    assert rest == raw


def test_strip_code_fence_json_block():
    """去除 ```json...``` 包裹"""
    text = "```json\n{\"key\": 1}\n```"
    assert strip_code_fence(text) == '{"key": 1}'


def test_strip_code_fence_plain_block():
    """去除 ```...``` 包裹（无语言标识符）"""
    text = "```\n{\"key\": 1}\n```"
    assert strip_code_fence(text) == '{"key": 1}'


def test_strip_code_fence_no_fence():
    """无代码块时原文返回"""
    text = '{"key": 1}'
    assert strip_code_fence(text) == text


async def test_replan_handles_markdown_wrapped_json(tmp_path):
    """replan 正确处理 markdown code block 包裹的 JSON"""
    from sunday.agent.models import Plan, Step

    settings = _make_settings(tmp_path)
    planner = Planner(settings.sunday)

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
        patch("sunday.agent.llm_client._get_http_client", return_value=mock_client),
    ):
        new = await planner.replan(failed_step, "原因", state)

    assert len(new) == 1
    assert new[0].id == "step_new"


async def test_replan_handles_empty_response(tmp_path):
    """replan 响应为空时返回空列表，不崩溃"""
    from sunday.agent.models import Plan, Step

    settings = _make_settings(tmp_path)
    planner = Planner(settings.sunday)

    mock_client = _mock_client(_anthropic_text_response(""))

    failed_step = Step(id="s1", intent="失败步骤", status=StepStatus.FAILED)
    state = AgentState(session_id="s1", task="test")
    state.plan = Plan(goal="目标", steps=[failed_step])

    with (
        patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-fake"}),
        patch("sunday.agent.llm_client._get_http_client", return_value=mock_client),
    ):
        new = await planner.replan(failed_step, "原因", state)

    assert new == []


async def test_replan_handles_think_tag_before_json(tmp_path):
    """replan 响应含 <think> 标签时，正确剥离后解析 JSON"""
    from sunday.agent.models import Plan, Step

    settings = _make_settings(tmp_path)
    planner = Planner(settings.sunday)

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
        patch("sunday.agent.llm_client._get_http_client", return_value=mock_client),
    ):
        new = await planner.replan(failed_step, "原因", state)

    assert len(new) == 1
    assert new[0].id == "step_new"


# ── task_type + synthesis 步骤注入 ─────────────────────────────────────────────

async def test_plan_task_type_parsed(tmp_path):
    """Plan JSON 含 task_type 和 synthesis_document_name 时正确解析"""
    settings = _make_settings(tmp_path)
    planner = Planner(settings.sunday)

    plan_data = {
        "task_type": "analysis_recommendation",
        "synthesis_document_name": "五一自驾游路线分析.md",
        "goal": "完成路线分析",
        "steps": [{
            "id": "step_1", "intent": "收集数据",
            "expected_input": "", "expected_output": "数据",
            "success_criteria": "有数据", "depends_on": [],
            "step_type": "research",
        }],
    }
    mock_client = _mock_client(_anthropic_text_response(json.dumps(plan_data)))
    state = AgentState(session_id="s1", task="规划五一旅行", thinking_level=ThinkingLevel.OFF)

    with (
        patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-fake"}),
        patch("sunday.agent.llm_client._get_http_client", return_value=mock_client),
    ):
        plan = await planner.think_and_plan(state)

    assert plan.task_type == "analysis_recommendation"
    assert plan.synthesis_document_name == "五一自驾游路线分析.md"
    assert plan.steps[0].step_type == "research"


async def test_synthesis_step_injected_for_analysis_recommendation(tmp_path):
    """task_type=analysis_recommendation 时自动追加 step_final_synthesis"""
    settings = _make_settings(tmp_path)
    planner = Planner(settings.sunday, templates=_load_builtin_templates())

    plan_data = {
        "task_type": "analysis_recommendation",
        "synthesis_document_name": "深圳自驾游路线分析.md",
        "goal": "路线分析",
        "steps": [
            {"id": "step_1", "intent": "收集数据", "expected_input": "",
             "expected_output": "数据", "success_criteria": "有数据", "depends_on": []},
            {"id": "step_2", "intent": "生成候选路线", "expected_input": "",
             "expected_output": "路线", "success_criteria": "有路线", "depends_on": ["step_1"]},
        ],
    }
    mock_client = _mock_client(_anthropic_text_response(json.dumps(plan_data)))
    state = AgentState(session_id="s1", task="规划旅行", thinking_level=ThinkingLevel.OFF)

    with (
        patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-fake"}),
        patch("sunday.agent.llm_client._get_http_client", return_value=mock_client),
    ):
        plan = await planner.think_and_plan(state)

    assert len(plan.steps) == 3
    last = plan.steps[-1]
    assert last.id == "step_final_synthesis"
    assert last.step_type == "synthesis"
    assert last.is_simple is True
    assert last.requires_realtime_data is False
    assert set(last.depends_on) == {"step_1", "step_2"}
    assert "深圳自驾游路线分析.md" in last.intent


async def test_synthesis_step_injected_for_research(tmp_path):
    """research 类型也启用 synthesis（验证模板驱动而非硬编码）"""
    settings = _make_settings(tmp_path)
    planner = Planner(settings.sunday, templates=_load_builtin_templates())

    plan_data = {
        "task_type": "research",
        "synthesis_document_name": "AI 芯片市场调研报告.md",
        "goal": "AI 芯片调研",
        "steps": [
            {"id": "step_1", "intent": "搜索信息", "expected_input": "",
             "expected_output": "信息", "success_criteria": "有信息", "depends_on": []},
        ],
    }
    mock_client = _mock_client(_anthropic_text_response(json.dumps(plan_data)))
    state = AgentState(session_id="s1", task="调研市场", thinking_level=ThinkingLevel.OFF)

    with (
        patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-fake"}),
        patch("sunday.agent.llm_client._get_http_client", return_value=mock_client),
    ):
        plan = await planner.think_and_plan(state)

    assert len(plan.steps) == 2
    last = plan.steps[-1]
    assert last.id == "step_final_synthesis"
    assert last.step_type == "synthesis"
    assert "AI 芯片市场调研报告.md" in last.intent


async def test_synthesis_step_not_injected_for_creative(tmp_path):
    """task_type=creative 时不追加 synthesis（synthesis.enabled=false）"""
    settings = _make_settings(tmp_path)
    planner = Planner(settings.sunday, templates=_load_builtin_templates())

    plan_data = {
        "task_type": "creative",
        "goal": "写一首诗",
        "steps": [{
            "id": "step_1", "intent": "写五言绝句", "expected_input": "",
            "expected_output": "诗", "success_criteria": "押韵工整", "depends_on": [],
        }],
    }
    mock_client = _mock_client(_anthropic_text_response(json.dumps(plan_data)))
    state = AgentState(session_id="s1", task="写一首诗", thinking_level=ThinkingLevel.OFF)

    with (
        patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-fake"}),
        patch("sunday.agent.llm_client._get_http_client", return_value=mock_client),
    ):
        plan = await planner.think_and_plan(state)

    assert len(plan.steps) == 1


async def test_synthesis_step_not_injected_when_no_templates(tmp_path):
    """未加载 templates 时不注入 synthesis（防止意外行为）"""
    settings = _make_settings(tmp_path)
    planner = Planner(settings.sunday)  # 不传 templates

    plan_data = {
        "task_type": "analysis_recommendation",
        "synthesis_document_name": "should_not_be_used.md",
        "goal": "目标",
        "steps": [{
            "id": "step_1", "intent": "x", "expected_input": "",
            "expected_output": "", "success_criteria": "", "depends_on": [],
        }],
    }
    mock_client = _mock_client(_anthropic_text_response(json.dumps(plan_data)))
    state = AgentState(session_id="s1", task="test", thinking_level=ThinkingLevel.OFF)

    with (
        patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-fake"}),
        patch("sunday.agent.llm_client._get_http_client", return_value=mock_client),
    ):
        plan = await planner.think_and_plan(state)

    assert len(plan.steps) == 1


async def test_synthesis_document_name_fallback(tmp_path):
    """LLM 未给 synthesis_document_name 时，从模板 hint 或 task_type 兜底"""
    settings = _make_settings(tmp_path)
    planner = Planner(settings.sunday, templates=_load_builtin_templates())

    plan_data = {
        "task_type": "diagnosis",  # 启用 synthesis 但 LLM 漏掉 doc name
        "goal": "排查问题",
        "steps": [{
            "id": "step_1", "intent": "现象收集", "expected_input": "",
            "expected_output": "现象", "success_criteria": "有现象", "depends_on": [],
        }],
    }
    mock_client = _mock_client(_anthropic_text_response(json.dumps(plan_data)))
    state = AgentState(session_id="s1", task="排查 bug", thinking_level=ThinkingLevel.OFF)

    with (
        patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-fake"}),
        patch("sunday.agent.llm_client._get_http_client", return_value=mock_client),
    ):
        plan = await planner.think_and_plan(state)

    # synthesis 步骤被注入，文档名取 fallback
    assert len(plan.steps) == 2
    assert plan.steps[-1].step_type == "synthesis"
    assert plan.synthesis_document_name  # 非空


async def test_task_type_catalog_injected_into_prompt(tmp_path):
    """templates 加载后，prompt 中应注入任务类型清单"""
    settings = _make_settings(tmp_path)
    planner = Planner(settings.sunday, templates=_load_builtin_templates())

    plan_data = _plan_response("test", n_steps=1)
    mock_client = _mock_client(_anthropic_text_response(json.dumps(plan_data)))
    state = AgentState(session_id="s1", task="任意任务", thinking_level=ThinkingLevel.OFF)

    with (
        patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-fake"}),
        patch("sunday.agent.llm_client._get_http_client", return_value=mock_client),
    ):
        await planner.think_and_plan(state)

    body = mock_client.post.call_args.kwargs.get("json", {})
    user_msg = body["messages"][0]["content"]
    assert "可选任务类型清单" in user_msg
    assert "analysis_recommendation" in user_msg
    assert "creative" in user_msg
    assert "diagnosis" in user_msg


async def test_replan_inherits_step_type_from_failed_step(tmp_path):
    """replan 生成的新步骤继承 failed_step.step_type"""
    from sunday.agent.models import Plan, Step, StepResult

    settings = _make_settings(tmp_path)
    planner = Planner(settings.sunday)

    # LLM 返回的新步骤未指定 step_type
    new_steps = [
        {"id": "step_2_new", "intent": "换个方法", "expected_input": "",
         "expected_output": "", "success_criteria": "", "depends_on": []}
    ]
    replan_json = json.dumps({"steps": new_steps})
    mock_client = _mock_client(_anthropic_text_response(replan_json))

    failed_step = Step(
        id="step_2", intent="原始步骤2", status=StepStatus.FAILED,
        step_type="analysis",  # 失败步骤有 step_type
    )
    state = AgentState(session_id="s1", task="test")
    state.plan = Plan(goal="目标", steps=[failed_step])

    with (
        patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-fake"}),
        patch("sunday.agent.llm_client._get_http_client", return_value=mock_client),
    ):
        new = await planner.replan(failed_step, "失败原因", state)

    assert len(new) == 1
    assert new[0].step_type == "analysis"  # 自动继承
