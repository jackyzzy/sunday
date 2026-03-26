#!/usr/bin/env python3
"""每日记忆整合脚本 — 供 cron 调用。

用法：
  uv run python scripts/memory_consolidate.py
  make consolidate
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

# 将 src/ 加入 path（非安装模式运行时）
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("memory_consolidate")


async def main() -> None:
    from sunday.config import settings
    from sunday.memory.janitor import MemoryJanitor

    cfg = settings.sunday
    workspace_dir = cfg.agent.workspace_dir
    retention_days = cfg.memory.log_retention_days

    logger.info("开始记忆整合，workspace=%s", workspace_dir)

    # 清理过期日志
    janitor = MemoryJanitor(workspace_dir, retention_days=retention_days)
    stats = janitor.run()
    logger.info("日志清理完成：删除 %d 个，保留 %d 个", stats["deleted"], stats["kept"])

    logger.info("记忆整合完成")


if __name__ == "__main__":
    asyncio.run(main())
