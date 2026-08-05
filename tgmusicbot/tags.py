"""Format-agnostic tag reading.

The library already contains FLAC, and Phase 2 will add Ogg/Opus, so nothing
here may assume ID3.  ``mutagen``'s ``easy`` mapping gives one vocabulary over
ID3 frames, Vorbis comments and MP4 atoms; Phase 1 adds the writing side.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from . import naming
from .errors import AlbumUnknown, ArtistUnknown, TitleUnknown, UnsupportedFormat

AUDIO_EXTENSIONS = frozenset(
    {".mp3", ".flac", ".m4a", ".mp4", ".aac", ".ogg", ".oga", ".opus", ".wav", ".ape", ".wv", ".wma"}
)


@dataclass(frozen=True, slots=True)
class TrackTags:
    artist: str | None = None
    album: str | None = None
    title: str | None = None
    track_no: int | None = None
    total_tracks: int | None = None

    def merged_with(self, fallback: "TrackTags") -> "TrackTags":
        """Fill empty fields from ``fallback``; own values always win."""
        return TrackTags(
            artist=self.artist or fallback.artist,
            album=self.album or fallback.album,
            title=self.title or fallback.title,
            track_no=self.track_no if self.track_no is not None else fallback.track_no,
            total_tracks=(
                self.total_tracks
                if self.total_tracks is not None
                else fallback.total_tracks
            ),
        )

    def require_complete(self) -> "TrackTags":
        """Raise the specific typed error for the first missing placement field."""
        if not self.artist:
            raise ArtistUnknown()
        if not self.album:
            raise AlbumUnknown()
        if not self.title:
            raise TitleUnknown()
        return self

    def with_(self, **changes: object) -> "TrackTags":
        return replace(self, **changes)  # type: ignore[arg-type]


def tags_from_filename(filename: str) -> TrackTags:
    parsed = naming.parse_track_filename(filename)
    return TrackTags(artist=parsed.artist, title=parsed.title, track_no=parsed.track_no)


def read_tags(path: Path) -> TrackTags:
    """Read what the file itself claims. Never raises on a broken file."""
    import mutagen  # imported lazily: the pure-logic modules stay dependency-free

    try:
        audio = mutagen.File(path, easy=True)
    except Exception:  # noqa: BLE001 — a corrupt file is data, not a bug
        audio = None
    if audio is None or not audio.tags:
        return TrackTags()

    def first(key: str) -> str | None:
        values = audio.tags.get(key)
        if not values:
            return None
        value = values[0] if isinstance(values, list) else values
        value = str(value).strip()
        return value or None

    track_no, total = _parse_track_number(first("tracknumber"))
    return TrackTags(
        artist=first("albumartist") or first("artist"),
        album=first("album"),
        title=first("title"),
        track_no=track_no,
        total_tracks=total,
    )


def write_tags(path: Path, tags: TrackTags) -> None:
    """Write tags back in the format the file already uses.

    ID3 goes out as v2.4/UTF-8 with no v1 block (v1 has no encoding field at
    all, and a stale latin-1 v1 tag is how half the mojibake in an old
    collection survives a repair).
    """
    import mutagen

    try:
        audio = mutagen.File(path, easy=True)
    except Exception as error:  # noqa: BLE001 — unparseable is "not audio" to us
        raise UnsupportedFormat(Path(path).suffix) from error
    if audio is None:
        raise UnsupportedFormat(Path(path).suffix)
    if audio.tags is None:
        audio.add_tags()

    _assign(audio, "artist", tags.artist)
    _assign(audio, "albumartist", tags.artist)
    _assign(audio, "album", tags.album)
    _assign(audio, "title", tags.title)
    _assign(audio, "tracknumber", _format_track_number(tags))

    try:
        audio.save(v1=0, v2_version=4)
    except TypeError:  # Vorbis comments and MP4 atoms take no ID3 options
        audio.save()


def _assign(audio, key: str, value: str | None) -> None:
    if value:
        try:
            audio.tags[key] = value
        except (KeyError, ValueError):
            pass  # e.g. no albumartist in this format's easy mapping
    elif key in audio.tags:
        del audio.tags[key]


def _format_track_number(tags: TrackTags) -> str | None:
    if tags.track_no is None:
        return None
    if tags.total_tracks:
        return f"{tags.track_no}/{tags.total_tracks}"
    return str(tags.track_no)


def _parse_track_number(raw: str | None) -> tuple[int | None, int | None]:
    """``"3/12"`` -> ``(3, 12)``; ``"03"`` -> ``(3, None)``."""
    if not raw:
        return None, None
    head, _, tail = raw.partition("/")
    return _to_int(head), _to_int(tail)


def _to_int(raw: str) -> int | None:
    raw = raw.strip()
    if not raw.isdigit():
        return None
    value = int(raw)
    return value if value > 0 else None
