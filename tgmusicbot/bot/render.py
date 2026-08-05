"""Turning core events into Telegram messages. No business logic here."""

from __future__ import annotations

import html
import time
from collections.abc import Callable

from ..events import Done, Event, Failed, Progress, Question
from ..jobs import encode_callback
from . import texts


def escape(value: object) -> str:
    """HTML mode instead of Markdown: paths full of ``_``, ``*`` and ``[`` were
    the source of bug 8, and escaping HTML is total rather than best-effort."""
    return html.escape(str(value), quote=False)


def render(event: Event) -> str:
    if isinstance(event, Progress):
        percent = "?" if event.fraction is None else f"{event.fraction * 100:.0f}"
        return texts.t("ingest.progress", stage=escape(event.stage), percent=percent)
    if isinstance(event, Question):
        return texts.t(event.key, **_escaped(event.params))
    if isinstance(event, Done):
        return texts.t(event.key, **_escaped(event.params))
    if isinstance(event, Failed):
        return texts.t(event.code, **_escaped(event.params))
    raise TypeError(f"unrenderable event: {event!r}")


def keyboard(question: Question):
    """Inline keyboard for a question. Payloads are ids, never text."""
    from telebot import types  # imported here so core tests need no telebot

    markup = types.InlineKeyboardMarkup()
    for option in question.options:
        markup.add(
            types.InlineKeyboardButton(
                texts.t(option.key, **_escaped(option.params)),
                callback_data=encode_callback("ans", question.job_id, option.index),
            )
        )
    return markup


def _escaped(params: dict[str, object]) -> dict[str, str]:
    return {key: escape(value) for key, value in params.items()}


class ProgressThrottle:
    """Telegram rate-limits edits, so progress is redrawn at most every N s.

    Lives in the bot layer precisely so that no source has to know about it.
    """

    def __init__(
        self,
        interval_s: float = 3.0,
        *,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._interval_s = interval_s
        self._clock = clock
        self._last: dict[str, float] = {}

    def should_send(self, job_id: str, *, force: bool = False) -> bool:
        now = self._clock()
        previous = self._last.get(job_id)
        if force or previous is None or now - previous >= self._interval_s:
            self._last[job_id] = now
            return True
        return False

    def forget(self, job_id: str) -> None:
        self._last.pop(job_id, None)
