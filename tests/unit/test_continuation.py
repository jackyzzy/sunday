"""sunday.gateway.continuation 单元测试。"""
from __future__ import annotations

from sunday.gateway.continuation import (
    build_effective_task,
    is_continuation_cue,
    resolve_continuation,
)


def _ev(type_: str, data: dict | None = None) -> dict:
    return {"type": type_, "data": data or {}}


def test_cue_detects_various_phrases():
    for text in [
        "请继续",
        "继续",
        "继续吧",
        "接着",
        "接着说",
        "往下",
        "往下说",
        "continue",
        "Continue",
        "go on",
        "Go On",
        "  请继续  ",
        "请继续。",
        "继续！",
        "continue.",
    ]:
        assert is_continuation_cue(text), f"应命中：{text!r}"


def test_cue_rejects_substantive_input():
    for text in [
        "请继续分析芯片层",
        "继续完成昨天的研报",
        "接着讲下一步",
        "",
        "帮我继续看一下这个错误",
        "continue the analysis",
    ]:
        assert not is_continuation_cue(text), f"不应命中：{text!r}"


def test_resolve_picks_earliest_substantive_send():
    """含多轮"请继续"失败轮 → 解析出最早的实质性 send 作为原始任务。"""
    events = [
        _ev("session_start"),
        _ev("send", {"content": "帮我分析一下，自变量这家公司怎么样？"}),
        _ev("error", {"message": "Server disconnected without sending a response."}),
        _ev("send", {"content": "请继续"}),
        _ev("error", {"message": "Network unreachable"}),
        _ev("send", {"content": "请继续"}),
    ]
    result = resolve_continuation(events, current_text="请继续")
    assert result.found
    assert result.original_task == "帮我分析一下，自变量这家公司怎么样？"
    # 最近的失败原因应覆盖前一条
    assert result.last_error == "Network unreachable"


def test_resolve_status_error_message_captured():
    """status 事件里的 error 状态也应捕获 last_error。"""
    events = [
        _ev("send", {"content": "主任务"}),
        _ev("status", {"state": "error", "message": "status 级错误"}),
    ]
    result = resolve_continuation(events, current_text="请继续")
    assert result.found
    assert result.last_error == "status 级错误"


def test_resolve_empty_session_returns_not_found():
    """stream 只有 session_start → found=False。"""
    events = [_ev("session_start")]
    result = resolve_continuation(events, current_text="请继续")
    assert not result.found
    assert result.original_task == ""


def test_resolve_only_continuation_cues_returns_not_found():
    """会话内所有 user send 都是连续词 → 无原始任务可续写。"""
    events = [
        _ev("session_start"),
        _ev("send", {"content": "请继续"}),
        _ev("send", {"content": "继续"}),
    ]
    result = resolve_continuation(events, current_text="请继续")
    assert not result.found


def test_build_effective_task_with_error():
    resolution = resolve_continuation(
        [
            _ev("send", {"content": "原任务"}),
            _ev("error", {"message": "超时"}),
        ],
        current_text="请继续",
    )
    text = build_effective_task(resolution)
    assert text.startswith("原任务")
    assert "上次失败原因：超时" in text
    assert "前一次未完成任务的继续" in text


def test_build_effective_task_without_error():
    resolution = resolve_continuation(
        [_ev("send", {"content": "原任务"})],
        current_text="请继续",
    )
    text = build_effective_task(resolution)
    assert "上次未完成。" in text


def test_build_effective_task_not_found_returns_empty():
    resolution = resolve_continuation([], current_text="请继续")
    assert build_effective_task(resolution) == ""
