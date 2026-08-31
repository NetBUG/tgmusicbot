"""The contract every download source implements.

Deliberately narrow: search returns candidates, fetch puts one of them on disk
and reports progress. Rutracker (Phase 4) is a producer of magnets rather than
files, so it will implement ``search`` and hand ``fetch`` to the torrent
client — the shape still holds.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

ProgressCallback = Callable[[int, int | None], None]


@dataclass(frozen=True, slots=True)
class Candidate:
    id: str
    title: str
    url: str
    source: str
    uploader: str | None = None
    duration_s: int | None = None
    extension: str | None = None
    """What ``fetch`` would produce, when the source can say in advance."""


@dataclass(frozen=True, slots=True)
class Fetched:
    path: Path
    candidate: Candidate


@runtime_checkable
class Source(Protocol):
    name: str

    def search(self, query: str, limit: int = 5) -> list[Candidate]: ...

    def fetch(
        self,
        candidate: Candidate,
        destination: Path,
        on_progress: ProgressCallback | None = None,
    ) -> Fetched: ...
