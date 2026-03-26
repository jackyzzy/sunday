"""Gateway 守护进程入口：python -m sunday.gateway"""
from __future__ import annotations

import asyncio
import logging

import click

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


@click.command()
@click.option("--port", default=7899, type=int, help="WebSocket 监听端口")
def main(port: int) -> None:
    """启动 Sunday Gateway 守护进程。"""
    from sunday.config import settings
    from sunday.gateway.server import Gateway

    gw = Gateway(settings)
    try:
        asyncio.run(gw.start(port=port))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
