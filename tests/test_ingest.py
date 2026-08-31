import io

import pytest

from tgmusicbot.events import Done, Question
from tgmusicbot.ingest import IngestService
from tgmusicbot.jobs import JobRegistry, JobState
from tgmusicbot.library import INCOMING_DIR, MediaLibrary
from tgmusicbot.tags import TrackTags


def staged(root):
    """What is left in staging; the directory may not exist at all."""
    incoming = root / INCOMING_DIR
    return sorted(path.name for path in incoming.iterdir()) if incoming.is_dir() else []


@pytest.fixture
def service(tmp_path):
    registry = JobRegistry()
    library = MediaLibrary(tmp_path)
    return IngestService(library, registry), registry, tmp_path


def start(service_bundle, filename, hint=None, payload=b"bytes"):
    service, registry, _ = service_bundle
    job = registry.create("ingest", 1, 1)
    return job, service.start(job, io.BytesIO(payload), filename, hint)


def test_full_metadata_files_the_track_immediately(service, tmp_path):
    svc, registry, root = service
    job = registry.create("ingest", 1, 1)
    event = svc.start(
        job,
        io.BytesIO(b"bytes"),
        "05 - Time.mp3",
        TrackTags(artist="Pink Floyd", album="Meddle"),
    )
    assert isinstance(event, Done)
    assert event.key == "ingest.created"
    assert event.params["path"].endswith("Pink Floyd/Meddle/05 - Time.mp3")
    assert job.state is JobState.DONE


def test_missing_album_asks_instead_of_failing(service, tmp_path):
    job, event = start(service, "Pink Floyd - Time.mp3")
    assert isinstance(event, Question)
    assert event.free_text is True
    assert [o.key for o in event.options] == ["ask.album.singles", "ask.cancel"]
    assert job.state is JobState.WAITING
    # bytes are safe on disk while we wait, and invisible to a scanner
    assert [name.endswith(".mp3") for name in staged(tmp_path)] == [True]  # real suffix, for mutagen
    assert not (tmp_path / "Pink Floyd").exists()


def test_resume_with_a_typed_album(service, tmp_path):
    svc, _, root = service
    job, _ = start(service, "Pink Floyd - Time.mp3")
    event = svc.resume(job, "Meddle")
    assert event.key == "ingest.created"
    assert (root / "Pink Floyd" / "Meddle" / "Time.mp3").exists()
    assert staged(root) == []


def test_singles_option(service, tmp_path):
    svc, _, root = service
    job, question = start(service, "Pink Floyd - Time.mp3")
    event = svc.answer_option(job, question.options[0])
    assert (root / "Pink Floyd" / "Singles" / "Time.mp3").exists()
    assert event.key == "ingest.created"


def test_cancel_discards_the_staged_bytes(service, tmp_path):
    svc, _, root = service
    job, question = start(service, "Pink Floyd - Time.mp3")
    event = svc.answer_option(job, question.options[1])
    assert event.key == "ask.cancelled"
    assert staged(root) == []
    assert not (root / "Pink Floyd").exists()
    assert job.state is JobState.DONE


def test_duplicate_reports_duplicate(service):
    svc, registry, _ = service
    hint = TrackTags(artist="Pink Floyd", album="Meddle")
    first = svc.start(registry.create("i", 1, 1), io.BytesIO(b"b"), "Time.mp3", hint)
    second = svc.start(registry.create("i", 1, 1), io.BytesIO(b"b"), "Time.mp3", hint)
    assert first.key == "ingest.created"
    assert second.key == "ingest.duplicate"
    assert second.params["path"] == first.params["path"]


def test_hint_loses_to_the_files_own_tags_but_beats_the_filename(service):
    """Telegram's ``performer`` is a fallback, not the truth."""
    svc, registry, _ = service
    job = registry.create("i", 1, 1)
    event = svc.start(
        job,
        io.BytesIO(b"b"),
        "Someone Else - Time.mp3",
        TrackTags(artist="Pink Floyd", album="Meddle"),
    )
    assert "Pink Floyd/Meddle" in event.params["path"]


def test_missing_artist_and_album_are_asked_for_in_turn(service, tmp_path):
    """A dead end ("no artist tag") becomes a two-step dialogue instead."""
    svc, _, root = service
    job, first = start(service, "Time.mp3")
    assert isinstance(first, Question) and first.key == "ask.artist"
    assert job.payload["awaiting"] == "artist"

    second = svc.resume(job, "Pink Floyd")
    assert isinstance(second, Question) and second.key == "ask.album"
    assert job.payload["awaiting"] == "album"

    done = svc.resume(job, "Meddle")
    assert done.key == "ingest.created"
    assert (root / "Pink Floyd" / "Meddle" / "Time.mp3").exists()


def test_cancel_midway_through_the_chain(service, tmp_path):
    svc, _, root = service
    job, question = start(service, "Time.mp3")
    event = svc.answer_option(job, question.options[-1])
    assert event.key == "ask.cancelled"
    assert staged(root) == []


def test_unsupported_extension_stages_nothing(service, tmp_path):
    svc, registry, root = service
    from tgmusicbot.errors import UnsupportedFormat

    with pytest.raises(UnsupportedFormat):
        svc.start(registry.create("i", 1, 1), io.BytesIO(b"b"), "notes.txt")
    assert not (root / INCOMING_DIR).exists()


# -- tags are written into the file, not just into the path -------------------


def make_mp3(payload=b""):
    """A bare MPEG-1 Layer III stream: enough for mutagen to attach ID3 to."""
    return io.BytesIO((b"\xff\xfb\x90\x00" + b"\x00" * 413) * 20 + payload)


def test_a_file_that_arrives_untagged_gets_tagged(service, tmp_path):
    """A player reads tags, not paths — untagged files show as Unknown Artist."""
    from tgmusicbot.tags import read_tags

    svc, registry, root = service
    job = registry.create("i", 1, 1)
    event = svc.start(
        job, make_mp3(), "Time.mp3", TrackTags(artist="Pink Floyd", album="Meddle")
    )

    written = read_tags(root / "Pink Floyd" / "Meddle" / "Time.mp3")
    assert written.artist == "Pink Floyd"
    assert written.album == "Meddle"
    assert written.title == "Time"
    assert event.key == "ingest.created"


def test_existing_tags_are_never_overwritten(service, tmp_path):
    from tgmusicbot.tags import read_tags, write_tags

    svc, registry, root = service
    source = tmp_path / "source.mp3"
    source.write_bytes(make_mp3().getvalue())
    write_tags(source, TrackTags(artist="Pink Floyd", title="Echoes"))

    job = registry.create("i", 1, 1)
    with source.open("rb") as handle:
        svc.start(job, handle, "source.mp3", TrackTags(album="Meddle"))

    stored = read_tags(root / "Pink Floyd" / "Meddle" / "Echoes.mp3")
    assert stored.title == "Echoes"  # the file's own title survived
    assert stored.album == "Meddle"  # only the gap was filled


def test_prefer_hint_overrides_what_the_file_claims(service, tmp_path):
    """`/dl` metadata is what the user typed; YouTube's is not to be trusted."""
    from tgmusicbot.tags import read_tags, write_tags

    svc, registry, root = service
    source = tmp_path / "yt.mp3"
    source.write_bytes(make_mp3().getvalue())
    write_tags(source, TrackTags(artist="Topic", title="Provided to YouTube by"))

    job = registry.create("dl", 1, 1)
    with source.open("rb") as handle:
        svc.start(
            job,
            handle,
            "yt.mp3",
            TrackTags(artist="Pink Floyd", album="Meddle", title="Echoes"),
            prefer_hint=True,
        )

    stored = read_tags(root / "Pink Floyd" / "Meddle" / "Echoes.mp3")
    assert (stored.artist, stored.title) == ("Pink Floyd", "Echoes")


def test_the_same_download_twice_is_still_a_duplicate(service):
    """Tagging changes the bytes, so the hash must be taken after tagging."""
    svc, registry, _ = service
    hint = TrackTags(artist="Pink Floyd", album="Meddle", title="Echoes")

    first = svc.start(registry.create("dl", 1, 1), make_mp3(), "a.mp3", hint, prefer_hint=True)
    second = svc.start(registry.create("dl", 1, 1), make_mp3(), "a.mp3", hint, prefer_hint=True)

    assert first.key == "ingest.created"
    assert second.key == "ingest.duplicate"
    assert second.params["path"] == first.params["path"]


def test_an_unwritable_tag_does_not_lose_the_file(service, tmp_path):
    def explode(path, tags):
        raise OSError("read-only file system")

    from tgmusicbot.ingest import IngestService

    registry = JobRegistry()
    svc = IngestService(MediaLibrary(tmp_path), registry, tag_writer=explode)
    event = svc.start(
        registry.create("i", 1, 1),
        make_mp3(),
        "Time.mp3",
        TrackTags(artist="Pink Floyd", album="Meddle"),
    )
    assert event.key == "ingest.created"
    assert (tmp_path / "Pink Floyd" / "Meddle" / "Time.mp3").exists()


def test_the_topic_suffix_is_dropped_from_an_uploaded_files_own_tags(service, tmp_path):
    """Files ripped from a YouTube Topic channel carry it in their tags."""
    from tgmusicbot.tags import write_tags

    svc, registry, root = service
    source = tmp_path / "src.mp3"
    source.write_bytes(make_mp3().getvalue())
    write_tags(source, TrackTags(artist="P!nk - Topic", album="M!ssundaztood", title="Get the Party Started"))

    with source.open("rb") as handle:
        svc.start(registry.create("i", 1, 1), handle, "src.mp3")

    assert (root / "P!nk" / "M!ssundaztood" / "Get the Party Started.mp3").exists()
    assert not (root / "P!nk - Topic").exists()
