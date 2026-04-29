"""测试 extract_conversation：会话失败后历史上下文重建"""
from __future__ import annotations

from sunday.memory.history import extract_conversation as _extract_conversation


def _ev(type_: str, data: dict, turn_id: str = "t1") -> dict:
    return {"type": type_, "turn_id": turn_id, "data": data}


def test_normal_turn_extracted():
    """正常轮次（send + done 有内容）应正确提取。"""
    events = [
        _ev("send", {"content": "帮我查天气"}),
        _ev("done", {"content": "今天晴，25°C"}),
    ]
    history = _extract_conversation(events, max_turns=5)
    assert len(history) == 2
    assert history[0].role == "user" and history[0].content == "帮我查天气"
    assert history[1].role == "assistant" and history[1].content == "今天晴，25°C"


def test_failed_turn_preserves_context():
    """失败轮次（done 内容为空，有 error 事件）应保留原始任务和错误信息作为 assistant 消息。"""
    events = [
        _ev("send", {"content": "执行复杂任务"}, turn_id="t1"),
        _ev("error", {"message": "连接超时"}, turn_id="t1"),
        _ev("done", {"content": ""}, turn_id="t1"),
        _ev("turn_end", {"outcome": "error"}, turn_id="t1"),
        _ev("send", {"content": "请继续"}, turn_id="t2"),
    ]
    history = _extract_conversation(events, max_turns=5)
    # "请继续"是当前轮，应被裁掉；剩余应为 [user: 执行复杂任务, assistant: 执行出错：连接超时]
    assert len(history) == 2
    assert history[0].role == "user"
    assert history[0].content == "执行复杂任务"
    assert history[1].role == "assistant"
    assert "连接超时" in history[1].content


def test_current_turn_user_message_trimmed():
    """当前轮 send 已写入流时，应被末尾裁剪逻辑去掉，不进入历史。"""
    events = [
        _ev("send", {"content": "第一个问题"}, turn_id="t1"),
        _ev("done", {"content": "这是答案"}, turn_id="t1"),
        _ev("send", {"content": "当前问题"}, turn_id="t2"),
    ]
    history = _extract_conversation(events, max_turns=5)
    assert len(history) == 2
    assert history[0].content == "第一个问题"
    assert history[1].content == "这是答案"


def test_max_turns_limits_history():
    """max_turns 限制最多返回 N 轮历史。"""
    events = []
    for i in range(4):
        events.append(_ev("send", {"content": f"问题{i}"}, turn_id=f"t{i}"))
        events.append(_ev("done", {"content": f"答案{i}"}, turn_id=f"t{i}"))
    # 当前轮
    events.append(_ev("send", {"content": "当前问题"}, turn_id="t4"))

    history = _extract_conversation(events, max_turns=2)
    # 2 轮 = 4 条消息，当前轮被裁掉
    assert len(history) == 4
    assert history[0].content == "问题2"
