"""集成：Planner THINK → FACT_CHECK（白名单工具）→ PLAN 的端到端流程。

复现场景：Turn 9b3460e4 类型错误 —— Planner 基于被 L2 污染的背景把"自变量"误判为
"AI 算力产业链公司"。引入 FACT_CHECK 子阶段后，Planner 会先通过 web_search 核实
自变量的真实行业归属（具身智能），并把核实到的事实注入 plan prompt。
"""
from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import yaml

from sunday.agent.models import AgentState, ThinkingLevel
from sunday.agent.planner import Planner


def _make_settings(tmp_path):
    from sunday.config import Settings
    (tmp_path / "agent.yaml").write_text(yaml.dump({
        "model": {"provider": "anthropic", "id": "claude-test", "max_tokens": 4096},
        "quality": {
            "fact_check": {
                "enabled": True,
                "max_tool_calls": 2,
                "timeout_seconds": 10,
                "allowed_tools": ["web_search", "read_file"],
            },
        },
    }))
    with patch.dict(os.environ, {
        "ANTHROPIC_API_KEY": "sk-ant-fake",
        "SUNDAY_CONFIGS_DIR": str(tmp_path),
    }):
        s = Settings()
        _ = s.sunday
        return s


def _resp(text: str):
    return {
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
    }


def _mock_client(responses: list[dict]):
    it = iter(responses)

    def _make():
        r = next(it)
        m = MagicMock()
        m.json.return_value = r
        m.raise_for_status.return_value = None
        m.is_success = True
        return m

    mc = AsyncMock()
    mc.post = AsyncMock(side_effect=lambda *a, **kw: _make())
    return mc


def _fake_registry(web_search_output: str):
    reg = MagicMock()
    reg.get_schemas = MagicMock(
        return_value=[
            {"name": "web_search", "description": "", "input_schema": {}},
            {"name": "read_file", "description": "", "input_schema": {}},
        ]
    )
    reg.execute = AsyncMock(return_value=web_search_output)
    return reg


async def test_fact_check_corrects_polluted_plan(tmp_path):
    """完整跑 think → fact_check → plan 三个阶段，核实事实注入最终 plan prompt。"""
    settings = _make_settings(tmp_path)
    planner = Planner(
        settings.sunday,
        # 模拟已被 L2 污染的跨会话背景注入
        system_prompt=(
            "# 跨会话长期背景（仅供参考，非当前会话历史）\n\n"
            "2026-04-18 围绕 AI 算力产业链的技术分层做了系统分析...\n"
        ),
    )

    think_json = json.dumps({
        "needs_fact_check": True,
        "claims": ["自变量 是 什么行业的公司"],
    })
    # Plan 明确采纳了核实后的事实（具身智能），而非记忆里的错误描述（AI 算力）
    plan_json = json.dumps({
        "goal": "围绕用户加入自变量（具身智能公司）这一职业决策做分析",
        "steps": [{
            "id": "step_1",
            "intent": "核实自变量公司作为具身智能公司的基本面",
            "expected_input": "",
            "expected_output": "公司摘要",
            "success_criteria": "包含公司核心业务",
            "depends_on": [],
            "is_simple": False,
        }],
    })
    mc = _mock_client([_resp(think_json), _resp(plan_json)])

    reg = _fake_registry(
        web_search_output=(
            "自变量（Leitz Robotics）是一家具身智能（Embodied AI）公司，"
            "成立于 2023 年，主要研发通用机器人与具身大模型，不是 AI 算力公司。"
        )
    )

    # 捕获 emit
    emits: list[tuple] = []

    async def capture_emit(sid, event, data):
        emits.append((sid, event, data))

    state = AgentState(
        session_id="sess",
        task="我最近要换工作，是否加入自变量团队？",
        thinking_level=ThinkingLevel.OFF,
    )

    with patch("sunday.agent.llm_client._get_http_client", return_value=mc):
        plan = await planner.think_and_plan(
            state, tool_registry=reg, emit=capture_emit
        )

    # 1) 工具被调用一次（只有一条 claim）
    reg.execute.assert_awaited_once()
    call_args = reg.execute.call_args
    assert call_args.args[0] == "web_search"

    # 2) plan 采纳了核实事实（具身智能），而不是 L2 里的 AI 算力
    assert "具身智能" in plan.goal
    assert "AI 算力" not in plan.goal

    # 3) plan 的 LLM prompt 含已核实事实段
    plan_body = mc.post.call_args_list[1].kwargs["json"]
    plan_user_msg = plan_body["messages"][0]["content"]
    assert "已核实事实" in plan_user_msg
    assert "具身智能" in plan_user_msg

    # 4) 两次 plan_fact_check emit：start + done
    fc_events = [e for e in emits if e[1] == "plan_fact_check"]
    assert [e[2]["phase"] for e in fc_events] == ["start", "done"]
    assert fc_events[0][2]["claims"] == ["自变量 是 什么行业的公司"]
    assert len(fc_events[1][2]["facts"]) == 1
