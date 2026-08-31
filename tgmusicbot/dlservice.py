"""``/dl`` as a job: search, let the user pick, fetch, then hand over to ingest.

The metadata comes from what the user typed, not from YouTube — channel names
and video titles are far too unreliable to file a library by. Once the file is
on disk the ingest flow takes over unchanged, which is why a missing album is
still just a question rather than a special case here.
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Callable
from pathlib import Path

from . import naming
from .errors import SourceUnavailable, TgMusicError
from .events import Done, Event, Option, Progress, Question
from .ingest import CANCEL, IngestService
from .jobs import Job, JobRegistry
from .library import INCOMING_DIR, MediaLibrary
from .sources.base import Candidate, Source
from .tags import TrackTags

STAGE_FETCH = "stage.fetch"
STAGE_INSPECT = "stage.inspect"
SEPARATOR = re.compile(r"\s+[-–—]\s+")
DEFAULT_ALBUM = "Singles"

SAVE = Option(index=0, key="ask.dl.save")
SPECIFY = Option(index=1, key="ask.dl.specify")


def parse_query(text: str) -> TrackTags:
    """``"Pink Floyd - Meddle - Time"`` -> artist, album, title.

    Two parts mean artist and title; the album is then a question, not a guess.
    """
    parts = [part.strip() for part in SEPARATOR.split(text.strip()) if part.strip()]
    if not parts:
        return TrackTags()
    if len(parts) == 1:
        return TrackTags(title=parts[0])
    if len(parts) == 2:
        return TrackTags(artist=parts[0], title=parts[1])
    return TrackTags(artist=parts[0], album=parts[1], title=" - ".join(parts[2:]))


def parse_target(text: str, current: TrackTags) -> TrackTags:
    """Read a correction the user typed at the confirmation prompt.

    A path (``Pink Floyd/Meddle/Time``) or the dash form
    (``Pink Floyd - Meddle - Time``) both work; a single word is taken as the
    album, since that is the field a proposal gets wrong most often.
    """
    text = text.strip().strip("/")
    if not text:
        return current

    if "/" in text:
        parts = [part.strip() for part in text.split("/") if part.strip()]
        if len(parts) == 1:
            return current.with_(album=parts[0])
        if len(parts) == 2:
            return current.with_(artist=parts[0], album=parts[1])
        stem, _ = naming.split_extension(parts[-1])
        return current.with_(artist=parts[0], album=parts[1], title=stem)

    parsed = parse_query(text)
    if parsed.artist is None and parsed.album is None:
        return current.with_(album=parsed.title)
    return parsed.merged_with(current)


def tags_from_candidate(candidate: Candidate) -> TrackTags:
    """What the video says about itself, cleaned up.

    The channel is the fallback artist — for a "Topic" auto-upload it is
    exactly the artist name, and for anything else it is at least a real name
    rather than a guess.
    """
    parsed = parse_query(naming.clean_video_title(candidate.title))
    return TrackTags(
        artist=naming.clean_artist(parsed.artist) or naming.clean_artist(candidate.uploader),
        album=parsed.album,
        title=parsed.title,
    )


class DownloadService:
    def __init__(
        self,
        library: MediaLibrary,
        registry: JobRegistry,
        ingest: IngestService,
        source: Source,
        *,
        limit: int = 5,
        max_bytes: int | None = None,
    ):
        self._library = library
        self._registry = registry
        self._ingest = ingest
        self._source = source
        self._limit = limit
        self._max_bytes = max_bytes

    def start(self, job: Job, query: str) -> Question | Done:
        hint = parse_query(query)
        job.payload["query"] = query
        job.payload["hint"] = hint

        candidates = self._source.search(query, self._limit)
        if not candidates:
            self._registry.finish(job, None)
            return Done(job.id, "dl.nothing_found", {"query": query})

        job.payload["candidates"] = candidates
        return self._registry.ask(job, self._question(job, candidates))

    def start_url(self, job: Job, url: str) -> Question | Done:
        """A bare link is treated like a sent file: propose a path, then ask."""
        candidate = self._source.inspect(url)  # type: ignore[attr-defined]
        job.payload["candidate"] = candidate
        job.payload["query"] = url
        job.payload["hint"] = tags_from_candidate(candidate)
        return self._confirm(job)

    def resume(
        self,
        job: Job,
        answer: str,
        emit: Callable[[Event], None] | None = None,
    ) -> Question | Done:
        """A typed reply: either the corrected path, or an ingest answer."""
        if job.payload.pop("awaiting_path", False):
            hint: TrackTags = job.payload["hint"]  # type: ignore[assignment]
            job.payload["hint"] = parse_target(answer, hint)
            return self._start_fetch(job, emit)
        return self._ingest.resume(job, answer)

    def answer_option(
        self,
        job: Job,
        option: Option,
        emit: Callable[[Event], None] | None = None,
    ) -> Question | Done:
        # past the fetch, the pending questions belong to the ingest flow
        if "staged" in job.payload or job.payload.get("awaiting"):
            return self._ingest.answer_option(job, option)
        if option.key == CANCEL.key:
            self._registry.finish(job, None)
            return Done(job.id, "ask.cancelled")

        if "candidate" in job.payload:
            if option.key == SPECIFY.key:
                return self._ask_for_path(job)
            return self._start_fetch(job, emit)

        candidates: list[Candidate] = job.payload["candidates"]  # type: ignore[assignment]
        if not 0 <= option.index < len(candidates):
            return Done(job.id, "ask.cancelled")
        return self._fetch(job, candidates[option.index], emit)

    # -- internals ---------------------------------------------------------

    def _confirm(self, job: Job) -> Question:
        candidate: Candidate = job.payload["candidate"]  # type: ignore[assignment]
        hint: TrackTags = job.payload["hint"]  # type: ignore[assignment]
        if not hint.album:
            hint = hint.with_(album=DEFAULT_ALBUM)
            job.payload["hint"] = hint
        return self._registry.ask(
            job,
            Question(
                job_id=job.id,
                key="ask.dl.confirm",
                options=(SAVE, SPECIFY, CANCEL),
                params={
                    "path": self._preview_path(hint, candidate),
                    "title": candidate.title,
                    "uploader": candidate.uploader or "",
                },
                free_text=True,
            ),
        )

    def _preview_path(self, tags: TrackTags, candidate: Candidate) -> str:
        """Where it would land, as the user would type it. Never raises."""
        try:
            path = self._library.track_path(tags, candidate.extension or ".m4a")
        except TgMusicError:
            return "?"
        shown = path.relative_to(self._library.root)
        return str(shown if candidate.extension else shown.with_suffix(""))

    def _ask_for_path(self, job: Job) -> Question:
        job.payload["awaiting_path"] = True
        return self._registry.ask(
            job,
            Question(
                job_id=job.id,
                key="ask.dl.specify_path",
                options=(CANCEL,),
                params={"path": self._preview_path(
                    job.payload["hint"],  # type: ignore[arg-type]
                    job.payload["candidate"],  # type: ignore[arg-type]
                )},
                free_text=True,
            ),
        )

    def _start_fetch(
        self, job: Job, emit: Callable[[Event], None] | None
    ) -> Question | Done:
        candidate: Candidate = job.payload["candidate"]  # type: ignore[assignment]
        return self._fetch(job, candidate, emit)

    def _fetch(
        self,
        job: Job,
        candidate: Candidate,
        emit: Callable[[Event], None] | None,
    ) -> Question | Done:
        def report(done: int, total: int | None) -> None:
            if emit is not None:
                emit(Progress(job.id, STAGE_FETCH, done, total))

        report(0, None)
        workdir = workdir_for(self._library.root, job.id)
        try:
            fetched = self._source.fetch(candidate, workdir, report)
            hint: TrackTags = job.payload["hint"]  # type: ignore[assignment]
            with fetched.path.open("rb") as handle:
                return self._ingest.start(
                    job,
                    handle,
                    fetched.path.name,
                    hint,
                    max_bytes=self._max_bytes,
                    prefer_hint=True,
                )
        except SourceUnavailable:
            self._registry.fail(job, None)
            raise
        finally:
            # ingest.start() has already copied the bytes into staging
            shutil.rmtree(workdir, ignore_errors=True)

    def _question(self, job: Job, candidates: list[Candidate]) -> Question:
        options = tuple(
            Option(
                index=index,
                key="ask.dl.candidate",
                params={"index": index + 1, "title": candidate.title},
            )
            for index, candidate in enumerate(candidates)
        ) + (CANCEL,)
        return Question(
            job_id=job.id,
            key="ask.dl.choose",
            options=options,
            params={"query": job.payload.get("query", "")},
        )


def workdir_for(root: Path, job_id: str) -> Path:
    """Downloads land in the library's own staging area, never in /tmp: the
    library may be a 7 TB mdraid while /tmp is a small root filesystem."""
    return root / INCOMING_DIR / f"dl-{job_id}"
