"""Run the spotiosu web app: python -m webapp"""
from __future__ import annotations

import logging
from pathlib import Path

import uvicorn

from bot.config import Config, ConfigError
from .server import create_app


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    log = logging.getLogger("spotiosu.web")
    try:
        config = Config.load(Path("config.json"))
    except ConfigError as exc:
        log.error("%s", exc)
        return 2

    app = create_app(config)
    log.info("Open %s in your browser and click 'Sign in with osu!'",
             config.web.public_base)
    uvicorn.run(app, host=config.web.host, port=config.web.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
