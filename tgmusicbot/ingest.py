"""Feature 5: a file sent to the bot lands in the library.

The whole flow lives here rather than in a handler, so it can be tested with a
plain ``BytesIO`` and no Telegram at all.  It returns events; it never returns
a sentence.

Anything the tags do not supply is asked for rather than treated as an error:
one question per missing field, the staged bytes waiting on disk in between.
"""

from __future__ import annotations

from typing import BinaryIO

from . import naming, tags as tagsmod
from .errors import AlbumUnknown, ArtistUnknown, MetadataMissing, TitleUnknown
from .events import Done, Option, Question
from .jobs import Job, JobRegistry
from .library import IngestStatus, MediaLibrary, Staged, check_extension
from .tags import TrackTags

CANCEL = Option(index=9, key="ask.cancel")
_SINGLES = "Singles"

_QUESTIONS: dict[type[MetadataMissing], tuple[str, tuple[Option, ...]]] = {
    ArtistUnknown: ("ask.artist", (CANCEL,)),
    AlbumUnknown: (
        "ask.album",
        (Option(index=0, key="ask.album.singles"), CANCEL),
    ),
    TitleUnknown: ("ask.title", (CANCEL,)),
}

_STATUS_KEYS = {
    IngestStatus.CREATED: "ingest.created",
    IngestStatus.RENAMED: "ingest.renamed",
    IngestStatus.DUPLICATE: "ingest.duplicate",
}


class IngestService:
    def __init__(self, library: MediaLibrary, registry: JobRegistry):
        self._library = library
        self._registry = registry

    # -- entry points ------------------------------------------------------

    def start(
        self,
        job: Job,
        stream: BinaryIO,
        filename: str,
        hint: TrackTags | None = None,
        *,
        max_bytes: int | None = None,
    ) -> Question | Done:
        """Stage the bytes, work out where they go, ask only about what is missing."""
        _, extension = naming.split_extension(filename)
        extension = check_extension(extension or "")

        staged = self._library.stage(stream, max_bytes=max_bytes)
        job.payload["staged"] = staged
        job.payload["extension"] = extension
        job.payload["filename"] = filename

        try:
            merged = (
                tagsmod.read_tags(staged.path)
                .merged_with(hint or TrackTags())
                .merged_with(tagsmod.tags_from_filename(filename))
            )
            return self._advance(job, merged)
        except BaseException:
            self._discard(job)
            raise

    def resume(self, job: Job, answer: str) -> Question | Done:
        """Continue after the user typed the value for the field we asked about."""
        field = job.payload.get("awaiting")
        tags: TrackTags = job.payload["tags"]  # type: ignore[assignment]
        if not field:
            return self.cancel(job)
        try:
            return self._advance(job, tags.with_(**{str(field): answer.strip()}))
        except BaseException:
            self._discard(job)
            raise

    def answer_option(self, job: Job, option: Option) -> Question | Done:
        if option.key == CANCEL.key:
            return self.cancel(job)
        if option.key == "ask.album.singles":
            return self.resume(job, _SINGLES)
        return self.cancel(job)

    def cancel(self, job: Job) -> Done:
        self._discard(job)
        self._registry.finish(job, None)
        return Done(job.id, "ask.cancelled")

    # -- internals ---------------------------------------------------------

    def _advance(self, job: Job, tags: TrackTags) -> Question | Done:
        job.payload["tags"] = tags
        try:
            tags.require_complete()
        except MetadataMissing as missing:
            return self._ask(job, missing)
        job.payload.pop("awaiting", None)
        return self._store(job, tags)

    def _ask(self, job: Job, missing: MetadataMissing) -> Question:
        key, options = _QUESTIONS[type(missing)]
        job.payload["awaiting"] = missing.field
        return self._registry.ask(
            job,
            Question(
                job_id=job.id,
                key=key,
                options=options,
                params={"filename": job.payload.get("filename", "")},
                free_text=True,
            ),
        )

    def _store(self, job: Job, tags: TrackTags) -> Done:
        staged: Staged = job.payload["staged"]  # type: ignore[assignment]
        extension: str = job.payload["extension"]  # type: ignore[assignment]
        result = self._library.ingest_staged(staged, tags, extension)
        job.payload.pop("staged", None)
        self._registry.finish(job, result)
        return Done(
            job.id,
            _STATUS_KEYS[result.status],
            {"path": str(result.path), "size": result.size},
        )

    def _discard(self, job: Job) -> None:
        staged: Staged | None = job.payload.pop("staged", None)  # type: ignore[assignment]
        if staged is not None:
            staged.discard()
