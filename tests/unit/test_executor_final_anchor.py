"""Executor 最终步骤主题锚定单元测试。"""
from __future__ import annotations

import os
from unittest.mock import patch

import yaml

from sunday.agent.executor import Executor
from sunday.agent.models import AgentState, Plan, SessionThread, Step


def _make_settings(tmp_path, quality: dict | None = None):
    from sunday.config import Settings
    payload = {
        "model": {"provider": "anthropic", "id": "claude-test", "max_tokens": 4096},
    }
    if quality is not None:
        payload["quality"] = quality
    (tmp_path / "agent.yaml").write_text(yaml.dump(payload))
    with patch.dict(os.environ, {
        "ANTHROPIC_API_KEY": "sk-ant-fake",
        "SUNDAY_CONFIGS_DIR": str(tmp_path),
    }):
        s = Settings()
        _ = s.sunday
        return s


def _make_state(task: str, goal: str = "", entities: list[str] | None = None) -> AgentState:
    state = AgentState(session_id="sess", task=task)
    if goal:
        state.plan = Plan(goal=goal, steps=[])
    if entities:
        state.session_thread = SessionThread(summary="", key_entities=entities)
    return state


# ── 关键词命中触发锚定 ──────────────────────────────────────────────────────

def test_anchor_triggered_by_keyword(tmp_path):
    """intent 含"整合"关键词时返回非空 anchor。"""
    settings = _make_settings(tmp_path)
    executor = Executor(settings.sunday)

    step = Step(id="s5", intent="整合所有分析，生成最终的综合建议")
    state = _make_state(
        task="分析自变量",
        goal="围绕自变量做分析",
        entities=["自变量", "具身智能"],
    )
    anchor = executor._build_anchor(step, state)
    assert anchor
    assert "主题锚定" in anchor
    assert "分析自变量" in anchor
    assert "围绕自变量做分析" in anchor
    assert "自变量" in anchor and "具身智能" in anchor


def test_anchor_empty_when_no_keyword(tmp_path):
    """intent 不含关键词时返回空字符串。"""
    settings = _make_settings(tmp_path)
    executor = Executor(settings.sunday)

    step = Step(id="s1", intent="搜索相关文章")
    state = _make_state(task="分析自变量")
    assert executor._build_anchor(step, state) == ""


def test_anchor_disabled_returns_empty(tmp_path):
    """final_step_anchor.enabled=false 时不产生 anchor。"""
    settings = _make_settings(tmp_path, quality={
        "final_step_anchor": {"enabled": False}
    })
    executor = Executor(settings.sunday)

    step = Step(id="s5", intent="整合所有分析")
    state = _make_state(task="分析自变量", goal="围绕自变量")
    assert executor._build_anchor(step, state) == ""


def test_anchor_custom_keywords(tmp_path):
    """自定义 intent_keywords 生效。"""
    settings = _make_settings(tmp_path, quality={
        "final_step_anchor": {"enabled": True, "intent_keywords": ["收尾"]}
    })
    executor = Executor(settings.sunday)

    state = _make_state(task="任务")
    # 默认列表里的"整合"应失效
    assert executor._build_anchor(
        Step(id="s1", intent="整合输出"), state
    ) == ""
    # 新关键词生效
    assert executor._build_anchor(
        Step(id="s1", intent="收尾总结"), state
    ) != ""


def test_anchor_no_subjects_returns_empty(tmp_path):
    """没有 task / goal / entities 时即便命中关键词也返回空字符串。"""
    settings = _make_settings(tmp_path)
    executor = Executor(settings.sunday)

    step = Step(id="s5", intent="整合所有分析")
    state = AgentState(session_id="sess", task="")  # 什么也没有
    assert executor._build_anchor(step, state) == ""


def test_anchor_multiple_keywords_match(tmp_path):
    """intent 中出现多个关键词中的任一都能触发。"""
    settings = _make_settings(tmp_path)
    executor = Executor(settings.sunday)

    state = _make_state(task="写报告")
    for kw in ["整合", "总结", "汇总", "最终", "综合", "清单", "建议", "报告"]:
        step = Step(id="s", intent=f"做一次{kw}")
        anchor = executor._build_anchor(step, state)
        assert anchor, f"关键词 {kw} 应该命中"
