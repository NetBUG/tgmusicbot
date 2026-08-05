import io

import pytest
import requests

from tgmusicbot.bot.download import ProgressStream, open_telegram_file
from tgmusicbot.errors import SourceUnavailable


def test_progress_stream_reports_as_it_is_read():
    seen = []
    stream = ProgressStream(io.BytesIO(b"x" * 10), 10, lambda d, t: seen.append((d, t)))

    assert stream.read(4) == b"x" * 4
    assert stream.read(4) == b"x" * 4
    assert stream.read(4) == b"x" * 2
    assert stream.read(4) == b""  # exhausted: no extra callback

    assert seen == [(4, 10), (8, 10), (10, 10)]
    assert stream.done == 10


def test_progress_stream_survives_an_unknown_total():
    seen = []
    stream = ProgressStream(io.BytesIO(b"abc"), None, lambda d, t: seen.append((d, t)))
    stream.read(-1)
    assert seen == [(3, None)]


class FakeResponse:
    def __init__(self, body=b"data", headers=None, error=None):
        self.raw = io.BytesIO(body)
        self.headers = headers or {}
        self._error = error
        self.closed = False

    def raise_for_status(self):
        if self._error:
            raise self._error

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.url = None
        self.kwargs = None

    def get(self, url, **kwargs):
        self.url = url
        self.kwargs = kwargs
        return self.response


def test_open_builds_the_file_url_and_streams():
    session = FakeSession(FakeResponse(b"payload", {"Content-Length": "7"}))
    response, stream = open_telegram_file(
        "123:secret", "music/file_1.mp3", lambda d, t: None, session=session
    )

    assert session.url == "https://api.telegram.org/file/bot123:secret/music/file_1.mp3"
    assert session.kwargs["stream"] is True
    assert stream.read(-1) == b"payload"
    assert response is session.response


def test_content_length_wins_over_the_size_telegram_announced():
    seen = []
    session = FakeSession(FakeResponse(b"12345", {"Content-Length": "5"}))
    _, stream = open_telegram_file(
        "t", "p", lambda d, t: seen.append(t), expected_size=999, session=session
    )
    stream.read(-1)
    assert seen == [5]


def test_missing_content_length_falls_back_to_the_announced_size():
    seen = []
    session = FakeSession(FakeResponse(b"12345"))
    _, stream = open_telegram_file(
        "t", "p", lambda d, t: seen.append(t), expected_size=5, session=session
    )
    stream.read(-1)
    assert seen == [5]


def test_a_network_failure_becomes_a_typed_error():
    session = FakeSession(
        FakeResponse(error=requests.HTTPError("404 Client Error: Not Found"))
    )
    with pytest.raises(SourceUnavailable) as raised:
        open_telegram_file("t", "p", lambda d, t: None, session=session)
    assert raised.value.source == "Telegram"
    assert "404" in raised.value.reason
    assert raised.value.code == "error.source_unavailable"
