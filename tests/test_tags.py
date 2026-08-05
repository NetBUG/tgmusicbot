"""Round-trips against real files, synthesised in-process so no fixtures or
ffmpeg are needed: a bare MPEG-1 Layer III stream and a bare FLAC STREAMINFO
are enough for mutagen to attach tags to."""

import struct

import pytest

from tgmusicbot.tags import TrackTags, read_tags, tags_from_filename, write_tags

CYRILLIC = "Гражданская оборона"


def make_mp3(path):
    path.write_bytes((b"\xff\xfb\x90\x00" + b"\x00" * 413) * 20)
    return path


def make_flac(path):
    info = struct.pack(">HH", 4096, 4096)
    info += (4096).to_bytes(3, "big") + (4096).to_bytes(3, "big")
    info += ((44100 << 44) | (1 << 41) | (15 << 36)).to_bytes(8, "big")
    info += b"\x00" * 16
    path.write_bytes(b"fLaC" + b"\x80" + len(info).to_bytes(3, "big") + info)
    return path


@pytest.fixture(params=["mp3", "flac"])
def audio(request, tmp_path):
    path = tmp_path / f"track.{request.param}"
    return (make_mp3 if request.param == "mp3" else make_flac)(path)


TAGS = TrackTags(
    artist=CYRILLIC, album="Русское поле экспериментов", title="Всё идёт по плану",
    track_no=3, total_tracks=12,
)


def test_round_trip_in_both_formats(audio):
    write_tags(audio, TAGS)
    assert read_tags(audio) == TAGS


def test_untagged_file_reads_as_empty(audio):
    assert read_tags(audio) == TrackTags()


def test_unreadable_file_is_data_not_a_crash(tmp_path):
    junk = tmp_path / "not-audio.mp3"
    junk.write_bytes(b"definitely not audio")
    assert read_tags(junk) == TrackTags()


def test_id3_is_written_as_v24_utf8_without_a_v1_block(tmp_path):
    from mutagen.id3 import ID3

    path = make_mp3(tmp_path / "track.mp3")
    write_tags(path, TAGS)

    assert ID3(path).version == (2, 4, 0)
    # a stale latin-1 v1 tag is how mojibake survives a repair
    assert path.read_bytes()[-128:-125] != b"TAG"
    assert CYRILLIC in read_tags(path).artist


def test_track_number_is_written_as_n_of_total(tmp_path):
    from mutagen.id3 import ID3

    path = make_mp3(tmp_path / "track.mp3")
    write_tags(path, TAGS)
    assert str(ID3(path)["TRCK"]) == "3/12"

    write_tags(path, TAGS.with_(total_tracks=None))
    assert str(ID3(path)["TRCK"]) == "3"


def test_clearing_a_field_removes_it(audio):
    write_tags(audio, TAGS)
    write_tags(audio, TAGS.with_(album=None))
    assert read_tags(audio).album is None
    assert read_tags(audio).title == TAGS.title


def test_write_rejects_a_non_audio_file(tmp_path):
    from tgmusicbot.errors import UnsupportedFormat

    junk = tmp_path / "notes.mp3"
    junk.write_bytes(b"nope")
    with pytest.raises(UnsupportedFormat):
        write_tags(junk, TAGS)


@pytest.mark.parametrize(
    "raw,expected",
    [("3/12", (3, 12)), ("03", (3, None)), ("0", (None, None)), ("", (None, None))],
)
def test_track_number_parsing(raw, expected, tmp_path):
    from tgmusicbot.tags import _parse_track_number

    assert _parse_track_number(raw) == expected


def test_tags_from_filename():
    assert tags_from_filename("03 - Pink Floyd - Time.flac") == TrackTags(
        artist="Pink Floyd", title="Time", track_no=3
    )


def test_merged_with_keeps_own_values():
    own = TrackTags(artist="A", track_no=1)
    other = TrackTags(artist="B", album="C", track_no=9)
    assert own.merged_with(other) == TrackTags(artist="A", album="C", track_no=1)
