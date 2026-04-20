"""/history 命令：Gateway compact payload + TUI 渲染。"""
from __future__ import annotations

from sunday.gateway.server import _compact_turn_for_display, _ellipsize
from sunday.tui.app import _format_history_payload


def test_ellipsize_short_untouched():
    assert _ellipsize("abc", 10) == "abc"
    assert _ellipsize("", 10) == ""


def test_ellipsize_long_truncated_with_marker():
    out = _ellipsize("x" * 500, 100)
    assert out.startswith("x" * 100)
    assert out.endswith("……")


def test_compact_turn_preserves_core_fields():
    turn = {
        "turn_id": "t-abc",
        "turn_index": 2,
        "ts_start": "2026-04-20T10:00:00Z",
        "outcome": "success",
        "user_input": "介绍 DeepSeek V4 发布",
        "plan": {"goal": "梳理 DeepSeek V4 核心能力"},
        "output": "DeepSeek V4 是 ...",
        "execution": [{"step": "1"}],  # 应被丢弃
    }
    out = _compact_turn_for_display(turn)
    assert out["turn_id"] == "t-abc"
    assert out["turn_index"] == 2
    assert out["user_input"] == "介绍 DeepSeek V4 发布"
    assert out["plan_goal"] == "梳理 DeepSeek V4 核心能力"
    assert out["output"] == "DeepSeek V4 是 ..."
    assert "execution" not in out


def test_compact_turn_ellipsizes_long_output():
    turn = {
        "turn_id": "t1", "turn_index": 1, "ts_start": "", "outcome": "success",
        "user_input": "q", "plan": {"goal": "g"}, "output": "x" * 1000,
    }
    out = _compact_turn_for_display(turn)
    assert out["output"].endswith("……")
    assert len(out["output"]) <= 400 + len("……")


def test_compact_turn_handles_missing_plan():
    """plan 字段为 None 或非 dict 时不应崩溃。"""
    turn = {"turn_id": "t1", "turn_index": 1, "user_input": "q", "output": "a"}
    out = _compact_turn_for_display(turn)
    assert out["plan_goal"] == ""


def test_format_history_renders_thread_and_turns():
    payload = {
        "session_id": "abc123",
        "session_thread": {
            "summary": "围绕 DeepSeek V4 发布展开",
            "key_entities": ["DeepSeek V4", "AI 算力"],
        },
        "turns": [
            {
                "turn_index": 1, "ts_start": "2026-04-20T10:00:00.123Z",
                "outcome": "success",
                "user_input": "介绍 DeepSeek V4",
                "plan_goal": "梳理核心能力",
                "output": "V4 发布了...",
            },
            {
                "turn_index": 2, "ts_start": "2026-04-20T10:05:00.000Z",
                "outcome": "success",
                "user_input": "AI 算力基础设施",
                "plan_goal": "讨论对算力的影响",
                "output": "算力层的变化...",
            },
        ],
    }
    text = _format_history_payload(payload)
    assert "abc123" in text
    assert "围绕 DeepSeek V4 发布展开" in text
    assert "DeepSeek V4" in text
    assert "AI 算力" in text
    assert "Turn 1" in text and "Turn 2" in text
    assert "介绍 DeepSeek V4" in text
    assert "梳理核心能力" in text
    assert "V4 发布了..." in text


def test_format_history_without_thread():
    """无 session_thread 字段时，仍应渲染 turns，不崩溃。"""
    payload = {
        "session_id": "s1",
        "turns": [{
            "turn_index": 1, "ts_start": "", "outcome": "success",
            "user_input": "q", "plan_goal": "", "output": "a",
        }],
    }
    text = _format_history_payload(payload)
    assert "s1" in text
    assert "Turn 1" in text
    assert "主线：" not in text  # 无 thread，不应出现主线标签


def test_format_history_empty_turns():
    """空 turns 时给出提示文案。"""
    payload = {"session_id": "s1", "turns": []}
    text = _format_history_payload(payload)
    assert "还没有已完成的 turn" in text


def test_format_history_marks_non_success_outcome():
    """非 success outcome 应在 Turn 头部标注。"""
    payload = {
        "session_id": "s1",
        "turns": [{
            "turn_index": 1, "ts_start": "2026-04-20T10:00:00Z",
            "outcome": "error",
            "user_input": "q", "plan_goal": "g", "output": "",
        }],
    }
    text = _format_history_payload(payload)
    assert "(error)" in text
