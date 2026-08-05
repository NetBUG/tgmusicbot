import pytest

from tgmusicbot.errors import NoAudioFiles, PathNotFound, UnsafeName
from tgmusicbot.events import Done, Question
from tgmusicbot.ingest import CANCEL
from tgmusicbot.jobs import JobRegistry, JobState
from tgmusicbot.library import MediaLibrary
from tgmusicbot.tagfix import BACKUP_NAME, Source, TagFixer
from tgmusicbot.tags import TrackTags
from tgmusicbot.tagservice import APPLY, FROM_DIRECTORY, FROM_FILENAMES, TagService

MOJIBAKE = "Пинк Флойд".encode("cp1251").decode("latin-1")


class FakeTags:
    def __init__(self, mapping=None):
        self.mapping = mapping or {}
        self.written = {}

    def read(self, path):
        return self.mapping.get(path.name, TrackTags())

    def write(self, path, tags):
        self.written[path.name] = tags


@pytest.fixture
def bundle(tmp_path):
    album = tmp_path / "Pink Floyd" / "Meddle (1971)"
    album.mkdir(parents=True)
    (album / "01 - One of These Days.flac").write_bytes(b"x")

    fake = FakeTags({"01 - One of These Days.flac": TrackTags(artist=MOJIBAKE)})
    registry = JobRegistry()
    library = MediaLibrary(tmp_path)
    fixer = TagFixer(reader=fake.read, writer=fake.write, clock=lambda: 0.0)
    return TagService(library, registry, fixer), registry, fake, tmp_path


def test_start_proposes_and_asks_without_writing(bundle):
    service, registry, fake, _ = bundle
    job = registry.create("tags", 1, 1)
    event = service.start(job, "Pink Floyd/Meddle (1971)")

    assert isinstance(event, Question)
    assert event.key == "ask.tags.confirm"
    assert [option.key for option in event.options] == [
        "ask.tags.apply",
        "ask.tags.from_directory",
        "ask.tags.from_filenames",
        "ask.cancel",
    ]
    assert event.free_text is False  # a diff is answered with a button, not prose
    assert job.state is JobState.WAITING
    assert fake.written == {}


def test_apply_writes_and_reports(bundle):
    service, registry, fake, _ = bundle
    job = registry.create("tags", 1, 1)
    service.start(job, "Pink Floyd/Meddle (1971)")
    event = service.answer_option(job, APPLY)

    assert isinstance(event, Done)
    assert event.key == "tags.applied"
    assert event.params["written"] == 1
    assert fake.written["01 - One of These Days.flac"].artist == "Пинк Флойд"


def test_switching_the_source_re_proposes_and_asks_again(bundle):
    service, registry, fake, _ = bundle
    job = registry.create("tags", 1, 1)
    service.start(job, "Pink Floyd/Meddle (1971)")

    event = service.answer_option(job, FROM_DIRECTORY)
    assert isinstance(event, Question)
    assert job.payload["proposal"].source is Source.DIRECTORY
    assert fake.written == {}

    event = service.answer_option(job, FROM_FILENAMES)
    assert job.payload["proposal"].source is Source.FILENAME
    assert fake.written == {}

    service.answer_option(job, APPLY)
    written = fake.written["01 - One of These Days.flac"]
    assert written.title == "One of These Days"
    assert written.track_no == 1


def test_cancel_writes_nothing(bundle):
    service, registry, fake, tmp_path = bundle
    job = registry.create("tags", 1, 1)
    service.start(job, "Pink Floyd/Meddle (1971)")
    event = service.answer_option(job, CANCEL)

    assert event.key == "ask.cancelled"
    assert fake.written == {}
    assert not (tmp_path / "Pink Floyd" / "Meddle (1971)" / BACKUP_NAME).exists()
    assert job.state is JobState.DONE


def test_nothing_to_change_finishes_without_a_question(tmp_path):
    album = tmp_path / "Artist" / "Album"
    album.mkdir(parents=True)
    (album / "01 - Song.flac").write_bytes(b"x")
    correct = {"01 - Song.flac": TrackTags(artist="Artist", album="Album", title="Song", track_no=1)}
    fake = FakeTags(correct)
    registry = JobRegistry()
    service = TagService(
        MediaLibrary(tmp_path),
        registry,
        TagFixer(reader=fake.read, writer=fake.write),
    )
    job = registry.create("tags", 1, 1)
    event = service.start(job, "Artist/Album")

    assert isinstance(event, Done)
    assert event.key == "tags.nothing_to_do"
    assert job.state is JobState.DONE


def test_path_is_reported_relative_to_the_library_root(bundle):
    service, registry, _, _ = bundle
    job = registry.create("tags", 1, 1)
    event = service.start(job, "Pink Floyd/Meddle (1971)")
    assert event.params["path"] == "Pink Floyd/Meddle (1971)"


@pytest.mark.parametrize("bad", ["../../etc", "/etc", "/etc/passwd"])
def test_a_path_outside_the_library_is_refused(bundle, bad):
    service, registry, _, _ = bundle
    job = registry.create("tags", 1, 1)
    with pytest.raises(UnsafeName):
        service.start(job, bad)


def test_missing_or_empty_target_raises_a_typed_error(bundle):
    service, registry, _, tmp_path = bundle
    with pytest.raises(PathNotFound):
        service.start(registry.create("tags", 1, 1), "Nope")
    (tmp_path / "Empty").mkdir()
    with pytest.raises(NoAudioFiles):
        service.start(registry.create("tags", 1, 1), "Empty")


def test_absolute_path_inside_the_library_is_accepted(bundle):
    service, registry, _, tmp_path = bundle
    job = registry.create("tags", 1, 1)
    event = service.start(job, str(tmp_path / "Pink Floyd" / "Meddle (1971)"))
    assert isinstance(event, Question)
