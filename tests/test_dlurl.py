"""The link flow: a YouTube URL sent on its own behaves like a sent file."""

import pytest

from tgmusicbot.dlservice import (
    DEFAULT_ALBUM,
    DownloadService,
    parse_target,
    tags_from_candidate,
)
from tgmusicbot.errors import SourceUnavailable
from tgmusicbot.events import Done, Question
from tgmusicbot.ingest import CANCEL, IngestService
from tgmusicbot.jobs import JobRegistry, JobState
from tgmusicbot.library import INCOMING_DIR, MediaLibrary
from tgmusicbot.naming import clean_video_title
from tgmusicbot.sources.base import Candidate, Fetched
from tgmusicbot.sources.youtube import find_url
from tgmusicbot.tags import TrackTags

URL = "https://www.youtube.com/watch?v=Q8WJz-DmPVg"


# -- URL recognition ------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        URL,
        "https://youtu.be/Q8WJz-DmPVg",
        "https://m.youtube.com/watch?v=Q8WJz-DmPVg",
        "https://music.youtube.com/watch?v=Q8WJz-DmPVg&list=RDQ8WJz-DmPVg",
        "https://www.youtube.com/watch?v=Q8WJz-DmPVg&t=42s",
        "https://www.youtube.com/shorts/Q8WJz-DmPVg",
        "https://www.youtube.com/live/Q8WJz-DmPVg",
        "look at this https://youtu.be/Q8WJz-DmPVg it's great",
        "https://www.youtube.com/watch?app=desktop&v=Q8WJz-DmPVg",
    ],
)
def test_links_are_recognised_and_normalised(text):
    """Playlist and timestamp parameters must go, or yt-dlp grabs the playlist."""
    assert find_url(text) == URL


@pytest.mark.parametrize(
    "text",
    [None, "", "Pink Floyd - Time", "https://example.com/watch?v=Q8WJz-DmPVg",
     "youtube.com/watch?v=short", "https://vimeo.com/12345678"],
)
def test_non_links_are_not_mistaken_for_links(text):
    assert find_url(text) is None


# -- title parsing --------------------------------------------------------


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Pink Floyd - Time (Official Audio)", "Pink Floyd - Time"),
        ("Pink Floyd - Time [HD]", "Pink Floyd - Time"),
        ("Time (Official Music Video) [4K]", "Time"),
        ("Time (Remastered 2011)", "Time"),
        ("Pink Floyd - Time - Official Video", "Pink Floyd - Time"),
        ("Aphex Twin - #3 (Rhubarb)", "Aphex Twin - #3 (Rhubarb)"),  # not noise
        ("(I Can't Get No) Satisfaction", "(I Can't Get No) Satisfaction"),
        ("МакSим - Знаешь ли ты (официальный клип)", "МакSим - Знаешь ли ты"),
        ("Кино - Группа крови (Официальное видео)", "Кино - Группа крови"),
        ("Ария - Штиль (премьера клипа, 2002)", "Ария - Штиль"),
        ("Земфира - Искала (аудио)", "Земфира - Искала"),
        ("Сплин - Выхода нет (текст песни)", "Сплин - Выхода нет"),
        ("Ленинград - Экспонат (Official Video) [4K]", "Ленинград - Экспонат"),
        ("Аквариум - Город золотой", "Аквариум - Город золотой"),
        ("Наутилус - Клипы 90-х", "Наутилус - Клипы 90-х"),
        # a run of qualifiers in one bracket, seen live
        ("Jebroer - Kind Eines Teufels (Official Video HD)",
         "Jebroer - Kind Eines Teufels"),
        ("X - Y (Prod. by Paul Elstak) (Official Video HD)", "X - Y (Prod. by Paul Elstak)"),
        ("X - Y (Official Music Video 4K)", "X - Y"),
        ("X - Y (Lyric Video, 2019)", "X - Y"),
        ("X - Y - Official Video HD", "X - Y"),
        ("X - Y (Album Version)", "X - Y (Album Version)"),  # not decoration
        ("X - Y (Radio Edit)", "X - Y (Radio Edit)"),
    ],
)
def test_clean_video_title(title, expected):
    assert clean_video_title(title) == expected


def candidate(title="Pink Floyd - Meddle - Echoes", **kwargs):
    base = dict(
        id="Q8WJz-DmPVg",
        title=title,
        url=URL,
        source="youtube",
        uploader="Pink Floyd",
        duration_s=400,
        extension=".m4a",
    )
    return Candidate(**(base | kwargs))


@pytest.mark.parametrize(
    "title,uploader,expected",
    [
        ("Pink Floyd - Meddle - Echoes", "PF", TrackTags("Pink Floyd", "Meddle", "Echoes")),
        ("Pink Floyd - Echoes", "PF", TrackTags("Pink Floyd", None, "Echoes")),
        ("Echoes", "Pink Floyd", TrackTags("Pink Floyd", None, "Echoes")),
        ("Echoes", "Pink Floyd - Topic", TrackTags("Pink Floyd", None, "Echoes")),
        ("Echoes", "Pink Floyd – Topic", TrackTags("Pink Floyd", None, "Echoes")),
        ("Echoes", "P!nk - Topic ", TrackTags("P!nk", None, "Echoes")),
        ("Echoes (Official Audio)", "Pink Floyd", TrackTags("Pink Floyd", None, "Echoes")),
    ],
)
def test_tags_from_candidate(title, uploader, expected):
    assert tags_from_candidate(candidate(title, uploader=uploader)) == expected


# -- the flow -------------------------------------------------------------


class FakeSource:
    name = "youtube"

    def __init__(self, cand=None, inspect_error=None):
        self.candidate = cand or candidate()
        self.inspect_error = inspect_error
        self.inspected: list[str] = []
        self.fetched: list[Candidate] = []

    def inspect(self, url):
        self.inspected.append(url)
        if self.inspect_error:
            raise self.inspect_error
        return self.candidate

    def search(self, query, limit=5):
        return [self.candidate]

    def fetch(self, cand, destination, on_progress=None):
        self.fetched.append(cand)
        destination.mkdir(parents=True, exist_ok=True)
        path = destination / f"{cand.id}{cand.extension or '.m4a'}"
        path.write_bytes(b"audio")
        return Fetched(path=path, candidate=cand)


@pytest.fixture
def bundle(tmp_path):
    registry = JobRegistry()
    library = MediaLibrary(tmp_path)
    source = FakeSource()
    service = DownloadService(
        library, registry, IngestService(library, registry), source
    )
    return service, registry, source, tmp_path


def test_a_link_proposes_a_path_and_asks_first(bundle):
    service, registry, source, _ = bundle
    job = registry.create("dl", 1, 1)
    event = service.start_url(job, URL)

    assert source.inspected == [URL]
    assert source.fetched == []  # nothing downloaded before the user agrees
    assert isinstance(event, Question)
    assert event.key == "ask.dl.confirm"
    assert event.params["path"] == "Pink Floyd/Meddle/Echoes.m4a"
    assert [option.key for option in event.options] == [
        "ask.dl.save",
        "ask.dl.specify",
        "ask.cancel",
    ]
    assert event.free_text is True
    assert job.state is JobState.WAITING


def test_yes_downloads_and_files_it(bundle):
    service, registry, source, root = bundle
    job = registry.create("dl", 1, 1)
    question = service.start_url(job, URL)

    event = service.answer_option(job, question.options[0])
    assert isinstance(event, Done)
    assert event.key == "ingest.created"
    assert (root / "Pink Floyd" / "Meddle" / "Echoes.m4a").exists()
    assert source.fetched == [source.candidate]


def test_no_downloads_nothing(bundle):
    service, registry, source, root = bundle
    job = registry.create("dl", 1, 1)
    service.start_url(job, URL)

    event = service.answer_option(job, CANCEL)
    assert event.key == "ask.cancelled"
    assert source.fetched == []
    assert list(root.iterdir()) == []
    assert job.state is JobState.DONE


def test_a_title_without_an_album_is_proposed_as_singles(bundle):
    service, registry, source, root = bundle
    source.candidate = candidate("Pink Floyd - Echoes")
    job = registry.create("dl", 1, 1)
    event = service.start_url(job, URL)
    assert event.params["path"] == f"Pink Floyd/{DEFAULT_ALBUM}/Echoes.m4a"


def test_specify_path_then_save(bundle):
    service, registry, source, root = bundle
    job = registry.create("dl", 1, 1)
    question = service.start_url(job, URL)

    asked = service.answer_option(job, question.options[1])
    assert isinstance(asked, Question)
    assert asked.key == "ask.dl.specify_path"
    assert asked.free_text is True
    assert source.fetched == []

    done = service.resume(job, "Pink Floyd/Obscured by Clouds/Burning Bridges")
    assert done.key == "ingest.created"
    assert (
        root / "Pink Floyd" / "Obscured by Clouds" / "Burning Bridges.m4a"
    ).exists()


@pytest.mark.parametrize(
    "answer,expected",
    [
        ("A/B/C", TrackTags("A", "B", "C")),
        ("A/B/C.m4a", TrackTags("A", "B", "C")),
        ("A/B", TrackTags("A", "B", "Echoes")),
        ("Meddle", TrackTags("Pink Floyd", "Meddle", "Echoes")),
        ("A - B - C", TrackTags("A", "B", "C")),
        ("/A/B/C/", TrackTags("A", "B", "C")),
        ("", TrackTags("Pink Floyd", "Meddle", "Echoes")),
    ],
)
def test_parse_target(answer, expected):
    current = TrackTags("Pink Floyd", "Meddle", "Echoes")
    assert parse_target(answer, current) == expected


def test_a_typed_path_cannot_escape_the_library(bundle):
    from tgmusicbot.errors import UnsafeName

    service, registry, _, root = bundle
    job = registry.create("dl", 1, 1)
    question = service.start_url(job, URL)
    service.answer_option(job, question.options[1])

    with pytest.raises(UnsafeName):
        service.resume(job, "../../etc/passwd")
    assert list((root / INCOMING_DIR).iterdir()) == []


def test_an_unplayable_link_fails_before_anything_is_written(bundle):
    service, registry, source, root = bundle
    source.inspect_error = SourceUnavailable("youtube", "Video unavailable")
    job = registry.create("dl", 1, 1)
    with pytest.raises(SourceUnavailable):
        service.start_url(job, URL)
    assert list(root.iterdir()) == []


def test_the_preview_path_survives_missing_metadata(bundle):
    """A one-word title with no channel leaves nothing to build a path from."""
    service, registry, source, _ = bundle
    source.candidate = candidate("Echoes", uploader=None)
    job = registry.create("dl", 1, 1)
    event = service.start_url(job, URL)
    assert event.params["path"] == "?"


def test_the_extension_comes_from_the_format_that_would_be_downloaded(bundle):
    service, registry, source, _ = bundle
    source.candidate = candidate(extension=".ogg")
    job = registry.create("dl", 1, 1)
    assert service.start_url(job, URL).params["path"].endswith(".ogg")


def test_an_unknown_extension_is_left_out_of_the_preview(bundle):
    service, registry, source, _ = bundle
    source.candidate = candidate(extension=None)
    job = registry.create("dl", 1, 1)
    assert service.start_url(job, URL).params["path"] == "Pink Floyd/Meddle/Echoes"
