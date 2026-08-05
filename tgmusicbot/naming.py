"""Filename sanitising and track-name parsing. No filesystem access here."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .errors import UnsafeName

MAX_COMPONENT_BYTES = 255

# Reserved on Windows/SMB; ``/`` and ``\`` additionally guarantee that a
# sanitised component can never introduce a new path level.
_RESERVED_CHARS = '<>:"/\\|?*'
_TRANSLATION = {ord(c): "-" for c in _RESERVED_CHARS}

# Legacy DOS device names still rejected by SMB shares.
_DEVICE_NAMES = frozenset(
    ["con", "prn", "aux", "nul"]
    + [f"com{i}" for i in range(1, 10)]
    + [f"lpt{i}" for i in range(1, 10)]
)

_WHITESPACE = re.compile(r"\s+")

# "01 - ", "01. ", "01_", "(01) ", "01 "
_LEADING_NUMBER = re.compile(
    r"^\(?(?P<no>\d{1,3})\)?(?:\s*[-._)\]]\s*|\s+)(?P<rest>\S.*)$"
)
_SEPARATOR = re.compile(r"\s+[-–—]\s+")


def sanitize(name: object, *, max_bytes: int = MAX_COMPONENT_BYTES) -> str:
    """Make ``name`` usable as a single path component.

    Path separators and other reserved characters are replaced rather than
    rejected, so ``AC/DC`` survives as ``AC-DC`` instead of failing.  What *is*
    rejected is a component that carries no information after cleaning
    (empty, ``.``, ``..``) — that is the path-traversal case.
    """
    if not isinstance(name, str):
        raise UnsafeName(name)

    cleaned = "".join(ch for ch in name if ch >= " " and ch != "\x7f")
    cleaned = cleaned.translate(_TRANSLATION)
    cleaned = _WHITESPACE.sub(" ", cleaned).strip()
    cleaned = cleaned.rstrip(". ")

    if cleaned in ("", ".", ".."):
        raise UnsafeName(name)

    if cleaned.split(".")[0].lower() in _DEVICE_NAMES:
        cleaned = "_" + cleaned

    return _truncate_bytes(cleaned, max_bytes)


def _truncate_bytes(value: str, max_bytes: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    truncated = encoded[:max_bytes].decode("utf-8", errors="ignore").rstrip(". ")
    if not truncated:
        raise UnsafeName(value)
    return truncated


def split_extension(filename: str) -> tuple[str, str]:
    """``"01. Intro.mp3"`` -> ``("01. Intro", ".mp3")``.

    Only the last dot counts, and only when it looks like an extension —
    this is the fix for the ``title.replace(".", "_0.")`` bug.
    """
    stem, dot, ext = filename.rpartition(".")
    if not dot or not stem or not ext or len(ext) > 5 or not ext.isalnum():
        return filename, ""
    return stem, "." + ext.lower()


@dataclass(frozen=True, slots=True)
class ParsedTrack:
    title: str
    artist: str | None = None
    track_no: int | None = None


def parse_track_filename(filename: str) -> ParsedTrack:
    """Best-effort read of ``NN - Artist - Title`` and its shorter variants.

    Deliberately guesses; the caller is expected to let the user confirm or
    override the result (that is exactly what the Phase 1 ``/tags`` buttons do).
    """
    stem, _ = split_extension(filename)
    stem = _WHITESPACE.sub(" ", stem.replace("_", " ")).strip()

    track_no: int | None = None
    match = _LEADING_NUMBER.match(stem)
    if match:
        track_no = int(match.group("no"))
        stem = match.group("rest").strip()

    artist: str | None = None
    parts = _SEPARATOR.split(stem, maxsplit=1)
    if len(parts) == 2 and parts[0] and parts[1]:
        artist, stem = parts[0].strip(), parts[1].strip()

    return ParsedTrack(title=stem, artist=artist, track_no=track_no)


@dataclass(frozen=True, slots=True)
class AlbumDirName:
    album: str
    artist: str | None = None
    year: int | None = None


_BRACKETED_YEAR = re.compile(r"[(\[]\s*((?:19|20)\d{2})\s*[)\]]")
_LEADING_YEAR = re.compile(r"^((?:19|20)\d{2})\s*[-.–—]?\s+")


def parse_album_dirname(name: str) -> AlbumDirName:
    """``"Pink Floyd - Meddle (1971)"`` -> artist, album and year.

    Used when the user says the *directory* is the source of truth.
    """
    text = _WHITESPACE.sub(" ", name.replace("_", " ")).strip()

    year: int | None = None
    bracketed = _BRACKETED_YEAR.search(text)
    if bracketed:
        year = int(bracketed.group(1))
        text = text[: bracketed.start()] + " " + text[bracketed.end() :]
    else:
        leading = _LEADING_YEAR.match(text)
        if leading:
            year = int(leading.group(1))
            text = text[leading.end() :]

    text = _WHITESPACE.sub(" ", text).strip(" -–—.")

    artist: str | None = None
    parts = _SEPARATOR.split(text, maxsplit=1)
    if len(parts) == 2 and parts[0] and parts[1]:
        artist, text = parts[0].strip(), parts[1].strip()

    return AlbumDirName(album=text, artist=artist, year=year)


def format_track_filename(
    title: str, extension: str, track_no: int | None = None
) -> str:
    """``("Intro", ".mp3", 1)`` -> ``"01 - Intro.mp3"``."""
    stem = sanitize(title, max_bytes=MAX_COMPONENT_BYTES - len(extension.encode()) - 5)
    if track_no is not None:
        stem = f"{track_no:02d} - {stem}"
    return stem + extension
