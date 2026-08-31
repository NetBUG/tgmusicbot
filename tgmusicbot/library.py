"""The media library: the only module that touches the collection on disk.

Layout is three levels — ``<root>/<artist>/<album>/<NN - title>.<ext>`` — which
is what Navidrome and Jellyfin expect from the shared read-only NFS export.

Ingest is two-step on purpose.  ``stage()`` streams the bytes into
``<root>/.incoming`` and hashes them; ``ingest_staged()`` moves the staged file
into place with ``os.replace``.  Between the two the caller can read the file's
own tags and, if the album is unknown, ask the user — without holding the file
in RAM and without a half-written file ever appearing where a scanner looks.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import BinaryIO

from . import naming
from .errors import TooLarge, UnsafeName, UnsupportedFormat
from .tags import AUDIO_EXTENSIONS, TrackTags

CHUNK_SIZE = 1 << 20
INCOMING_DIR = ".incoming"


class IngestStatus(StrEnum):
    CREATED = "created"
    DUPLICATE = "duplicate"
    RENAMED = "renamed"


@dataclass(frozen=True, slots=True)
class Staged:
    """Bytes safely on disk, not yet part of the library."""

    path: Path
    size: int
    sha256: str

    def discard(self) -> None:
        self.path.unlink(missing_ok=True)


@dataclass(frozen=True, slots=True)
class IngestResult:
    path: Path
    status: IngestStatus
    size: int
    sha256: str
    duration_s: float

    @property
    def stored(self) -> bool:
        return self.status is not IngestStatus.DUPLICATE


class MediaLibrary:
    def __init__(
        self,
        root: Path,
        *,
        clock: Callable[[], float] = time.monotonic,
        chunk_size: int = CHUNK_SIZE,
    ):
        self.root = Path(root)
        self._clock = clock
        self._chunk_size = chunk_size
        self._sequence = 0
        self._lock_counter = os.getpid()

    # -- placement ---------------------------------------------------------

    def album_dir(self, artist: str, album: str) -> Path:
        return self.root / naming.sanitize(artist) / naming.sanitize(album)

    def resolve(self, relative: str) -> Path:
        """Turn user input into a path guaranteed to be inside the library."""
        text = str(relative).strip().strip('"').rstrip("/")
        root = self.root.resolve()
        candidate = Path(text)
        if candidate.is_absolute():
            resolved = candidate.resolve()
        else:
            resolved = (root / text.lstrip("/")).resolve()
        if resolved != root and not resolved.is_relative_to(root):
            raise UnsafeName(relative)
        return resolved

    def track_path(self, tags: TrackTags, extension: str) -> Path:
        tags.require_complete()
        filename = naming.format_track_filename(
            tags.title,  # type: ignore[arg-type]  # require_complete() checked it
            check_extension(extension),
            tags.track_no,
        )
        return self.album_dir(tags.artist, tags.album) / filename  # type: ignore[arg-type]

    # -- ingest ------------------------------------------------------------

    def stage(self, stream: BinaryIO, *, max_bytes: int | None = None) -> Staged:
        """Stream into ``.incoming`` while hashing. Never holds the file in RAM."""
        incoming = self.root / INCOMING_DIR
        incoming.mkdir(parents=True, exist_ok=True)
        self._sequence += 1
        temp = incoming / f"{self._lock_counter}-{self._sequence}.part"

        digest = hashlib.sha256()
        size = 0
        try:
            with temp.open("wb") as out:
                while chunk := stream.read(self._chunk_size):
                    size += len(chunk)
                    if max_bytes is not None and size > max_bytes:
                        raise TooLarge(size, max_bytes)
                    digest.update(chunk)
                    out.write(chunk)
        except BaseException:
            temp.unlink(missing_ok=True)
            raise
        return Staged(path=temp, size=size, sha256=digest.hexdigest())

    def rename_staged(self, staged: Staged, extension: str) -> Staged:
        """Give the staged file its real extension.

        mutagen sniffs by content *and* filename — an MP3 with no ID3 block
        scores zero when the name ends in ``.part`` — so tag reading and writing
        both want the proper suffix before anything touches the file.
        """
        target = staged.path.with_name(staged.path.stem + check_extension(extension))
        if target == staged.path:
            return staged
        os.replace(staged.path, target)
        return Staged(path=target, size=staged.size, sha256=staged.sha256)

    def restat(self, staged: Staged) -> Staged:
        """Re-measure a staged file that was modified in place (tags written)."""
        return Staged(
            path=staged.path,
            size=staged.path.stat().st_size,
            sha256=self.sha256(staged.path),
        )

    def ingest_staged(
        self, staged: Staged, tags: TrackTags, extension: str
    ) -> IngestResult:
        started = self._clock()
        extension = check_extension(extension)
        tags.require_complete()
        try:
            target = self.track_path(tags, extension)
            target.parent.mkdir(parents=True, exist_ok=True)
            final, status = self._resolve_clash(target, staged.size, staged.sha256)
            if status is not IngestStatus.DUPLICATE:
                os.replace(staged.path, final)
        finally:
            staged.discard()

        return IngestResult(
            path=final,
            status=status,
            size=staged.size,
            sha256=staged.sha256,
            duration_s=self._clock() - started,
        )

    def ingest_stream(
        self,
        stream: BinaryIO,
        tags: TrackTags,
        extension: str,
        *,
        max_bytes: int | None = None,
    ) -> IngestResult:
        check_extension(extension)
        tags.require_complete()
        staged = self.stage(stream, max_bytes=max_bytes)
        try:
            return self.ingest_staged(staged, tags, extension)
        except BaseException:
            staged.discard()
            raise

    def ingest_file(
        self, source: Path, tags: TrackTags, *, max_bytes: int | None = None
    ) -> IngestResult:
        """Ingest a file already on disk (yt-dlp output, finished torrent)."""
        source = Path(source)
        with source.open("rb") as handle:
            return self.ingest_stream(handle, tags, source.suffix, max_bytes=max_bytes)

    def sweep_incoming(self, older_than_s: float, *, now: float | None = None) -> int:
        """Delete abandoned staging: files nobody answered a question about, and
        working directories left behind by a download that died."""
        incoming = self.root / INCOMING_DIR
        if not incoming.is_dir():
            return 0
        now = time.time() if now is None else now
        removed = 0
        for leftover in incoming.iterdir():
            if now - leftover.stat().st_mtime <= older_than_s:
                continue
            if leftover.is_dir():
                shutil.rmtree(leftover, ignore_errors=True)
            else:
                leftover.unlink(missing_ok=True)
            removed += 1
        return removed

    # -- internals ---------------------------------------------------------

    def _resolve_clash(
        self, target: Path, size: int, digest: str
    ) -> tuple[Path, IngestStatus]:
        if not target.exists():
            return target, IngestStatus.CREATED
        if self._same_file(target, size, digest):
            return target, IngestStatus.DUPLICATE

        stem, extension = naming.split_extension(target.name)
        for counter in range(2, 1000):
            candidate = target.with_name(f"{stem} ({counter}){extension}")
            if not candidate.exists():
                return candidate, IngestStatus.RENAMED
            if self._same_file(candidate, size, digest):
                return candidate, IngestStatus.DUPLICATE
        raise FileExistsError(target)

    def _same_file(self, path: Path, size: int, digest: str) -> bool:
        return path.stat().st_size == size and self.sha256(path) == digest

    def sha256(self, path: Path) -> str:
        digest = hashlib.sha256()
        with Path(path).open("rb") as handle:
            while chunk := handle.read(self._chunk_size):
                digest.update(chunk)
        return digest.hexdigest()


def check_extension(extension: str) -> str:
    extension = extension.lower()
    if not extension.startswith("."):
        extension = "." + extension
    if extension not in AUDIO_EXTENSIONS:
        raise UnsupportedFormat(extension)
    return extension
