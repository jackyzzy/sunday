"""会话历史提取：把 stream.jsonl 事件流还原为 Planner 可用的 Message 列表。

CLI 与 Gateway 共享本模块，避免重复实现。
"""
from __future__ import annotations

from sunday.agent.models import Message as ConvMessage


def extract_conversation(events: list[dict], max_turns: int = 5) -> list[ConvMessage]:
    """从 stream.jsonl 事件列表提取最近 N 轮对话。

    事件来源：
      - `send` → user 消息
      - `plan` → 紧跟其后的 assistant 消息前端会被标注 `[plan_goal: ...]`
      - `done` → assistant 消息
      - `error` → assistant "[执行出错：...]" 占位

    返回的 Message 列表严格按 user/assistant 交替，排除最后一条 user（当前轮自身的输入，
    会作为 task 单独传入，不重复注入）。每轮 2 条，最后取 `max_turns * 2` 条。
    """
    messages: list[ConvMessage] = []
    pending_plan_goal: str | None = None

    for ev in events:
        ev_type = ev.get("type", "")
        data = ev.get("data", {}) or {}

        if ev_type == "send":
            content = data.get("content", "")
            if content:
                messages.append(ConvMessage(role="user", content=content))
            pending_plan_goal = None

        elif ev_type == "plan":
            goal = data.get("goal", "") or ""
            if goal:
                pending_plan_goal = goal

        elif ev_type == "done":
            content = data.get("content", "")
            if content:
                if pending_plan_goal:
                    content = f"[plan_goal: {pending_plan_goal}]\n{content}"
                messages.append(ConvMessage(role="assistant", content=content))
            pending_plan_goal = None

        elif ev_type == "error":
            msg = data.get("message", "")
            if msg:
                messages.append(ConvMessage(role="assistant", content=f"[执行出错：{msg}]"))
            pending_plan_goal = None

    # 排除最后一条 user（当前轮的输入会通过 task 字段单独传入）
    if messages and messages[-1].role == "user":
        messages = messages[:-1]

    return messages[-(max_turns * 2):]
