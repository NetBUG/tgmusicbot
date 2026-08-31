"""Feature 4: repair the tags of an album or an artist subtree.

Nothing here writes anything until :meth:`TagFixer.apply` is called — the
proposal *is* the dry run.  Which of three sources is authoritative is the
user's choice, never a guess:

* :attr:`Source.TAGS` — what the files claim, with mis-decoded text repaired
* :attr:`Source.DIRECTORY` — the folder names decide artist and album
* :attr:`Source.FILENAME` — the filenames decide track number, artist, title

Before writing, the current tags are dumped to ``.tags-backup.json`` next to
the target, so a bad call is one file away from being undone.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from . import naming
from .encoding import repair
from .errors import NoAudioFiles, PathNotFound
from .tags import AUDIO_EXTENSIONS, TrackTags, read_tags, tags_from_filename, write_tags

BACKUP_NAME = ".tags-backup.json"
FIELDS = ("artist", "album", "title", "track_no", "total_tracks")


class Source(StrEnum):
    TAGS = "tags"
    DIRECTORY = "directory"
    FILENAME = "filename"


@dataclass(frozen=True, slots=True)
class TrackProposal:
    path: Path
    current: TrackTags
    proposed: TrackTags

    @property
    def changes(self) -> dict[str, tuple[object, object]]:
        return {
            field: (getattr(self.current, field), getattr(self.proposed, field))
            for field in FIELDS
            if getattr(self.current, field) != getattr(self.proposed, field)
        }

    @property
    def changed(self) -> bool:
        return bool(self.changes)


@dataclass(frozen=True, slots=True)
class Proposal:
    target: Path
    source: Source
    tracks: tuple[TrackProposal, ...]

    @property
    def changed(self) -> tuple[TrackProposal, ...]:
        return tuple(track for track in self.tracks if track.changed)


@dataclass(frozen=True, slots=True)
class Applied:
    written: int
    backup: Path | None
    failures: tuple[tuple[Path, str], ...] = ()


class TagFixer:
    def __init__(
        self,
        *,
        reader: Callable[[Path], TrackTags] = read_tags,
        writer: Callable[[Path, TrackTags], None] = write_tags,
        clock: Callable[[], float] = time.time,
    ):
        self._read = reader
        self._write = writer
        self._clock = clock

    # -- proposing ---------------------------------------------------------

    def propose(self, target: Path, source: Source = Source.TAGS) -> Proposal:
        target = Path(target)
        if not target.is_dir():
            raise PathNotFound(str(target))

        files = _audio_files(target)
        if not files:
            raise NoAudioFiles(str(target))

        totals: dict[Path, int] = {}
        for path in files:
            totals[path.parent] = totals.get(path.parent, 0) + 1

        tracks = tuple(
            self._propose_one(path, source, totals[path.parent]) for path in files
        )
        return Proposal(target=target, source=source, tracks=tracks)

    def _propose_one(self, path: Path, source: Source, total: int) -> TrackProposal:
        current = self._read(path)
        repaired = _repair_all(current)
        from_name = tags_from_filename(path.name)
        from_dir = _tags_from_directory(path.parent)

        if source is Source.DIRECTORY:
            proposed = from_dir.merged_with(repaired).merged_with(from_name)
        elif source is Source.FILENAME:
            proposed = from_name.merged_with(from_dir).merged_with(repaired)
        else:
            proposed = repaired.merged_with(from_name).merged_with(from_dir)

        proposed = proposed.with_(total_tracks=total if total > 1 else None)
        return TrackProposal(path=path, current=current, proposed=_clean(proposed))

    # -- applying ----------------------------------------------------------

    def apply(self, proposal: Proposal) -> Applied:
        changed = proposal.changed
        if not changed:
            return Applied(written=0, backup=None)

        backup = self._write_backup(proposal, changed)

        written = 0
        failures: list[tuple[Path, str]] = []
        for track in changed:
            try:
                self._write(track.path, track.proposed)
            except Exception as error:  # noqa: BLE001 — one bad file must not stop the rest
                failures.append((track.path, str(error)))
            else:
                written += 1
        return Applied(written=written, backup=backup, failures=tuple(failures))

    def _write_backup(
        self, proposal: Proposal, changed: Iterable[TrackProposal]
    ) -> Path:
        path = proposal.target / BACKUP_NAME
        history = []
        if path.exists():
            try:
                history = json.loads(path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                history = []

        history.append(
            {
                "timestamp": self._clock(),
                "source": str(proposal.source),
                "tracks": [
                    {
                        "file": str(track.path.relative_to(proposal.target)),
                        "before": _as_dict(track.current),
                        "after": _as_dict(track.proposed),
                    }
                    for track in changed
                ],
            }
        )
        path.write_text(
            json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return path


def _audio_files(target: Path) -> list[Path]:
    return sorted(
        path
        for path in target.rglob("*")
        if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS
    )


def _tags_from_directory(directory: Path) -> TrackTags:
    parsed = naming.parse_album_dirname(directory.name)
    artist = parsed.artist or directory.parent.name
    return TrackTags(
        artist=naming.clean_artist(repair(artist).text),
        album=repair(parsed.album).text or None,
    )


def _repair_all(tags: TrackTags) -> TrackTags:
    return TrackTags(
        artist=naming.clean_artist(repair(tags.artist).text),
        album=repair(tags.album).text or None,
        title=repair(tags.title).text or None,
        track_no=tags.track_no,
        total_tracks=tags.total_tracks,
    )


def _clean(tags: TrackTags) -> TrackTags:
    return TrackTags(
        artist=_strip(tags.artist),
        album=_strip(tags.album),
        title=_strip(tags.title),
        track_no=tags.track_no,
        total_tracks=tags.total_tracks,
    )


def _strip(value: str | None) -> str | None:
    return value.strip() or None if value else None


def _as_dict(tags: TrackTags) -> dict[str, object]:
    return {field: getattr(tags, field) for field in FIELDS}
