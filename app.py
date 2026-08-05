"""Entrypoint: read the environment, wire the pieces, poll."""

from __future__ import annotations

import sys

from loguru import logger

from tgmusicbot.bot.handlers import build_app
from tgmusicbot.config import Config
from tgmusicbot.errors import ConfigError
from tgmusicbot.logsetup import configure_telebot_logging


def main() -> int:
    try:
        config = Config.from_env()
    except ConfigError as error:
        logger.error("{}: {}", error.variable, error.reason)
        return 2

    if config.log_path:
        config.log_path.parent.mkdir(parents=True, exist_ok=True)
        logger.add(
            config.log_path,
            format="{time:YYYY-MM-DD HH:mm} {level} {message}",
            rotation="10 MB",
            retention=5,
        )

    configure_telebot_logging()
    config.library_root.mkdir(parents=True, exist_ok=True)
    build_app(config).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
