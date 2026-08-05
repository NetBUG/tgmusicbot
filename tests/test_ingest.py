import io

import pytest

from tgmusicbot.events import Done, Question
from tgmusicbot.ingest import IngestService
from tgmusicbot.jobs import JobRegistry, JobState
from tgmusicbot.library import INCOMING_DIR, MediaLibrary
from tgmusicbot.tags import TrackTags


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
    assert list((tmp_path / INCOMING_DIR).glob("*.part"))
    assert not (tmp_path / "Pink Floyd").exists()


def test_resume_with_a_typed_album(service, tmp_path):
    svc, _, root = service
    job, _ = start(service, "Pink Floyd - Time.mp3")
    event = svc.resume(job, "Meddle")
    assert event.key == "ingest.created"
    assert (root / "Pink Floyd" / "Meddle" / "Time.mp3").exists()
    assert list((root / INCOMING_DIR).glob("*.part")) == []


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
    assert list((root / INCOMING_DIR).glob("*.part")) == []
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
    assert list((root / INCOMING_DIR).glob("*.part")) == []


def test_unsupported_extension_stages_nothing(service, tmp_path):
    svc, registry, root = service
    from tgmusicbot.errors import UnsupportedFormat

    with pytest.raises(UnsupportedFormat):
        svc.start(registry.create("i", 1, 1), io.BytesIO(b"b"), "notes.txt")
    assert not (root / INCOMING_DIR).exists()
