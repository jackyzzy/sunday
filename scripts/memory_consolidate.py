#!/usr/bin/env python3
"""每日记忆维护脚本 — 触发一次 TTL 清理。

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
    from sunday.bootstrap import build_memory_client
    from sunday.config import settings

    cfg = settings.sunday
    logger.info("开始记忆维护，memory_dir=%s", cfg.agent.memory_dir)

    # 关闭后台 janitor，由本脚本一次性触发扫描
    client = build_memory_client(cfg, run_janitor=False)
    try:
        stats = client.knowledge._sweep_expired_daily()
        logger.info("日志清理完成：删除 %d 个，保留 %d 个", stats["deleted"], stats["kept"])
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
