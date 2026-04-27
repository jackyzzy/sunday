"""会话主线（session_thread）增量维护

每轮 turn 结束后调用 `update_session_thread()`：用轻量 LLM 调用把本轮 user_input +
plan.goal + output 精要化，与 session_thread（持久化在 meta.json）增量合并。

session_thread 让后续轮次的 Planner 能识别"当前任务是主线延续"，避免把每一轮
当作独立任务重新规划。

失败静默：LLM/JSON 异常均吞掉，不影响用户主路径（主线缺失只是降级为无主线拼接）。
"""
from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING

from sunday.agent.models import SessionThread

if TYPE_CHECKING:
    from sunday.config import SundayConfig
    from sunday.memory.client import MemoryClient

logger = logging.getLogger(__name__)

_OUTPUT_SNIPPET_LIMIT = 1500
_USER_INPUT_LIMIT = 800


async def update_session_thread(
    session_id: str,
    turn_buffer: dict,
    client: "MemoryClient",
    config: "SundayConfig",
) -> None:
    """用 LLM 把本轮信息增量合并进 session_thread。

    只有 turn_buffer.outcome == "success" 时才更新（aborted/error 不污染主线）。
    """
    if turn_buffer.get("outcome") != "success":
        return

    from sunday.agent.llm_client import LLMClient

    prev_thread = await client.sessions.get_thread(session_id)
    prev_summary = prev_thread.summary if prev_thread else ""
    prev_entities = list(prev_thread.key_entities) if prev_thread else []

    user_input = (turn_buffer.get("user_input") or "")[:_USER_INPUT_LIMIT]
    plan = turn_buffer.get("plan") or {}
    plan_goal = plan.get("goal", "") if isinstance(plan, dict) else ""
    output = (turn_buffer.get("output") or "")[:_OUTPUT_SNIPPET_LIMIT]

    try:
        prompt_tpl = config.load_prompt("thread_update")
    except FileNotFoundError:
        logger.warning("thread_update.md 不存在，跳过 session_thread 更新")
        return

    prompt = prompt_tpl.format(
        prev_summary=prev_summary or "（首轮，无已有主线）",
        prev_entities="、".join(prev_entities) or "（无）",
        user_input=user_input,
        plan_goal=plan_goal or "（未记录）",
        output=output or "（空）",
    )

    try:
        raw = await LLMClient.call_text(
            config.model,
            prompt,
            max_tokens=512,
            temperature=0.2,
            timeout=30,
        )
    except Exception:
        logger.exception("session_thread LLM 调用失败（不影响用户）")
        return

    data = _parse_json(raw)
    if not data:
        return

    new_summary = (data.get("summary") or "").strip() or prev_summary
    new_entities_raw = data.get("key_entities") or prev_entities
    seen: set[str] = set()
    new_entities: list[str] = []
    for e in new_entities_raw:
        if not isinstance(e, str):
            continue
        e = e.strip()
        if not e or e in seen:
            continue
        seen.add(e)
        new_entities.append(e)

    new_thread = SessionThread(
        summary=new_summary,
        key_entities=new_entities,
        updated_at_turn=turn_buffer.get("turn_id", ""),
    )
    try:
        await client.sessions.save_thread(session_id, new_thread)
    except Exception:
        logger.exception("session_thread 写入失败（不影响用户）")


def _parse_json(raw: str) -> dict | None:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[-1].strip() == "```":
            text = "\n".join(lines[1:-1])
        else:
            text = "\n".join(lines[1:])
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except (json.JSONDecodeError, ValueError):
                pass
    logger.warning("session_thread JSON 解析失败，原文：%s", raw[:200])
    return None
