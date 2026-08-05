"""Structured events emitted by the core.

Progress is an event, not a string.  The core reports *what happened*; the bot
layer decides how often to redraw a message and in which language.  Without
this split the >=3 s Telegram throttle would end up duplicated in the yt-dlp
hook, the qBittorrent poller and every future source.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Option:
    """One answer to a :class:`Question`.

    ``key`` is a text-catalogue key, not a rendered label — ``index`` is what
    travels back in ``callback_data``.
    """

    index: int
    key: str
    params: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Progress:
    job_id: str
    stage: str
    done: int = 0
    total: int | None = None

    @property
    def fraction(self) -> float | None:
        if not self.total:
            return None
        return min(1.0, self.done / self.total)


@dataclass(frozen=True, slots=True)
class Question:
    job_id: str
    key: str
    options: tuple[Option, ...]
    params: dict[str, object] = field(default_factory=dict)
    free_text: bool = False
    """True when the user may also reply with a plain message (e.g. album name)."""


@dataclass(frozen=True, slots=True)
class Done:
    job_id: str
    key: str
    params: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Failed:
    job_id: str
    code: str
    params: dict[str, object] = field(default_factory=dict)


Event = Progress | Question | Done | Failed
