"""Taming pyTelegramBotAPI's logging.

Long polling drops a connection every few minutes; that is normal, not an
incident. Left alone, telebot answers each one with a full ``ReadTimeoutError``
traceback plus a second record carrying the same traceback again, which buries
anything worth reading. Here transient failures collapse to one line, repeats
are suppressed for a while, and everything else is passed through to loguru
unchanged.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from loguru import logger

TELEBOT_LOGGER = "TeleBot"
REPEAT_INTERVAL_S = 60.0

# Matched against the lowercased message; first hit wins, so order matters.
_TRANSIENT = (
    ("read timed out", "read timeout"),
    ("readtimeout", "read timeout"),
    ("connection aborted", "connection aborted"),
    ("connection reset", "connection reset"),
    ("connection refused", "connection refused"),
    ("remote end closed", "connection closed by Telegram"),
    ("max retries exceeded", "connection failed"),
    ("temporary failure in name resolution", "DNS failure"),
    ("name or service not known", "DNS failure"),
    ("connectionerror", "connection failed"),
    ("timed out", "timeout"),
    ("bad gateway", "Telegram returned 502"),
    ("gateway time-out", "Telegram returned 504"),
    ("internal server error", "Telegram returned 500"),
    ("too many requests", "rate limited"),
    (
        "terminated by other getupdates request",
        "another instance is polling with the same token",
    ),
)

_DROP = ("exception traceback",)


@dataclass(frozen=True, slots=True)
class Collapsed:
    """What to do with one telebot log record."""

    message: str | None
    level: str = "WARNING"
    reason: str | None = None

    @property
    def dropped(self) -> bool:
        return self.message is None


def collapse(message: str, level: str = "ERROR") -> Collapsed:
    """Turn a telebot record into the single line it deserves."""
    lowered = message.lower()

    if any(marker in lowered for marker in _DROP):
        return Collapsed(None)

    for marker, reason in _TRANSIENT:
        if marker in lowered:
            return Collapsed(
                f"reconnecting to Telegram ({reason})", "WARNING", reason
            )

    return Collapsed(message.strip() or None, level)


class RepeatSuppressor:
    """Lets the same reason through at most once per interval."""

    def __init__(
        self,
        interval_s: float = REPEAT_INTERVAL_S,
        *,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._interval_s = interval_s
        self._clock = clock
        self._last: dict[str, float] = {}
        self._suppressed: dict[str, int] = {}

    def allow(self, reason: str) -> tuple[bool, int]:
        """``(should_log, how_many_were_swallowed_since_the_last_line)``."""
        now = self._clock()
        previous = self._last.get(reason)
        if previous is not None and now - previous < self._interval_s:
            self._suppressed[reason] = self._suppressed.get(reason, 0) + 1
            return False, 0
        self._last[reason] = now
        return True, self._suppressed.pop(reason, 0)


class TelebotHandler(logging.Handler):
    def __init__(self, suppressor: RepeatSuppressor | None = None):
        super().__init__()
        self._suppressor = suppressor or RepeatSuppressor()

    def emit(self, record: logging.LogRecord) -> None:
        collapsed = collapse(record.getMessage(), record.levelname)
        if collapsed.dropped:
            return

        message = collapsed.message
        if collapsed.reason is not None:
            allowed, swallowed = self._suppressor.allow(collapsed.reason)
            if not allowed:
                return
            if swallowed:
                message = f"{message}, {swallowed} more since the last line"

        # depth would point into logging's internals, so name the source instead
        logger.log(collapsed.level, "telebot: {}", message)


def configure_telebot_logging(
    suppressor: RepeatSuppressor | None = None,
) -> logging.Logger:
    """Route telebot through loguru and strip the traceback spam."""
    telebot_logger = logging.getLogger(TELEBOT_LOGGER)
    for handler in list(telebot_logger.handlers):
        telebot_logger.removeHandler(handler)
    telebot_logger.addHandler(TelebotHandler(suppressor))
    telebot_logger.propagate = False
    telebot_logger.setLevel(logging.INFO)
    return telebot_logger
