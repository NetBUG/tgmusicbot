import pytest

from tgmusicbot import naming
from tgmusicbot.errors import UnsafeName


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("AC/DC", "AC-DC"),
        ("../../etc/passwd", "..-..-etc-passwd"),
        ("Sigur Rós", "Sigur Rós"),
        ("name\x00with\x07control", "namewithcontrol"),
        ("trailing dots...", "trailing dots"),
        ("trailing space   ", "trailing space"),
        ("  lots   of    space  ", "lots of space"),
        ('a<b>c:d"e|f?g*h', "a-b-c-d-e-f-g-h"),
    ],
)
def test_sanitize(raw, expected):
    assert naming.sanitize(raw) == expected


def test_sanitize_never_introduces_a_path_level():
    assert "/" not in naming.sanitize("a/b/c")


@pytest.mark.parametrize("raw", ["", "   ", ".", "..", "...", None, 42])
def test_sanitize_rejects_empty_and_traversal(raw):
    with pytest.raises(UnsafeName):
        naming.sanitize(raw)


def test_sanitize_escapes_dos_device_names():
    assert naming.sanitize("CON") == "_CON"
    assert naming.sanitize("nul.mp3") == "_nul.mp3"


def test_sanitize_truncates_on_byte_boundary():
    result = naming.sanitize("я" * 300)
    assert len(result.encode("utf-8")) <= naming.MAX_COMPONENT_BYTES
    assert "�" not in result


@pytest.mark.parametrize(
    "filename,stem,ext",
    [
        ("01. Intro.mp3", "01. Intro", ".mp3"),
        ("no extension", "no extension", ""),
        ("weird.name.FLAC", "weird.name", ".flac"),
        (".hidden", ".hidden", ""),
        ("track.verylongext", "track.verylongext", ""),
    ],
)
def test_split_extension(filename, stem, ext):
    assert naming.split_extension(filename) == (stem, ext)


def test_split_extension_fixes_the_replace_dot_bug():
    """``title.replace(".", "_0.")`` mangled every dot; only the last is an ext."""
    stem, ext = naming.split_extension("01. Intro.mp3")
    assert f"{stem} (2){ext}" == "01. Intro (2).mp3"


@pytest.mark.parametrize(
    "filename,track_no,artist,title",
    [
        ("03 - Pink Floyd - Time.flac", 3, "Pink Floyd", "Time"),
        ("03 - Time.flac", 3, None, "Time"),
        ("03. Time.flac", 3, None, "Time"),
        ("(03) Time.flac", 3, None, "Time"),
        ("03_Time.flac", 3, None, "Time"),
        ("Pink Floyd - Time.flac", None, "Pink Floyd", "Time"),
        ("Time.flac", None, None, "Time"),
        ("Pink Floyd – Time.flac", None, "Pink Floyd", "Time"),
    ],
)
def test_parse_track_filename(filename, track_no, artist, title):
    parsed = naming.parse_track_filename(filename)
    assert (parsed.track_no, parsed.artist, parsed.title) == (track_no, artist, title)


def test_format_track_filename():
    assert naming.format_track_filename("Time", ".flac", 3) == "03 - Time.flac"
    assert naming.format_track_filename("Time", ".flac") == "Time.flac"
    assert naming.format_track_filename("A/B", ".mp3", 12) == "12 - A-B.mp3"


def test_format_track_filename_keeps_component_within_limit():
    name = naming.format_track_filename("я" * 300, ".flac", 1)
    assert len(name.encode("utf-8")) <= naming.MAX_COMPONENT_BYTES
    assert name.endswith(".flac")


@pytest.mark.parametrize(
    "dirname,album,artist,year",
    [
        ("Meddle", "Meddle", None, None),
        ("Meddle (1971)", "Meddle", None, 1971),
        ("Meddle [1971]", "Meddle", None, 1971),
        ("1971 - Meddle", "Meddle", None, 1971),
        ("1971 Meddle", "Meddle", None, 1971),
        ("Pink Floyd - Meddle (1971)", "Meddle", "Pink Floyd", 1971),
        ("Pink_Floyd_-_Meddle", "Meddle", "Pink Floyd", None),
        ("2001: A Space Odyssey", "2001: A Space Odyssey", None, None),
        ("Greatest Hits 2", "Greatest Hits 2", None, None),
    ],
)
def test_parse_album_dirname(dirname, album, artist, year):
    parsed = naming.parse_album_dirname(dirname)
    assert (parsed.album, parsed.artist, parsed.year) == (album, artist, year)
