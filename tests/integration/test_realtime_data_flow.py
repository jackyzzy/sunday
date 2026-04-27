"""集成测试：Planner → Executor → Verifier 的"实时数据需求"全链路。

复现 turn 563c91ce 的摩尔线程场景：
- 用户问"调研摩尔线程近况"
- 由于代理故障，web_search 一直失败
- 模型凭旧训练数据写"摩尔线程上市不确定"，然而摩尔线程已 2025-12 上市
- 期望：报告首行被强制打"⚠ 未联网"标签 + Verifier 翻转为失败

三个场景：
- A) 正确路径：web_search 成功 → 无标签
- B) 工具失败：web_search 抛错 → 自动打标 + Verifier 失败
- C) 纯写作对照：task=写诗 → 不触发实时性约束
"""
from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import yaml

from sunday.agent.executor import Executor
from sunday.agent.models import (
    AgentState,
    ReactIteration,
    Step,
    StepResult,
)
from sunday.agent.verifier import Verifier


def _make_settings(tmp_path):
    from sunday.config import Settings
    payload = {
        "model": {"provider": "anthropic", "id": "claude-test", "max_tokens": 4096},
    }
    (tmp_path / "agent.yaml").write_text(yaml.dump(payload))
    with patch.dict(os.environ, {
        "ANTHROPIC_API_KEY": "sk-ant-fake",
        "SUNDAY_CONFIGS_DIR": str(tmp_path),
    }):
        s = Settings()
        _ = s.sunday
        return s


def _resp(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "stop_reason": "end_turn"}


def _make_client(responses: list[dict]):
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


# ── Scenario B：工具失败 → 强制打标 + Verifier 翻转（核心修复验证）─────────

async def test_offline_label_added_when_search_fails(tmp_path):
    """模拟 turn 563c91ce 现场：realtime step 调用 web_search 但被代理拦截
    返回 [错误]...，最终输出全是模型旧知识 → Executor 后处理打标。"""
    settings = _make_settings(tmp_path)
    executor = Executor(settings.sunday)

    # 模拟 ReAct 循环里发生过一次 web_search 但失败
    failed_search = ReactIteration(
        iteration=0,
        tool_name="web_search",
        tool_input={"query": "摩尔线程上市"},
        observation="[错误] 网络搜索失败：ConnectError",
    )
    realtime_step = Step(
        id="step_4", intent="调研摩尔线程", requires_realtime_data=True,
    )
    polluted_output = (
        "## 摩尔线程公司分析\n"
        "公司未上市，期权变现需依赖未来 IPO。"
        "若 IPO 成功，期权价值可能大增。"
    )
    labeled = executor._apply_offline_label(
        polluted_output, [failed_search], realtime_step,
    )
    # 关键断言：首行已被强制打标
    assert labeled.startswith("> ⚠ 本节未联网验证")
    assert "摩尔线程" in labeled  # 原内容保留


async def test_verifier_flips_passed_when_realtime_step_lacks_evidence(tmp_path):
    """串通 Verifier：basic verify 通过 + audit 因 realtime+无标签+无联网 → failed。"""
    settings = _make_settings(tmp_path)
    verifier = Verifier(settings.sunday, subject_checker=_AlwaysConsistent())

    step = Step(
        id="step_4", intent="调研摩尔线程",
        success_criteria="包含核心信息",
        requires_realtime_data=True,
    )
    # 输出 250 字以上以触发 subject_consistency 阶段（已被 mock 通过）
    polluted_output = "摩尔线程公司未上市，期权变现需依赖未来 IPO。" * 8
    result = StepResult(step_id="step_4", output=polluted_output)
    state = AgentState(session_id="sess", task="调研摩尔线程")

    verify_pass = json.dumps({
        "passed": True, "reason": "结构清晰，内容完整",
        "should_replan": False,
    })
    mc = _make_client([_resp(verify_pass)])

    with patch("sunday.agent.llm_client._get_http_client", return_value=mc):
        vr = await verifier.check(step, result, state)

    # 关键：尽管 basic verify passed，但 audit 翻转为 failed + replan
    assert vr.passed is False
    assert "工具使用审计失败" in vr.reason
    assert vr.should_replan is True


# ── Scenario A：正确路径 — 联网成功 → 无标签 + Verifier 通过 ───────────────

async def test_successful_search_yields_no_label_and_verify_pass(tmp_path):
    """有一次成功的 web_search → 无标签 + Verifier 通过。"""
    settings = _make_settings(tmp_path)
    executor = Executor(settings.sunday)
    verifier = Verifier(settings.sunday, subject_checker=_AlwaysConsistent())

    # 1) Executor 后处理：成功联网 → 不加标签
    success_search = ReactIteration(
        iteration=0,
        tool_name="web_search",
        tool_input={"query": "moore threads listing"},
        observation="1. 摩尔线程于 2025-12-08 在科创板上市，发行价 114 元。",
    )
    step = Step(
        id="step_4", intent="调研摩尔线程",
        success_criteria="基于 web_search 结果",
        requires_realtime_data=True,
    )
    real_output = (
        "## 摩尔线程公司分析\n"
        "摩尔线程已于 2025-12-08 上市，发行价 114 元，市值显著。"
    ) * 5
    labeled = executor._apply_offline_label(real_output, [success_search], step)
    assert "未联网验证" not in labeled

    # 2) Verifier：basic verify + audit 都过
    result = StepResult(
        step_id="step_4", output=labeled, react_iterations=[success_search],
    )
    state = AgentState(session_id="sess", task="调研摩尔线程")

    verify_pass = json.dumps({
        "passed": True, "reason": "基于实时搜索结果", "should_replan": False,
    })
    mc = _make_client([_resp(verify_pass)])

    with patch("sunday.agent.llm_client._get_http_client", return_value=mc):
        vr = await verifier.check(step, result, state)
    assert vr.passed is True


# ── Scenario C：纯写作对照 — task 不触发，realtime=False 不进 audit ─────

async def test_creative_task_not_subject_to_audit(tmp_path):
    """非 realtime 步骤：纯写作即使 0 工具调用、无标签也不会被 audit 拦截。"""
    settings = _make_settings(tmp_path)
    verifier = Verifier(settings.sunday, subject_checker=_AlwaysConsistent())

    step = Step(
        id="poem_1", intent="写一首关于春天的诗",
        success_criteria="押韵、含春天意象",
        requires_realtime_data=False,
    )
    output = "春风又绿江南岸\n明月何时照我还" * 20
    result = StepResult(step_id="poem_1", output=output)
    state = AgentState(session_id="sess", task="写一首关于春天的诗")

    verify_pass = json.dumps({
        "passed": True, "reason": "押韵、含春天意象", "should_replan": False,
    })
    mc = _make_client([_resp(verify_pass)])

    with patch("sunday.agent.llm_client._get_http_client", return_value=mc):
        vr = await verifier.check(step, result, state)

    assert vr.passed is True
    assert "未联网" not in vr.reason


# ── 辅助：把主题一致性永远当通过 ────────────────────────────────────────

class _AlwaysConsistent:
    async def check(self, output, subjects):
        return MagicMock(consistent=True, reason="跳过")
