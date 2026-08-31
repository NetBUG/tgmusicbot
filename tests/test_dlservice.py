import pytest

from tgmusicbot.dlservice import DownloadService, parse_query
from tgmusicbot.errors import SourceUnavailable
from tgmusicbot.events import Done, Progress, Question
from tgmusicbot.ingest import CANCEL, IngestService
from tgmusicbot.jobs import JobRegistry, JobState
from tgmusicbot.library import INCOMING_DIR, MediaLibrary
from tgmusicbot.sources.base import Candidate, Fetched
from tgmusicbot.tags import TrackTags


@pytest.mark.parametrize(
    "query,expected",
    [
        ("Pink Floyd - Meddle - One of These Days",
         TrackTags(artist="Pink Floyd", album="Meddle", title="One of These Days")),
        ("Pink Floyd - Time", TrackTags(artist="Pink Floyd", title="Time")),
        ("Time", TrackTags(title="Time")),
        ("Pink Floyd – Meddle – Time",  # en dash
         TrackTags(artist="Pink Floyd", album="Meddle", title="Time")),
        ("A - B - C - D", TrackTags(artist="A", album="B", title="C - D")),
        ("", TrackTags()),
    ],
)
def test_parse_query(query, expected):
    assert parse_query(query) == expected


def candidate(index=0):
    return Candidate(
        id=f"id{index}",
        title=f"Track {index}",
        url=f"https://youtu.be/id{index}",
        source="youtube",
        uploader="Channel",
        duration_s=200,
    )


class FakeSource:
    name = "youtube"

    def __init__(self, candidates=None, error=None, payload=b"audio", suffix=".m4a"):
        self.candidates = candidates if candidates is not None else [candidate(0), candidate(1)]
        self.error = error
        self.payload = payload
        self.suffix = suffix
        self.fetched: list[Candidate] = []
        self.destinations: list = []

    def search(self, query, limit=5):
        if self.error:
            raise self.error
        return self.candidates[:limit]

    def fetch(self, cand, destination, on_progress=None):
        self.fetched.append(cand)
        self.destinations.append(destination)
        destination.mkdir(parents=True, exist_ok=True)
        path = destination / f"{cand.id}{self.suffix}"
        path.write_bytes(self.payload)
        if on_progress:
            on_progress(len(self.payload), len(self.payload))
        return Fetched(path=path, candidate=cand)


@pytest.fixture
def bundle(tmp_path):
    registry = JobRegistry()
    library = MediaLibrary(tmp_path)
    ingest = IngestService(library, registry)
    source = FakeSource()
    service = DownloadService(library, registry, ingest, source, limit=5)
    return service, registry, source, tmp_path


def test_search_offers_the_results_plus_cancel(bundle):
    service, registry, _, _ = bundle
    job = registry.create("dl", 1, 1)
    event = service.start(job, "Pink Floyd - Meddle - Time")

    assert isinstance(event, Question)
    assert event.key == "ask.dl.choose"
    assert [option.key for option in event.options] == [
        "ask.dl.candidate",
        "ask.dl.candidate",
        "ask.cancel",
    ]
    assert event.options[0].params == {"index": 1, "title": "Track 0"}
    assert job.state is JobState.WAITING


def test_nothing_found_finishes_the_job(bundle):
    service, registry, source, _ = bundle
    source.candidates = []
    job = registry.create("dl", 1, 1)
    event = service.start(job, "unlikely query")

    assert isinstance(event, Done)
    assert event.key == "dl.nothing_found"
    assert event.params["query"] == "unlikely query"
    assert job.state is JobState.DONE


def test_choosing_a_candidate_fetches_and_files_it(bundle):
    service, registry, source, root = bundle
    job = registry.create("dl", 1, 1)
    question = service.start(job, "Pink Floyd - Meddle - Time")

    event = service.answer_option(job, question.options[1])

    assert source.fetched == [candidate(1)]
    assert isinstance(event, Done)
    assert event.key == "ingest.created"
    assert (root / "Pink Floyd" / "Meddle" / "Time.m4a").exists()


def test_the_query_beats_whatever_youtube_called_the_file(bundle):
    """A YouTube title is not a tag; what the user typed wins."""
    service, registry, source, root = bundle
    job = registry.create("dl", 1, 1)
    question = service.start(job, "Pink Floyd - Meddle - Time")
    service.answer_option(job, question.options[0])
    assert (root / "Pink Floyd" / "Meddle" / "Time.m4a").exists()
    assert not (root / "Channel").exists()


def test_a_two_part_query_still_asks_for_the_album(bundle):
    service, registry, _, root = bundle
    job = registry.create("dl", 1, 1)
    question = service.start(job, "Pink Floyd - Time")

    event = service.answer_option(job, question.options[0])
    assert isinstance(event, Question)
    assert event.key == "ask.album"
    assert job.payload["awaiting"] == "album"

    done = service.answer_option(job, event.options[0])  # "Singles"
    assert done.key == "ingest.created"
    assert (root / "Pink Floyd" / "Singles" / "Time.m4a").exists()


def test_progress_is_reported_while_fetching(bundle):
    service, registry, _, _ = bundle
    job = registry.create("dl", 1, 1)
    question = service.start(job, "A - B - C")

    seen = []
    service.answer_option(job, question.options[0], seen.append)
    stages = [event.stage for event in seen if isinstance(event, Progress)]
    assert stages == ["stage.fetch", "stage.fetch"]
    assert seen[-1].done == len(b"audio")


def test_cancel_before_fetching_downloads_nothing(bundle):
    service, registry, source, root = bundle
    job = registry.create("dl", 1, 1)
    service.start(job, "A - B - C")

    event = service.answer_option(job, CANCEL)
    assert event.key == "ask.cancelled"
    assert source.fetched == []
    assert job.state is JobState.DONE


def test_the_working_directory_is_always_cleaned_up(bundle):
    service, registry, source, root = bundle
    job = registry.create("dl", 1, 1)
    question = service.start(job, "A - B - C")
    service.answer_option(job, question.options[0])

    assert source.destinations[0] == root / INCOMING_DIR / f"dl-{job.id}"
    assert not source.destinations[0].exists()


def test_a_failed_fetch_cleans_up_and_raises(bundle):
    service, registry, source, root = bundle

    def explode(cand, destination, on_progress=None):
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "partial.part").write_bytes(b"half")
        raise SourceUnavailable("youtube", "403 Forbidden")

    source.fetch = explode
    job = registry.create("dl", 1, 1)
    question = service.start(job, "A - B - C")

    with pytest.raises(SourceUnavailable):
        service.answer_option(job, question.options[0])
    assert not (root / INCOMING_DIR / f"dl-{job.id}").exists()
    assert job.state is JobState.FAILED


def test_a_format_the_library_refuses_is_a_typed_error(bundle):
    from tgmusicbot.errors import UnsupportedFormat

    service, registry, source, _ = bundle
    source.suffix = ".webm"
    job = registry.create("dl", 1, 1)
    question = service.start(job, "A - B - C")
    with pytest.raises(UnsupportedFormat):
        service.answer_option(job, question.options[0])


def test_a_stale_button_index_does_not_crash(bundle):
    service, registry, _, _ = bundle
    job = registry.create("dl", 1, 1)
    service.start(job, "A - B - C")
    from tgmusicbot.events import Option

    event = service.answer_option(job, Option(99, "ask.dl.candidate"))
    assert event.key == "ask.cancelled"
