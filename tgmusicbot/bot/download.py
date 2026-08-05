"""Streaming a file out of the Bot API.

``TeleBot.download_file`` returns the whole file as ``bytes`` and reports
nothing while it does so — which is why the progress message could only ever
say ``?%``. Reading the response ourselves gives both a real percentage and a
file that never exists in memory all at once.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import BinaryIO

import requests

from ..errors import SourceUnavailable

FILE_URL = "https://api.telegram.org/file/bot{token}/{path}"
CONNECT_TIMEOUT_S = 10.0
READ_TIMEOUT_S = 60.0


class ProgressStream:
    """A read-only file object that reports how far it has got.

    ``MediaLibrary.stage`` pulls it a chunk at a time, so the callback fires
    naturally as the download proceeds; throttling is the caller's business.
    """

    def __init__(
        self,
        inner: BinaryIO,
        total: int | None,
        on_progress: Callable[[int, int | None], None],
    ):
        self._inner = inner
        self._total = total
        self._on_progress = on_progress
        self._done = 0

    def read(self, size: int = -1) -> bytes:
        chunk = self._inner.read(size)
        if chunk:
            self._done += len(chunk)
            self._on_progress(self._done, self._total)
        return chunk

    @property
    def done(self) -> int:
        return self._done


def open_telegram_file(
    token: str,
    file_path: str,
    on_progress: Callable[[int, int | None], None],
    *,
    expected_size: int | None = None,
    session: requests.Session | None = None,
):
    """Open a Bot API file for streaming. Caller closes the response."""
    url = FILE_URL.format(token=token, path=file_path)
    getter = session.get if session else requests.get
    try:
        response = getter(
            url, stream=True, timeout=(CONNECT_TIMEOUT_S, READ_TIMEOUT_S)
        )
        response.raise_for_status()
    except requests.RequestException as error:
        raise SourceUnavailable("Telegram", _reason(error)) from error

    total = expected_size
    declared = response.headers.get("Content-Length")
    if declared and declared.isdigit():
        total = int(declared)

    response.raw.decode_content = True
    return response, ProgressStream(response.raw, total, on_progress)


def _reason(error: Exception) -> str:
    text = str(error)
    return text.split("\n", 1)[0][:120] or type(error).__name__
