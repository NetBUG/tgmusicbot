import pytest

from tgmusicbot.errors import SourceUnavailable
from tgmusicbot.sources.youtube import (
    FORMAT_NO_FFMPEG,
    FORMAT_WITH_FFMPEG,
    REMUX,
    YouTubeSource,
)


def entry(**overrides):
    base = {
        "id": "abc123",
        "title": "Pink Floyd - Time",
        "duration": 400,
        "uploader": "Pink Floyd",
        "url": "https://www.youtube.com/watch?v=abc123",
    }
    return base | overrides


class FakeYdl:
    """Records the options it was built with and what was asked of it."""

    instances: list["FakeYdl"] = []

    def __init__(self, options, entries=None, error=None, on_download=None):
        self.options = options
        self.entries = entries if entries is not None else [entry()]
        self.error = error
        self.on_download = on_download
        self.targets: list[tuple[str, bool]] = []
        FakeYdl.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def extract_info(self, target, download=False):
        self.targets.append((target, download))
        if self.error:
            raise self.error
        if download:
            if self.on_download:
                self.on_download(self)
            return self.entries[0]
        return {"entries": self.entries}


def source(entries=None, error=None, on_download=None, **kwargs):
    FakeYdl.instances = []
    return YouTubeSource(
        ydl_factory=lambda options: FakeYdl(options, entries, error, on_download),
        **kwargs,
    )


# -- search ---------------------------------------------------------------


def test_search_maps_entries_to_candidates():
    found = source().search("Pink Floyd Time", limit=5)
    assert len(found) == 1
    candidate = found[0]
    assert candidate.id == "abc123"
    assert candidate.uploader == "Pink Floyd"
    assert candidate.duration_s == 400
    assert candidate.source == "youtube"


def test_search_asks_for_more_than_it_needs():
    """Filtering throws results away, so the search must over-fetch."""
    youtube = source()
    youtube.search("query", limit=5)
    assert FakeYdl.instances[0].targets == [("ytsearch15:query", False)]


def test_search_stops_at_the_limit():
    entries = [entry(id=f"id{i}", title=f"Track {i}") for i in range(20)]
    assert len(source(entries).search("q", limit=3)) == 3


@pytest.mark.parametrize("status", ["is_live", "is_upcoming", "post_live"])
def test_live_streams_are_dropped(status):
    assert source([entry(live_status=status)]).search("q") == []


def test_live_is_kept_when_the_query_asks_for_it():
    entries = [entry(live_status="is_live")]
    assert len(source(entries).search("Pink Floyd live at Pompeii")) == 1


@pytest.mark.parametrize("duration", [5, 29, 3600])
def test_durations_outside_the_window_are_dropped(duration):
    assert source([entry(duration=duration)]).search("q") == []


def test_a_long_recording_is_allowed_when_live_was_asked_for():
    assert len(source([entry(duration=3600)]).search("q live")) == 1


def test_unknown_duration_is_not_a_reason_to_drop():
    assert len(source([entry(duration=None)]).search("q")) == 1


@pytest.mark.parametrize("word", ["cover", "karaoke", "remix", "кавер"])
def test_covers_and_karaoke_are_dropped_unless_asked_for(word):
    entries = [entry(title=f"Time ({word} version)")]
    assert source(entries).search("Pink Floyd Time") == []
    assert len(source(entries).search(f"Pink Floyd Time {word}")) == 1


def test_entries_without_an_id_or_title_are_skipped():
    assert source([entry(id=None), entry(title=None), {}]).search("q") == []


def test_search_failure_becomes_a_typed_error():
    with pytest.raises(SourceUnavailable) as raised:
        source(error=RuntimeError("ERROR: unable to extract\nsecond line")).search("q")
    assert raised.value.source == "youtube"
    assert raised.value.reason == "unable to extract"


# -- fetch ----------------------------------------------------------------


def written(name):
    def on_download(ydl):
        target = ydl.options["outtmpl"].replace("%(id)s.%(ext)s", name)
        from pathlib import Path

        Path(target).write_bytes(b"audio")

    return on_download


def test_fetch_returns_the_downloaded_file(tmp_path):
    youtube = source(on_download=written("abc123.m4a"))
    candidates = youtube.search("q")
    fetched = youtube.fetch(candidates[0], tmp_path / "work")

    assert fetched.path.name == "abc123.m4a"
    assert fetched.path.read_bytes() == b"audio"
    assert fetched.candidate is candidates[0]


def test_fetch_ignores_leftovers_that_are_not_audio(tmp_path):
    def on_download(ydl):
        from pathlib import Path

        base = Path(ydl.options["outtmpl"]).parent
        (base / "abc123.m4a").write_bytes(b"audio")
        (base / "abc123.info.json").write_bytes(b"{}")
        (base / "abc123.webp").write_bytes(b"thumb")

    youtube = source(on_download=on_download)
    fetched = youtube.fetch(youtube.search("q")[0], tmp_path / "work")
    assert fetched.path.suffix == ".m4a"


def test_fetch_without_a_file_is_an_error_not_a_none(tmp_path):
    youtube = source()
    with pytest.raises(SourceUnavailable, match="nothing was downloaded"):
        youtube.fetch(youtube.search("q")[0], tmp_path / "work")


def test_progress_hook_is_translated_to_bytes(tmp_path):
    seen = []

    def on_download(ydl):
        hook = ydl.options["progress_hooks"][0]
        hook({"status": "downloading", "downloaded_bytes": 10, "total_bytes": 100})
        hook({"status": "downloading", "downloaded_bytes": 50, "total_bytes_estimate": 90.0})
        hook({"status": "finished"})  # ignored
        written("abc123.m4a")(ydl)

    youtube = source(on_download=on_download)
    youtube.fetch(youtube.search("q")[0], tmp_path / "w", lambda d, t: seen.append((d, t)))
    assert seen == [(10, 100), (50, 90)]


def test_no_ffmpeg_means_no_webm_and_no_postprocessing(tmp_path):
    youtube = source(on_download=written("abc123.m4a"))
    youtube.fetch(youtube.search("q")[0], tmp_path / "w")
    options = FakeYdl.instances[-1].options
    assert options["format"] == FORMAT_NO_FFMPEG
    assert "webm" not in options["format"]
    assert "postprocessors" not in options


def test_ffmpeg_enables_a_conditional_remux_only(tmp_path):
    youtube = source(on_download=written("abc123.ogg"), ffmpeg_path="/usr/bin/ffmpeg")
    youtube.fetch(youtube.search("q")[0], tmp_path / "w")
    options = FakeYdl.instances[-1].options

    assert options["format"] == FORMAT_WITH_FFMPEG
    assert options["ffmpeg_location"] == "/usr/bin/ffmpeg"
    # "webm>ogg": an m4a download must not be touched, and nothing is re-encoded
    assert options["postprocessors"] == [
        {"key": "FFmpegVideoRemuxer", "preferedformat": REMUX}
    ]
    assert REMUX == "webm>ogg"


# -- the knobs that exist because YouTube blocks datacentre IPs ---------------


def test_cookies_and_player_clients_reach_every_call(tmp_path):
    youtube = source(
        on_download=written("abc123.m4a"),
        cookies_file="/etc/cookies.txt",
        player_clients=("web_safari", "ios"),
    )
    candidate = youtube.search("q")[0]
    youtube.inspect("https://youtu.be/abc123")
    youtube.fetch(candidate, tmp_path / "w")

    assert len(FakeYdl.instances) == 3
    for instance in FakeYdl.instances:
        assert instance.options["cookiefile"] == "/etc/cookies.txt"
        assert instance.options["extractor_args"] == {
            "youtube": {"player_client": ["web_safari", "ios"]}
        }
        assert instance.options["noprogress"] is True


def test_no_knobs_means_no_extra_options():
    youtube = source()
    youtube.search("q")
    options = FakeYdl.instances[0].options
    assert "cookiefile" not in options
    assert "extractor_args" not in options


def test_inspect_returns_metadata_without_downloading():
    youtube = source([entry(ext="m4a")])
    candidate = youtube.inspect("https://youtu.be/abc123")
    assert candidate.id == "abc123"
    assert candidate.extension == ".m4a"
    assert FakeYdl.instances[0].targets == [("https://youtu.be/abc123", False)]
    assert FakeYdl.instances[0].options["skip_download"] is True


def test_inspect_on_an_empty_result_is_a_typed_error():
    from tgmusicbot.errors import SourceUnavailable

    with pytest.raises(SourceUnavailable, match="no video at that link"):
        source([]).inspect("https://youtu.be/abc123")
