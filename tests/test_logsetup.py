import logging

import pytest

from tgmusicbot.logsetup import (
    RepeatSuppressor,
    TelebotHandler,
    collapse,
    configure_telebot_logging,
)

READ_TIMEOUT = (
    "Threaded polling exception: A request to the Telegram API was unsuccessful. "
    "HTTPSConnectionPool(host='api.telegram.org', port=443): Read timed out. "
    "(read timeout=25)"
)


class ManualClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


@pytest.mark.parametrize(
    "message,reason",
    [
        (READ_TIMEOUT, "read timeout"),
        ("ConnectionResetError: Connection reset by peer", "connection reset"),
        ("('Connection aborted.', RemoteDisconnected(...))", "connection aborted"),
        ("Max retries exceeded with url: /bot123/getUpdates", "connection failed"),
        ("Temporary failure in name resolution", "DNS failure"),
        ("Error code: 502. Description: Bad Gateway", "Telegram returned 502"),
        ("Error code: 429. Description: Too Many Requests", "rate limited"),
        (
            "Error code: 409. Conflict: terminated by other getUpdates request",
            "another instance is polling with the same token",
        ),
    ],
)
def test_transient_failures_collapse_to_one_line(message, reason):
    result = collapse(message)
    assert result.message == f"reconnecting to Telegram ({reason})"
    assert result.level == "WARNING"
    assert result.reason == reason


def test_the_duplicate_traceback_record_is_dropped():
    """telebot logs the message, then logs the whole traceback again."""
    assert collapse("Exception traceback:\nTraceback (most recent call last):").dropped


def test_a_real_error_is_passed_through_untouched():
    result = collapse("Bot token is invalid", "ERROR")
    assert result.message == "Bot token is invalid"
    assert result.level == "ERROR"
    assert result.reason is None


def test_repeats_are_suppressed_then_counted():
    clock = ManualClock()
    suppressor = RepeatSuppressor(60.0, clock=clock)

    assert suppressor.allow("read timeout") == (True, 0)
    assert suppressor.allow("read timeout") == (False, 0)
    assert suppressor.allow("read timeout") == (False, 0)
    assert suppressor.allow("connection reset") == (True, 0)  # per reason

    clock.now = 60.0
    assert suppressor.allow("read timeout") == (True, 2)
    assert suppressor.allow("read timeout") == (False, 0)


@pytest.fixture
def captured_lines():
    """A loguru sink; pytest's stderr capture cannot see loguru's own handler."""
    from loguru import logger

    lines: list[str] = []
    sink_id = logger.add(lines.append, format="{level} {message}")
    yield lines
    logger.remove(sink_id)


def test_handler_emits_one_line_per_burst(captured_lines):
    clock = ManualClock()
    handler = TelebotHandler(RepeatSuppressor(60.0, clock=clock))
    record = logging.LogRecord(
        "TeleBot", logging.ERROR, __file__, 1, READ_TIMEOUT, (), None
    )
    traceback_record = logging.LogRecord(
        "TeleBot", logging.ERROR, __file__, 1, "Exception traceback:\n...", (), None
    )

    for _ in range(5):
        handler.emit(record)
        handler.emit(traceback_record)

    assert len(captured_lines) == 1
    assert "reconnecting to Telegram (read timeout)" in captured_lines[0]
    assert "WARNING" in captured_lines[0]
    assert "Traceback" not in captured_lines[0]


def test_after_the_interval_the_line_says_how_many_were_swallowed(captured_lines):
    clock = ManualClock()
    handler = TelebotHandler(RepeatSuppressor(60.0, clock=clock))
    record = logging.LogRecord(
        "TeleBot", logging.ERROR, __file__, 1, READ_TIMEOUT, (), None
    )

    for _ in range(4):
        handler.emit(record)
    clock.now = 61.0
    handler.emit(record)

    assert len(captured_lines) == 2
    assert "3 more since the last line" in captured_lines[1]


def test_a_genuine_error_is_never_suppressed(captured_lines):
    handler = TelebotHandler()
    for index in range(3):
        handler.emit(
            logging.LogRecord(
                "TeleBot", logging.ERROR, __file__, 1, f"Bot token invalid {index}", (), None
            )
        )
    assert len(captured_lines) == 3
    assert all("ERROR" in line for line in captured_lines)


def test_configure_replaces_telebot_handlers():
    telebot_logger = logging.getLogger("TeleBot")
    telebot_logger.addHandler(logging.StreamHandler())
    configure_telebot_logging()
    assert len(telebot_logger.handlers) == 1
    assert isinstance(telebot_logger.handlers[0], TelebotHandler)
    assert telebot_logger.propagate is False
