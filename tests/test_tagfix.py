import json

import pytest

from tgmusicbot.errors import NoAudioFiles, PathNotFound
from tgmusicbot.tagfix import BACKUP_NAME, Source, TagFixer
from tgmusicbot.tags import TrackTags

MOJIBAKE = "Пинк Флойд".encode("cp1251").decode("latin-1")


class FakeTags:
    """Stands in for the file's own tags, so no real audio is needed."""

    def __init__(self, mapping=None):
        self.mapping = mapping or {}
        self.written: dict = {}

    def read(self, path):
        return self.mapping.get(path.name, TrackTags())

    def write(self, path, tags):
        self.written[path.name] = tags


@pytest.fixture
def album(tmp_path):
    directory = tmp_path / "Pink Floyd" / "Meddle (1971)"
    directory.mkdir(parents=True)
    for name in ("01 - One of These Days.flac", "02 - A Pillow of Winds.flac"):
        (directory / name).write_bytes(b"x")
    return directory


def fixer(fake, clock=lambda: 1000.0):
    return TagFixer(reader=fake.read, writer=fake.write, clock=clock)


def test_proposal_writes_nothing(album):
    fake = FakeTags()
    proposal = fixer(fake).propose(album)
    assert fake.written == {}
    assert len(proposal.tracks) == 2
    assert proposal.changed


def test_source_tags_repairs_mojibake_and_keeps_the_rest(album):
    fake = FakeTags(
        {
            "01 - One of These Days.flac": TrackTags(
                artist=MOJIBAKE, album="Meddle", title="One of These Days", track_no=1
            )
        }
    )
    proposal = fixer(fake).propose(album, Source.TAGS)
    track = proposal.tracks[0]
    assert track.proposed.artist == "Пинк Флойд"
    assert track.proposed.album == "Meddle"
    assert track.changes["artist"] == (MOJIBAKE, "Пинк Флойд")


def test_source_directory_overrides_the_tags(album):
    fake = FakeTags(
        {
            "01 - One of These Days.flac": TrackTags(
                artist="Wrong Band", album="Wrong Album", title="One of These Days"
            )
        }
    )
    proposal = fixer(fake).propose(album, Source.DIRECTORY)
    track = proposal.tracks[0]
    assert track.proposed.artist == "Pink Floyd"  # parent directory
    assert track.proposed.album == "Meddle"  # directory name, year stripped
    assert track.proposed.title == "One of These Days"  # tags still supply this


def test_source_filename_overrides_the_tags(album):
    fake = FakeTags(
        {"01 - One of These Days.flac": TrackTags(title="Wrong Title", track_no=9)}
    )
    proposal = fixer(fake).propose(album, Source.FILENAME)
    track = proposal.tracks[0]
    assert track.proposed.title == "One of These Days"
    assert track.proposed.track_no == 1
    assert track.proposed.album == "Meddle"


def test_total_tracks_is_the_count_in_that_directory(album):
    proposal = fixer(FakeTags()).propose(album)
    assert {track.proposed.total_tracks for track in proposal.tracks} == {2}


def test_a_single_track_gets_no_total(tmp_path):
    directory = tmp_path / "Pink Floyd" / "Singles"
    directory.mkdir(parents=True)
    (directory / "Time.flac").write_bytes(b"x")
    proposal = fixer(FakeTags()).propose(directory)
    assert proposal.tracks[0].proposed.total_tracks is None


def test_artist_level_target_walks_every_album(tmp_path):
    artist = tmp_path / "Pink Floyd"
    for album_name in ("Meddle (1971)", "Animals (1977)"):
        (artist / album_name).mkdir(parents=True)
        (artist / album_name / "01 - Track.flac").write_bytes(b"x")
    proposal = fixer(FakeTags()).propose(artist, Source.DIRECTORY)
    assert {track.proposed.album for track in proposal.tracks} == {"Meddle", "Animals"}


def test_nothing_to_change_is_not_a_change(album):
    correct = {
        "01 - One of These Days.flac": TrackTags(
            artist="Pink Floyd",
            album="Meddle",
            title="One of These Days",
            track_no=1,
            total_tracks=2,
        ),
        "02 - A Pillow of Winds.flac": TrackTags(
            artist="Pink Floyd",
            album="Meddle",
            title="A Pillow of Winds",
            track_no=2,
            total_tracks=2,
        ),
    }
    proposal = fixer(FakeTags(correct)).propose(album)
    assert proposal.changed == ()


def test_apply_writes_only_changed_tracks_and_backs_them_up(album):
    fake = FakeTags(
        {
            "01 - One of These Days.flac": TrackTags(artist=MOJIBAKE),
            "02 - A Pillow of Winds.flac": TrackTags(),
        }
    )
    tagger = fixer(fake)
    proposal = tagger.propose(album)
    applied = tagger.apply(proposal)

    assert applied.written == 2
    assert applied.failures == ()
    assert set(fake.written) == {
        "01 - One of These Days.flac",
        "02 - A Pillow of Winds.flac",
    }

    backup = json.loads((album / BACKUP_NAME).read_text(encoding="utf-8"))
    assert len(backup) == 1
    assert backup[0]["timestamp"] == 1000.0
    assert backup[0]["source"] == "tags"
    assert backup[0]["tracks"][0]["before"]["artist"] == MOJIBAKE
    assert backup[0]["tracks"][0]["after"]["artist"] == "Пинк Флойд"


def test_backup_is_appended_not_overwritten(album):
    fake = FakeTags({"01 - One of These Days.flac": TrackTags(artist=MOJIBAKE)})
    tagger = fixer(fake)
    tagger.apply(tagger.propose(album))
    tagger.apply(tagger.propose(album))
    backup = json.loads((album / BACKUP_NAME).read_text(encoding="utf-8"))
    assert len(backup) == 2


def test_apply_on_an_unchanged_proposal_writes_no_backup(album):
    fake = FakeTags()
    tagger = fixer(fake)
    proposal = tagger.propose(album)
    object.__setattr__(proposal, "tracks", ())
    applied = tagger.apply(proposal)
    assert applied == type(applied)(written=0, backup=None)
    assert not (album / BACKUP_NAME).exists()


def test_one_unwritable_file_does_not_stop_the_others(album):
    fake = FakeTags()

    def explode(path, tags):
        if path.name.startswith("01"):
            raise OSError("read-only file system")
        fake.written[path.name] = tags

    tagger = TagFixer(reader=fake.read, writer=explode, clock=lambda: 0.0)
    applied = tagger.apply(tagger.propose(album))
    assert applied.written == 1
    assert len(applied.failures) == 1
    assert "read-only" in applied.failures[0][1]


def test_missing_directory_and_empty_directory(tmp_path):
    tagger = fixer(FakeTags())
    with pytest.raises(PathNotFound):
        tagger.propose(tmp_path / "nope")
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(NoAudioFiles):
        tagger.propose(empty)


def test_backup_file_is_not_mistaken_for_audio(album):
    (album / BACKUP_NAME).write_text("[]", encoding="utf-8")
    proposal = fixer(FakeTags()).propose(album)
    assert len(proposal.tracks) == 2


def test_the_topic_suffix_is_proposed_for_removal(album):
    fake = FakeTags(
        {"01 - One of These Days.flac": TrackTags(artist="Pink Floyd - Topic")}
    )
    proposal = fixer(fake).propose(album)
    assert proposal.tracks[0].changes["artist"] == ("Pink Floyd - Topic", "Pink Floyd")
