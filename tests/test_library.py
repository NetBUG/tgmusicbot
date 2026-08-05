import io

import pytest

from tgmusicbot.errors import AlbumUnknown, ArtistUnknown, TooLarge, UnsupportedFormat
from tgmusicbot.library import INCOMING_DIR, IngestStatus, MediaLibrary
from tgmusicbot.tags import TrackTags

TAGS = TrackTags(artist="Pink Floyd", album="The Dark Side of the Moon", title="Time", track_no=4)


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        self.now += 0.5
        return self.now


@pytest.fixture
def library(tmp_path):
    return MediaLibrary(tmp_path, clock=FakeClock(), chunk_size=8)


def ingest(library, payload=b"audio-bytes", tags=TAGS, ext=".mp3", **kwargs):
    return library.ingest_stream(io.BytesIO(payload), tags, ext, **kwargs)


def test_three_level_layout(library, tmp_path):
    result = ingest(library)
    assert result.path == (
        tmp_path / "Pink Floyd" / "The Dark Side of the Moon" / "04 - Time.mp3"
    )
    assert result.status is IngestStatus.CREATED
    assert result.path.read_bytes() == b"audio-bytes"
    assert result.duration_s > 0


def test_identical_file_is_not_rewritten(library):
    first = ingest(library)
    mtime = first.path.stat().st_mtime_ns

    second = ingest(library)
    assert second.status is IngestStatus.DUPLICATE
    assert second.path == first.path
    assert second.path.stat().st_mtime_ns == mtime  # bug 3: no redundant write


def test_same_name_different_bytes_is_renamed(library):
    first = ingest(library, b"one")
    second = ingest(library, b"two")
    assert second.status is IngestStatus.RENAMED
    assert second.path.name == "04 - Time (2).mp3"
    assert first.path.read_bytes() == b"one"
    assert second.path.read_bytes() == b"two"


def test_duplicate_of_a_renamed_copy_is_detected(library):
    ingest(library, b"one")
    renamed = ingest(library, b"two")
    again = ingest(library, b"two")
    assert again.status is IngestStatus.DUPLICATE
    assert again.path == renamed.path


def test_path_traversal_cannot_escape_the_root(library, tmp_path):
    result = ingest(library, tags=TAGS.with_(artist="../../etc", album="../evil"))
    assert tmp_path in result.path.parents
    assert result.path.resolve().is_relative_to(tmp_path.resolve())


def test_a_component_that_is_only_traversal_is_rejected(library, tmp_path):
    from tgmusicbot.errors import UnsafeName

    with pytest.raises(UnsafeName):
        ingest(library, tags=TAGS.with_(album=".."))
    assert list((tmp_path / INCOMING_DIR).glob("*.part")) == []


def test_missing_metadata_raises_the_specific_error(library):
    with pytest.raises(ArtistUnknown):
        ingest(library, tags=TrackTags(album="x", title="y"))
    with pytest.raises(AlbumUnknown):
        ingest(library, tags=TrackTags(artist="x", title="y"))


def test_unsupported_extension(library):
    with pytest.raises(UnsupportedFormat):
        ingest(library, ext=".exe")


def test_oversized_upload_leaves_nothing_behind(library, tmp_path):
    with pytest.raises(TooLarge):
        ingest(library, b"x" * 100, max_bytes=10)
    assert list((tmp_path / INCOMING_DIR).glob("*.part")) == []


def test_failed_ingest_does_not_leave_a_staged_file(library, tmp_path):
    with pytest.raises(ArtistUnknown):
        ingest(library, tags=TrackTags(album="a", title="t"))
    assert list((tmp_path / INCOMING_DIR).glob("*.part")) == []


def test_staged_file_is_invisible_until_complete(library, tmp_path):
    staged = library.stage(io.BytesIO(b"data"))
    assert staged.path.parent.name == INCOMING_DIR
    assert staged.path.parent.name.startswith(".")  # scanners skip dotdirs
    assert not (tmp_path / "Pink Floyd").exists()

    result = library.ingest_staged(staged, TAGS, ".mp3")
    assert result.status is IngestStatus.CREATED
    assert not staged.path.exists()


def test_sweep_incoming_removes_abandoned_staging(library, tmp_path):
    staged = library.stage(io.BytesIO(b"data"))
    assert library.sweep_incoming(3600, now=staged.path.stat().st_mtime + 10) == 0
    assert library.sweep_incoming(5, now=staged.path.stat().st_mtime + 10) == 1
    assert not staged.path.exists()


def test_ingest_file_from_disk(library, tmp_path):
    source = tmp_path / "source.flac"
    source.write_bytes(b"flac-bytes")
    result = library.ingest_file(source, TAGS)
    assert result.path.suffix == ".flac"
    assert result.path.read_bytes() == b"flac-bytes"


def test_sha256_is_streamed_not_buffered(library, tmp_path):
    import hashlib

    payload = b"y" * 1000
    result = ingest(library, payload)
    assert result.sha256 == hashlib.sha256(payload).hexdigest()
    assert result.size == len(payload)
