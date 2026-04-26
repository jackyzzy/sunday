"""FACT_CHECK 子阶段单元测试：预算、超时、白名单、关开关。"""
from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import yaml

from sunday.agent.fact_check import VerifiedFact, _build_args, _choose_tool, maybe_fact_check


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


def _mock_registry(
    available_tools: list[str],
    execute_return: str = "搜索结果：自变量是具身智能公司",
    execute_side_effect=None,
):
    reg = MagicMock()
    reg.get_schemas = MagicMock(
        return_value=[{"name": t, "description": "", "input_schema": {}}
                      for t in available_tools]
    )
    if execute_side_effect is not None:
        reg.execute = AsyncMock(side_effect=execute_side_effect)
    else:
        reg.execute = AsyncMock(return_value=execute_return)
    return reg


# ── 开关 / 前置条件 ─────────────────────────────────────────────────────────

async def test_disabled_returns_empty(tmp_path):
    """quality.fact_check.enabled=false 时直接返回 []，不调用任何工具。"""
    settings = _make_settings(tmp_path, quality={"fact_check": {"enabled": False}})
    reg = _mock_registry(["web_search"])

    out = await maybe_fact_check(["自变量 是 什么公司"], settings.sunday, reg)
    assert out == []
    reg.execute.assert_not_called()


async def test_no_claims_returns_empty(tmp_path):
    """无 claims 时直接返回 []。"""
    settings = _make_settings(tmp_path)
    reg = _mock_registry(["web_search"])
    out = await maybe_fact_check([], settings.sunday, reg)
    assert out == []
    reg.execute.assert_not_called()


async def test_no_tool_registry_returns_empty(tmp_path):
    """tool_registry=None 时直接返回 []。"""
    settings = _make_settings(tmp_path)
    out = await maybe_fact_check(["自变量"], settings.sunday, None)
    assert out == []


async def test_allowed_tools_all_unavailable_returns_empty(tmp_path):
    """白名单工具都不在可用列表时返回 []。"""
    settings = _make_settings(tmp_path, quality={
        "fact_check": {"enabled": True, "allowed_tools": ["web_search", "read_file"]},
    })
    reg = _mock_registry(["email"])  # 仅有 email，白名单全都不可用
    out = await maybe_fact_check(["自变量"], settings.sunday, reg)
    assert out == []
    reg.execute.assert_not_called()


# ── 核心行为 ────────────────────────────────────────────────────────────────

async def test_happy_path_returns_facts(tmp_path):
    """至少一个白名单工具可用 → 至少一次调用 → 返回事实。"""
    settings = _make_settings(tmp_path, quality={"fact_check": {"enabled": True}})
    reg = _mock_registry(["web_search"], execute_return="自变量是具身智能公司")

    out = await maybe_fact_check(
        ["自变量 是 什么行业的公司"], settings.sunday, reg, session_id="s1"
    )
    assert len(out) == 1
    assert isinstance(out[0], VerifiedFact)
    assert out[0].tool == "web_search"
    assert "具身智能" in out[0].finding
    reg.execute.assert_called_once()
    args, _ = reg.execute.call_args
    assert args[0] == "web_search"
    assert args[1] == {"query": "自变量 是 什么行业的公司", "max_results": 3}


async def test_budget_limits_calls(tmp_path):
    """max_tool_calls=1 时，即便有 3 条 claims 也只调用一次。"""
    settings = _make_settings(tmp_path, quality={
        "fact_check": {
            "enabled": True, "max_tool_calls": 1, "timeout_seconds": 30,
            "allowed_tools": ["web_search"],
        }
    })
    reg = _mock_registry(["web_search"])

    out = await maybe_fact_check(
        ["c1", "c2", "c3"], settings.sunday, reg
    )
    assert len(out) == 1
    assert reg.execute.call_count == 1


async def test_tool_failure_skips_claim_continues(tmp_path):
    """工具单次失败不影响其他 claims（第 1 条失败、第 2 条成功）。"""
    settings = _make_settings(tmp_path, quality={
        "fact_check": {"enabled": True, "max_tool_calls": 2, "timeout_seconds": 30}
    })
    reg = _mock_registry(
        ["web_search"],
        execute_side_effect=[Exception("网络错误"), "c2 finding"],
    )
    out = await maybe_fact_check(["c1", "c2"], settings.sunday, reg)
    assert len(out) == 1
    assert out[0].claim == "c2"


async def test_timeout_returns_partial(tmp_path):
    """整体超时时返回已收集到的部分事实，不抛出。"""
    settings = _make_settings(tmp_path, quality={
        "fact_check": {
            "enabled": True, "max_tool_calls": 5, "timeout_seconds": 1,
            "allowed_tools": ["web_search"],
        }
    })

    async def slow_execute(*_args, **_kwargs):
        await asyncio.sleep(2)  # 超过 1s timeout
        return "永远不会收到"

    reg = _mock_registry(["web_search"], execute_side_effect=slow_execute)
    out = await maybe_fact_check(["c1"], settings.sunday, reg)
    assert out == []  # 第一个调用就超时


# ── 辅助函数 ────────────────────────────────────────────────────────────────

def test_choose_tool_prefers_path_for_read_file():
    """看起来像文件路径的 claim 优先选 read_file。"""
    assert _choose_tool("/tmp/report.md", ["read_file", "web_search"]) == "read_file"


def test_choose_tool_falls_back_to_web_search():
    """非路径 claim 使用 web_search。"""
    assert _choose_tool("自变量 是 什么公司", ["web_search", "read_file"]) == "web_search"


def test_choose_tool_http_url_goes_to_web_search():
    """HTTP URL 不当作 read_file 路径，走 web_search。"""
    assert _choose_tool("http://example.com/page", ["read_file", "web_search"]) == "web_search"


def test_build_args_web_search():
    assert _build_args("自变量", "web_search") == {"query": "自变量", "max_results": 3}


def test_build_args_read_file():
    assert _build_args("/tmp/x.md", "read_file") == {"path": "/tmp/x.md"}
