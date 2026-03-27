"""Gateway 守护进程入口：python -m sunday.gateway"""
from __future__ import annotations

import asyncio
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

import click


def _setup_logging() -> None:
    """配置根 Logger：stderr 控制台 + 轮转文件（~/.sunday/logs/gateway.log）。"""
    log_level_name = os.environ.get("SUNDAY_LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_name, logging.INFO)

    log_dir = Path.home() / ".sunday" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(log_level)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(fmt)
    root.addHandler(console_handler)

    file_handler = RotatingFileHandler(
        log_dir / "gateway.log",
        maxBytes=5 * 1024 * 1024,  # 5 MB
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)


@click.command()
@click.option("--port", default=7899, type=int, help="WebSocket 监听端口")
def main(port: int) -> None:
    """启动 Sunday Gateway 守护进程。"""
    _setup_logging()

    from sunday.config import settings
    from sunday.gateway.server import Gateway

    gw = Gateway(settings)
    try:
        asyncio.run(gw.start(port=port))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
