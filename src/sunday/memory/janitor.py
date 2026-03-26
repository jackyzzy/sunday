"""Phase 3：MemoryJanitor — 每日日志 TTL 清理"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


class MemoryJanitor:
    """清理超过保留期的每日日志文件。

    只处理 memory/ 目录下符合 YYYY-MM-DD.md 格式的文件。
    """

    def __init__(self, workspace_dir: Path, retention_days: int = 30) -> None:
        self.memory_dir = workspace_dir / "memory"
        self.retention_days = retention_days

    def run(self) -> dict[str, int]:
        """清理过期日志，返回统计信息 {"deleted": N, "kept": M}。"""
        if not self.memory_dir.exists():
            return {"deleted": 0, "kept": 0}

        cutoff = date.today() - timedelta(days=self.retention_days)
        deleted = 0
        kept = 0

        for path in sorted(self.memory_dir.glob("????-??-??.md")):
            log_date = self._parse_date(path.stem)
            if log_date is None:
                continue
            if log_date < cutoff:
                path.unlink()
                logger.info("删除过期日志：%s", path.name)
                deleted += 1
            else:
                kept += 1

        return {"deleted": deleted, "kept": kept}

    @staticmethod
    def _parse_date(stem: str) -> date | None:
        """从文件名（stem）解析日期，解析失败返回 None。"""
        try:
            return date.fromisoformat(stem)
        except ValueError:
            return None
