"""Executor 未联网兜底打标的单元测试 — `_apply_offline_label` + `_build_realtime_notice`。"""
from __future__ import annotations

import os
from unittest.mock import patch

import yaml

from sunday.agent.executor import Executor
from sunday.agent.models import ReactIteration, Step


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


# ── _build_realtime_notice ────────────────────────────────────────────────

def test_realtime_notice_only_when_step_requires_it(tmp_path):
    settings = _make_settings(tmp_path)
    executor = Executor(settings.sunday)

    assert executor._build_realtime_notice(Step(id="s", intent="x")) == ""
    notice = executor._build_realtime_notice(
        Step(id="s", intent="调研", requires_realtime_data=True)
    )
    assert "时效性约束" in notice
    assert "web_search" in notice
    assert "未联网验证" in notice


# ── _apply_offline_label：触发 / 不触发的多组合 ────────────────────────────

def test_label_added_when_realtime_and_no_tool_call(tmp_path):
    settings = _make_settings(tmp_path)
    executor = Executor(settings.sunday)
    step = Step(id="s", intent="x", requires_realtime_data=True)
    out = executor._apply_offline_label("摩尔线程上市不确定", [], step)
    assert out.startswith("> ⚠ 本节未联网验证")
    assert "摩尔线程上市不确定" in out


def test_label_not_added_when_realtime_false(tmp_path):
    settings = _make_settings(tmp_path)
    executor = Executor(settings.sunday)
    step = Step(id="s", intent="x", requires_realtime_data=False)
    out = executor._apply_offline_label("纯写作输出", [], step)
    assert out == "纯写作输出"


def test_label_not_added_when_search_success(tmp_path):
    settings = _make_settings(tmp_path)
    executor = Executor(settings.sunday)
    step = Step(id="s", intent="x", requires_realtime_data=True)
    iters = [
        ReactIteration(iteration=0, tool_name="web_search",
                       tool_input={"query": "moore threads"},
                       observation="1. 摩尔线程 2025-12-08 上市..."),
    ]
    out = executor._apply_offline_label("摩尔线程已上市", iters, step)
    assert out == "摩尔线程已上市"
    assert "未联网" not in out


def test_label_added_when_search_returns_error(tmp_path):
    """工具返回 [错误]... 字符串视为未联网，仍打标。"""
    settings = _make_settings(tmp_path)
    executor = Executor(settings.sunday)
    step = Step(id="s", intent="x", requires_realtime_data=True)
    iters = [
        ReactIteration(iteration=0, tool_name="web_search",
                       tool_input={"query": "x"},
                       observation="[错误] 网络搜索失败：ConnectError"),
    ]
    out = executor._apply_offline_label("信息基于推断", iters, step)
    assert out.startswith("> ⚠ 本节未联网验证")


def test_label_idempotent_when_already_present(tmp_path):
    """模型自己已经写了标签时不重复打。"""
    settings = _make_settings(tmp_path)
    executor = Executor(settings.sunday)
    step = Step(id="s", intent="x", requires_realtime_data=True)
    pre = "> ⚠ 本节未联网验证，基于训练数据。\n\n内容..."
    out = executor._apply_offline_label(pre, [], step)
    assert out == pre
    assert out.count("⚠ 本节未联网验证") == 1


def test_label_skipped_when_disabled(tmp_path):
    settings = _make_settings(tmp_path, quality={
        "offline_output_label": {"enabled": False},
    })
    executor = Executor(settings.sunday)
    step = Step(id="s", intent="x", requires_realtime_data=True)
    out = executor._apply_offline_label("X", [], step)
    assert out == "X"


def test_non_search_tool_does_not_count_as_online(tmp_path):
    """调用了别的工具（如 read_file 写入），不算真正联网。"""
    settings = _make_settings(tmp_path)
    executor = Executor(settings.sunday)
    step = Step(id="s", intent="x", requires_realtime_data=True)
    iters = [
        ReactIteration(iteration=0, tool_name="write_file",
                       tool_input={"path": "/tmp/x"},
                       observation="ok"),
    ]
    out = executor._apply_offline_label("内容", iters, step)
    assert out.startswith("> ⚠ 本节未联网验证")


def test_fetch_url_success_also_counts(tmp_path):
    settings = _make_settings(tmp_path)
    executor = Executor(settings.sunday)
    step = Step(id="s", intent="x", requires_realtime_data=True)
    iters = [
        ReactIteration(iteration=0, tool_name="fetch_url",
                       tool_input={"url": "https://example.com"},
                       observation="HTML 解析后的纯文本..."),
    ]
    out = executor._apply_offline_label("内容", iters, step)
    assert "未联网" not in out
