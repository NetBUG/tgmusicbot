"""``/tags`` as a job: propose, let the user pick the source of truth, apply.

Same shape as :mod:`tgmusicbot.ingest` — returns events, formats nothing.  The
proposal itself lives in the job payload, which is why the buttons only need
to carry a job id.
"""

from __future__ import annotations

from .events import Done, Option, Question
from .ingest import CANCEL
from .jobs import Job, JobRegistry
from .library import MediaLibrary
from .tagfix import Proposal, Source, TagFixer

APPLY = Option(index=0, key="ask.tags.apply")
FROM_DIRECTORY = Option(index=1, key="ask.tags.from_directory")
FROM_FILENAMES = Option(index=2, key="ask.tags.from_filenames")

_SOURCE_FOR_OPTION = {
    FROM_DIRECTORY.key: Source.DIRECTORY,
    FROM_FILENAMES.key: Source.FILENAME,
}


class TagService:
    def __init__(
        self,
        library: MediaLibrary,
        registry: JobRegistry,
        fixer: TagFixer | None = None,
    ):
        self._library = library
        self._registry = registry
        self._fixer = fixer or TagFixer()

    def start(self, job: Job, raw_path: str) -> Question | Done:
        target = self._library.resolve(raw_path)
        job.payload["target"] = target
        return self._propose(job, Source.TAGS)

    def answer_option(self, job: Job, option: Option) -> Question | Done:
        if option.key == CANCEL.key:
            self._registry.finish(job, None)
            return Done(job.id, "ask.cancelled")
        source = _SOURCE_FOR_OPTION.get(option.key)
        if source is not None:
            return self._propose(job, source)
        return self._apply(job)

    # -- internals ---------------------------------------------------------

    def _propose(self, job: Job, source: Source) -> Question | Done:
        target = job.payload["target"]
        proposal = self._fixer.propose(target, source)  # type: ignore[arg-type]
        job.payload["proposal"] = proposal

        if not proposal.changed:
            self._registry.finish(job, proposal)
            return Done(
                job.id,
                "tags.nothing_to_do",
                {"path": _relative(self._library, proposal.target)},
            )
        return self._registry.ask(job, self._question(job, proposal))

    def _apply(self, job: Job) -> Done:
        proposal: Proposal = job.payload["proposal"]  # type: ignore[assignment]
        applied = self._fixer.apply(proposal)
        self._registry.finish(job, applied)
        params: dict[str, object] = {
            "written": applied.written,
            "path": _relative(self._library, proposal.target),
        }
        if applied.failures:
            params["failed"] = len(applied.failures)
            return Done(job.id, "tags.applied_with_failures", params)
        return Done(job.id, "tags.applied", params)

    def _question(self, job: Job, proposal: Proposal) -> Question:
        return Question(
            job_id=job.id,
            key="ask.tags.confirm",
            options=(APPLY, FROM_DIRECTORY, FROM_FILENAMES, CANCEL),
            params={
                "path": _relative(self._library, proposal.target),
                "changed": len(proposal.changed),
                "total": len(proposal.tracks),
                "source": str(proposal.source),
            },
        )


def _relative(library: MediaLibrary, path) -> str:
    try:
        return str(path.relative_to(library.root))
    except ValueError:
        return str(path)
