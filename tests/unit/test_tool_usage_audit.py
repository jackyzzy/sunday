"""tool_usage_audit 单元测试 — DefaultToolUsageAuditor + 工厂方法。"""
from __future__ import annotations

import os
from unittest.mock import patch

import yaml

from sunday.agent.models import ReactIteration, Step
from sunday.agent.tool_usage_audit import (
    DefaultToolUsageAuditor,
    ToolAuditResult,
    _AlwaysPassAuditor,
    build_tool_usage_auditor,
)


def _make_settings(tmp_path, quality: dict | None = None):
    from sunday.config import Settings
    payload = {"model": {"provider": "anthropic", "id": "claude-test", "max_tokens": 4096}}
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


def _step(realtime: bool) -> Step:
    return Step(id="s", intent="x", requires_realtime_data=realtime)


def _iter(tool: str, observation: str) -> ReactIteration:
    return ReactIteration(iteration=0, tool_name=tool,
                          tool_input={}, observation=observation)


# ── DefaultToolUsageAuditor ──────────────────────────────────────────────

async def test_non_realtime_step_passes():
    out = await DefaultToolUsageAuditor().check(_step(False), [], "any output")
    assert out.passed
    assert "非实时" in out.reason


async def test_realtime_with_successful_search_passes():
    iters = [_iter("web_search", "1. 摩尔线程 2025-12 上市...")]
    out = await DefaultToolUsageAuditor().check(_step(True), iters, "ok")
    assert out.passed
    assert "成功" in out.reason


async def test_realtime_with_fetch_url_success_passes():
    iters = [_iter("fetch_url", "<html>...")]
    out = await DefaultToolUsageAuditor().check(_step(True), iters, "ok")
    assert out.passed


async def test_realtime_with_offline_label_passes():
    """未联网但已带兜底标签 → 容忍通过。"""
    out = await DefaultToolUsageAuditor().check(
        _step(True), [], "> ⚠ 本节未联网验证\n\n报告内容...",
    )
    assert out.passed
    assert "已含未联网标签" in out.reason


async def test_realtime_no_tool_no_label_fails():
    """未联网且无标签 → 判失败。"""
    out = await DefaultToolUsageAuditor().check(_step(True), [], "纯生成内容")
    assert not out.passed
    assert "未成功调联网工具" in out.reason


async def test_realtime_search_returned_error_does_not_count():
    """工具返回 [错误]... 不算成功联网。"""
    iters = [_iter("web_search", "[错误] 网络搜索失败：ConnectError")]
    out = await DefaultToolUsageAuditor().check(_step(True), iters, "fallback content")
    assert not out.passed


async def test_realtime_non_search_tool_does_not_count():
    """调用了别的工具不算联网（如 read_file/write_file）。"""
    iters = [_iter("write_file", "ok")]
    out = await DefaultToolUsageAuditor().check(_step(True), iters, "ok")
    assert not out.passed


async def test_returns_tool_audit_result_type():
    out = await DefaultToolUsageAuditor().check(_step(False), [], "")
    assert isinstance(out, ToolAuditResult)


# ── 工厂方法 ──────────────────────────────────────────────────────────────

def test_factory_returns_default_when_enabled(tmp_path):
    settings = _make_settings(tmp_path)
    auditor = build_tool_usage_auditor(settings.sunday)
    assert isinstance(auditor, DefaultToolUsageAuditor)


def test_factory_returns_always_pass_when_disabled(tmp_path):
    settings = _make_settings(tmp_path, quality={
        "tool_usage_audit": {"enabled": False},
    })
    auditor = build_tool_usage_auditor(settings.sunday)
    assert isinstance(auditor, _AlwaysPassAuditor)


async def test_always_pass_passes_realtime_unconditionally():
    out = await _AlwaysPassAuditor().check(_step(True), [], "随便什么内容")
    assert out.passed
