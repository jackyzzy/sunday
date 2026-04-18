"""MemoryManager — 文件系统记忆读写

记忆分层：
    L1  ~/.sunday/memory/MEMORY.md    — 长期事实摘要（AI 维护）
    L1  ~/.sunday/memory/USER.md      — 用户画像（AI 维护）
    L2  ~/.sunday/memory/daily/       — 每日摘要（从 L3 提炼）

# TODO(memory-service): 未来抽象为独立 MemoryStore 服务接口
# class MemoryStore(Protocol):
#     async def read(self, layer: str, key: str) -> str: ...
#     async def write(self, layer: str, key: str, content: str) -> None: ...
#     async def append(self, layer: str, key: str, content: str) -> None: ...
#     async def search(self, query: str, layer: str | None = None) -> list[str]: ...
#       - L3 跨会话检索：按关键词或语义查找历史 turns
#     async def consolidate(self, from_layer: str, to_layer: str) -> None: ...
# 当前阶段：FileMemoryManager 直接实现，不经过协议层
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sunday.agent.models import AgentState
    from sunday.config import SundayConfig

logger = logging.getLogger(__name__)

_CONSOLIDATE_PROMPT = """请从以下会话摘要中，提取值得长期记忆的事实、用户偏好和工具约定。

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


class MemoryManager:
    """文件系统记忆管理器（L1+L2 层）。

    所有写操作通过 asyncio.Lock 串行化。
    文件写入使用 .tmp + rename 保证原子性。

    路径约定：
        memory_dir/MEMORY.md          — L1 长期事实摘要
        memory_dir/USER.md            — L1 用户画像
        memory_dir/daily/YYYY-MM-DD.md — L2 每日摘要
    """

    def __init__(self, memory_dir: Path, config: "SundayConfig | None" = None,
                 workspace_dir: Path | None = None) -> None:
        self.memory_dir = memory_dir
        # workspace_dir 保留兼容参数，实际读写均使用 memory_dir
        self.workspace_dir = workspace_dir or memory_dir
        self.config = config
        self._lock = asyncio.Lock()

        # 确保目录存在
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        (self.memory_dir / "daily").mkdir(parents=True, exist_ok=True)

    # ── 公开接口 ──────────────────────────────────────────────────────────

    async def append_daily_log(self, content: str) -> None:
        """追加内容到今日 memory/daily/YYYY-MM-DD.md（L2 层）。"""
        today = date.today().isoformat()
        log_path = self.memory_dir / "daily" / f"{today}.md"
        async with self._lock:
            with log_path.open("a", encoding="utf-8") as f:
                f.write(content)
                if not content.endswith("\n"):
                    f.write("\n")

    async def update_memory(
        self,
        section: str,
        key: str,
        value: str,
        priority: str = "P1",
    ) -> None:
        """在 MEMORY.md 的 section 下 upsert 一条记忆条目（L1 层）。

        格式：- [P1][2026-03-26] key：value
        如果该 key 已存在，则更新；否则插入到 section 末尾。
        """
        memory_path = self.memory_dir / "MEMORY.md"
        today = date.today().isoformat()
        entry = f"- [{priority}][{today}] {key}：{value}"

        async with self._lock:
            content = memory_path.read_text(encoding="utf-8") if memory_path.exists() else ""
            lines = content.splitlines(keepends=True)

            section_header = f"## {section}"
            section_idx = None
            for i, line in enumerate(lines):
                if line.strip() == section_header:
                    section_idx = i
                    break

            if section_idx is None:
                if not content.endswith("\n") and content:
                    lines.append("\n")
                lines.append(f"\n{section_header}\n")
                lines.append(f"{entry}\n")
            else:
                key_marker = f"] {key}："
                key_idx = None
                for i in range(section_idx + 1, len(lines)):
                    if lines[i].startswith("## "):
                        break
                    if key_marker in lines[i]:
                        key_idx = i
                        break

                if key_idx is not None:
                    lines[key_idx] = f"{entry}\n"
                else:
                    insert_at = len(lines)
                    for i in range(section_idx + 1, len(lines)):
                        if lines[i].startswith("## "):
                            insert_at = i
                            break
                    lines.insert(insert_at, f"{entry}\n")

            self._atomic_write(memory_path, "".join(lines))

    async def update_user_profile(self, key: str, value: str) -> None:
        """在 USER.md 中 upsert 一条用户画像条目（L1 层）。

        格式：- key：value
        """
        user_path = self.memory_dir / "USER.md"
        entry = f"- {key}：{value}"
        key_marker = f"- {key}："

        async with self._lock:
            if user_path.exists():
                content = user_path.read_text(encoding="utf-8")
            else:
                content = "# 用户画像\n"
            lines = content.splitlines(keepends=True)

            for i, line in enumerate(lines):
                if line.strip().startswith(key_marker):
                    lines[i] = f"{entry}\n"
                    self._atomic_write(user_path, "".join(lines))
                    return

            if not content.endswith("\n") and content:
                lines.append("\n")
            lines.append(f"{entry}\n")
            self._atomic_write(user_path, "".join(lines))

    async def consolidate_session(self, state: "AgentState") -> None:
        """会话结束时调用：同步写今日日志，异步触发 AI 整合（L3→L2→L1）。"""
        steps_summary = "\n".join(
            f"- {r.step_id}（{'✓' if r.passed else '✗'}）：{r.output[:200]}"
            for r in state.team_results
        )
        log_content = (
            f"\n## 任务：{state.task}\n"
            f"会话ID：{state.session_id}\n"
            f"{steps_summary or '无执行记录'}\n"
        )
        await self.append_daily_log(log_content)

        if self.config is not None:
            asyncio.create_task(self._ai_consolidate(state))

    # ── 私有方法 ──────────────────────────────────────────────────────────

    async def _ai_consolidate(self, state: "AgentState") -> None:
        """后台 LLM 提炼：从会话中提取值得长期记忆的内容（L3→L1）。

        失败时仅记录日志，不抛出异常，不影响用户。
        """
        try:
            if self.config is None:
                return

            from sunday.agent.llm_client import LLMClient

            model_cfg = self.config.model

            steps_summary = "\n".join(
                f"- {r.step_id}: {r.output[:300]}"
                for r in state.team_results
            )
            prompt = _CONSOLIDATE_PROMPT.format(
                task=state.task,
                steps_summary=steps_summary or "无",
            )

            raw = await LLMClient.call_text(model_cfg, prompt, max_tokens=2048, timeout=60)
            data = self._parse_json_safe(raw)
            if data is None:
                return

            for mem in data.get("memories", []):
                await self.update_memory(
                    section=mem.get("section", "任务历史摘要"),
                    key=mem.get("key", ""),
                    value=mem.get("value", ""),
                    priority=mem.get("priority", "P1"),
                )
            for prof in data.get("user_profile", []):
                await self.update_user_profile(
                    key=prof.get("key", ""),
                    value=prof.get("value", ""),
                )

            n = len(data.get("memories", []))
            await self.append_daily_log(f"\n[AI 整合完成] 提取 {n} 条记忆\n")

        except Exception:
            logger.exception("_ai_consolidate 失败（后台任务，不影响用户）")

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        """先写 .tmp，再 rename，保证写入原子性。"""
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.rename(path)

    @staticmethod
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
