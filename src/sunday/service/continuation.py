"""Session 内连续词识别与任务续写解析。

用途：当用户输入 "请继续"、"继续"、"continue" 等纯粹的连续词时，
Service 在进入 AgentLoop 前先扫描本 session 的 stream.jsonl，
找出最早的实质性 user send 作为"原始任务"，并把最近的失败原因一并带上，
改写成带上下文的 effective_task 再交给 Planner，避免 Planner 在缺乏本会话
锚点的情况下误将 L1/L2 里最热的话题当成当前任务的延展。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# 纯连续词：整条消息仅由以下关键词（可带"请"/轻微标点）构成才命中。
# "请继续分析芯片层" 这类带实质内容的句子必须不命中，交由 Planner 正常处理。
_CONT_PATTERN = re.compile(
    r"^\s*(请)?\s*(继续吧|接着说|往下说|往下|继续|接着|go\s*on|continue)\s*[\s.。!！?？~]*\s*$",
    re.IGNORECASE,
)


@dataclass
class ContinuationResolution:
    """连续词解析结果。

    - found：是否找到可续写的原始任务
    - original_task：本 session 最早一条实质性 user send
    - last_error：最近一次 ERROR / 失败 STATUS 的 message（可能为空）
    """

    found: bool
    original_task: str = ""
    last_error: str = ""


def is_continuation_cue(text: str) -> bool:
    """判断文本是否为纯连续词（无实质内容）。"""
    if not text:
        return False
    return _CONT_PATTERN.match(text) is not None


def resolve_continuation(
    events: list[dict], current_text: str = ""
) -> ContinuationResolution:
    """扫描本 session 的 stream 事件，提取"最早的实质性任务" + "最近的失败原因"。

    参数：
      - events：来自 MemoryClient.sessions.load_events 的事件列表（按时间正序）
      - current_text：当前轮的 user 输入（连续词本身，用于排除）

    返回：
      - ContinuationResolution(found=False) 表示会话尚无实质任务
      - found=True 时 original_task 非空
    """
    original_task = ""
    last_error = ""

    for ev in events:
        ev_type = ev.get("type", "")
        data = ev.get("data", {}) or {}

        if ev_type == "send":
            content = (data.get("content") or "").strip()
            if not content:
                continue
            # 跳过连续词本身（含 current_text 与其他已存在的 "请继续" 轮）
            if is_continuation_cue(content):
                continue
            if content == current_text.strip():
                continue
            # 只取最早的实质性任务作为原始任务
            if not original_task:
                original_task = content

        elif ev_type == "error":
            msg = (data.get("message") or "").strip()
            if msg:
                last_error = msg

        elif ev_type == "status":
            # failed / error 状态里可能带了更具体的错误消息
            state = (data.get("state") or "").lower()
            if state in ("error", "aborted", "failed"):
                msg = (data.get("message") or "").strip()
                if msg:
                    last_error = msg

    if not original_task:
        return ContinuationResolution(found=False)
    return ContinuationResolution(
        found=True, original_task=original_task, last_error=last_error
    )


def build_effective_task(resolution: ContinuationResolution) -> str:
    """根据解析结果拼接给 Planner 的 effective_task。"""
    if not resolution.found:
        return ""
    suffix = (
        f"上次失败原因：{resolution.last_error}"
        if resolution.last_error
        else "上次未完成。"
    )
    return (
        f"{resolution.original_task}\n\n"
        f"（说明：这是对本会话前一次未完成任务的继续。{suffix}）"
    )
