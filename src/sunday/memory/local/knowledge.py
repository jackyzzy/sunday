"""LocalKnowledgeClient — 文件系统实现的 knowledge sub-client。

L1：memory/MEMORY.md（长期事实）+ memory/USER.md（用户画像）
L2：memory/daily/YYYY-MM-DD.md（每日摘要）

TTL 清理：以后台任务方式定期扫描 daily/ 目录，删除超过 retention_days
的过期文件。任务在 close() 时优雅取消。

ensure_seeded(template_dir) 是首次部署的 seed 入口（由 sunday init 调用）：
从项目模板复制缺失的 L1 文件（MEMORY.md / USER.md）。
"""
from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import date, timedelta
from pathlib import Path

from sunday.memory._io import atomic_write_text

logger = logging.getLogger(__name__)

_TTL_INTERVAL_SECONDS = 6 * 3600  # 默认每 6 小时扫一次


class LocalKnowledgeClient:
    """L1 + L2 知识层的本地实现。"""

    def __init__(
        self,
        memory_dir: Path,
        *,
        retention_days: int = 30,
        run_janitor: bool = True,
    ) -> None:
        self._dir = memory_dir
        self._daily_dir = memory_dir / "daily"
        self._retention_days = retention_days
        self._lock = asyncio.Lock()
        self._janitor_task: asyncio.Task | None = None
        self._closed = False

        self._dir.mkdir(parents=True, exist_ok=True)
        self._daily_dir.mkdir(parents=True, exist_ok=True)

        if run_janitor:
            self._start_janitor()

    # ── 写接口 ────────────────────────────────────────────────────────────

    async def append_daily(self, content: str, *, day: date | None = None) -> None:
        target_day = (day or date.today()).isoformat()
        log_path = self._daily_dir / f"{target_day}.md"
        async with self._lock:
            with log_path.open("a", encoding="utf-8") as f:
                f.write(content)
                if not content.endswith("\n"):
                    f.write("\n")

    async def upsert_fact(
        self,
        section: str,
        key: str,
        value: str,
        *,
        priority: str = "P1",
    ) -> None:
        memory_path = self._dir / "MEMORY.md"
        today = date.today().isoformat()
        entry = f"- [{priority}][{today}] {key}：{value}"
        section_header = f"## {section}"
        key_marker = f"] {key}："

        async with self._lock:
            content = memory_path.read_text(encoding="utf-8") if memory_path.exists() else ""
            lines = content.splitlines(keepends=True)

            section_idx = next(
                (i for i, line in enumerate(lines) if line.strip() == section_header),
                None,
            )
            if section_idx is None:
                if content and not content.endswith("\n"):
                    lines.append("\n")
                lines.append(f"\n{section_header}\n")
                lines.append(f"{entry}\n")
            else:
                key_idx = None
                next_section = len(lines)
                for i in range(section_idx + 1, len(lines)):
                    if lines[i].startswith("## "):
                        next_section = i
                        break
                    if key_marker in lines[i]:
                        key_idx = i
                        break
                if key_idx is not None:
                    lines[key_idx] = f"{entry}\n"
                else:
                    lines.insert(next_section, f"{entry}\n")
            atomic_write_text(memory_path, "".join(lines))

    async def upsert_user_profile(self, key: str, value: str) -> None:
        user_path = self._dir / "USER.md"
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
                    atomic_write_text(user_path, "".join(lines))
                    return

            if content and not content.endswith("\n"):
                lines.append("\n")
            lines.append(f"{entry}\n")
            atomic_write_text(user_path, "".join(lines))

    # ── 读接口 ────────────────────────────────────────────────────────────

    async def read_layer(self, layer: str) -> str:
        path = self._dir / f"{layer}.md"
        return path.read_text(encoding="utf-8") if path.exists() else ""

    async def read_daily(self, day: date) -> str:
        path = self._daily_dir / f"{day.isoformat()}.md"
        return path.read_text(encoding="utf-8") if path.exists() else ""

    async def ensure_seeded(self, template_dir: Path) -> list[str]:
        """从项目模板首次复制 L1 文件（MEMORY.md / USER.md）。幂等。"""
        seeded: list[str] = []
        self._dir.mkdir(parents=True, exist_ok=True)
        self._daily_dir.mkdir(parents=True, exist_ok=True)

        if not template_dir.is_dir():
            return seeded

        for fname in ("MEMORY.md", "USER.md"):
            dest = self._dir / fname
            src = template_dir / fname
            if not dest.exists() and src.exists():
                shutil.copy2(src, dest)
                logger.info("seed L1 文件：%s", dest)
                seeded.append(fname)
        return seeded

    # ── 生命周期 ──────────────────────────────────────────────────────────

    async def aclose(self) -> None:
        self._closed = True
        if self._janitor_task is not None:
            self._janitor_task.cancel()
            try:
                await self._janitor_task
            except (asyncio.CancelledError, Exception):
                pass
            self._janitor_task = None

    # ── TTL 清理（后台任务）───────────────────────────────────────────────

    def _start_janitor(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # 无运行中的 loop（如同步上下文构造），延后由首次写触发
        self._janitor_task = loop.create_task(self._janitor_loop())

    async def _janitor_loop(self) -> None:
        try:
            while not self._closed:
                self._sweep_expired_daily()
                await asyncio.sleep(_TTL_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("knowledge janitor 循环异常")

    def _sweep_expired_daily(self) -> dict[str, int]:
        if not self._daily_dir.exists():
            return {"deleted": 0, "kept": 0}
        cutoff = date.today() - timedelta(days=self._retention_days)
        deleted = 0
        kept = 0
        for path in sorted(self._daily_dir.glob("????-??-??.md")):
            try:
                log_date = date.fromisoformat(path.stem)
            except ValueError:
                continue
            if log_date < cutoff:
                path.unlink()
                logger.info("删除过期日志：%s", path.name)
                deleted += 1
            else:
                kept += 1
        return {"deleted": deleted, "kept": kept}
