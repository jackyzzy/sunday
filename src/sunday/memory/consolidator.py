"""会话整合 — 从 L3（本轮 state）提炼到 L1/L2 知识层。

`consolidate(state)` 在 ReactAgent.run() 末尾调用：
    1. 同步把本轮摘要追加到今日 daily/YYYY-MM-DD.md
    2. 后台异步触发 LLM 提炼，结果按 section 写入 MEMORY.md / USER.md

走 MemoryClient.knowledge.* 接口，不直接读写文件。失败静默，不影响主路径。
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING

from sunday.agent.llm_client import LLMClient

if TYPE_CHECKING:
    from sunday.agent.models import AgentState
    from sunday.config import SundayConfig
    from sunday.memory.client import MemoryClient

logger = logging.getLogger(__name__)


_PROMPT_TEMPLATE = """请从以下会话摘要中，提取值得长期记忆的事实、用户偏好和工具约定。

任务：{task}
步骤结果摘要：
{steps_summary}

请以 JSON 格式输出（不要输出任何其他内容）：
{{
  "memories": [
    {{"section": "用户偏好", "key": "键名", "value": "具体内容", "priority": "P1"}}
  ],
  "user_profile": [
    {{"key": "键名", "value": "具体内容"}}
  ]
}}

如果没有值得记录的内容，memories 和 user_profile 均返回空列表。
只输出 JSON。"""


class Consolidator:
    """从会话状态整合到 L1/L2 知识层的 LLM 编排。"""

    def __init__(self, client: "MemoryClient", config: "SundayConfig | None") -> None:
        self._client = client
        self._config = config

    async def consolidate(self, state: "AgentState") -> None:
        """同步写今日日志，异步触发 AI 整合。"""
        steps_summary = "\n".join(
            f"- {r.step_id}（{'✓' if r.passed else '✗'}）：{r.output[:200]}"
            for r in state.team_results
        )
        log_content = (
            f"\n## 任务：{state.task}\n"
            f"会话ID：{state.session_id}\n"
            f"{steps_summary or '无执行记录'}\n"
        )
        await self._client.knowledge.append_daily(log_content)

        if self._config is not None:
            asyncio.create_task(self._ai_consolidate(state))

    async def _ai_consolidate(self, state: "AgentState") -> None:
        """后台 LLM 提炼。失败仅记录日志。"""
        try:
            assert self._config is not None
            steps_summary = "\n".join(
                f"- {r.step_id}: {r.output[:300]}"
                for r in state.team_results
            )
            prompt = _PROMPT_TEMPLATE.format(
                task=state.task,
                steps_summary=steps_summary or "无",
            )
            raw = await LLMClient.call_text(
                self._config.model, prompt, max_tokens=2048, timeout=60,
            )
            data = _parse_json_safe(raw)
            if data is None:
                return

            for mem in data.get("memories", []):
                await self._client.knowledge.upsert_fact(
                    section=mem.get("section", "任务历史摘要"),
                    key=mem.get("key", ""),
                    value=mem.get("value", ""),
                    priority=mem.get("priority", "P1"),
                )
            for prof in data.get("user_profile", []):
                await self._client.knowledge.upsert_user_profile(
                    key=prof.get("key", ""),
                    value=prof.get("value", ""),
                )

            n = len(data.get("memories", []))
            await self._client.knowledge.append_daily(
                f"\n[AI 整合完成] 提取 {n} 条记忆\n"
            )
        except Exception:
            logger.exception("Consolidator._ai_consolidate 失败（后台任务）")


def _parse_json_safe(raw: str) -> dict | None:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        logger.warning("AI 整合：JSON 解析失败，原文：%s", raw[:200])
        return None
