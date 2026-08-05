"""Job registry, callback codec and worker pool.

Telegram caps ``callback_data`` at 64 bytes, so a button can never carry a
track title, a magnet link or a filesystem path.  Every interactive flow
therefore addresses state by a short job id allocated here, and the payload
lives in the registry.  Long work runs on the pool so polling is never blocked
by a download (that was bug 11 in the audit).
"""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

from .errors import JobUnknown, TgMusicError
from .events import Done, Event, Failed, Option, Progress, Question

CALLBACK_LIMIT = 64
_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"


class JobState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING = "waiting"
    DONE = "done"
    FAILED = "failed"


@dataclass(slots=True)
class Job:
    id: str
    kind: str
    chat_id: int
    user_id: int
    created_at: float
    payload: dict[str, object] = field(default_factory=dict)
    state: JobState = JobState.PENDING
    question: Question | None = None
    result: object | None = None
    message_id: int | None = None
    """Telegram message being edited with progress; owned by the bot layer."""


@dataclass(frozen=True, slots=True)
class Callback:
    action: str
    job_id: str
    option: int | None = None


def encode_callback(action: str, job_id: str, option: int | None = None) -> str:
    if ":" in action or ":" in job_id:
        raise ValueError("action and job_id must not contain ':'")
    data = f"{action}:{job_id}" if option is None else f"{action}:{job_id}:{option}"
    if len(data.encode("utf-8")) > CALLBACK_LIMIT:
        raise ValueError(f"callback_data exceeds {CALLBACK_LIMIT} bytes: {data!r}")
    return data


def parse_callback(data: str) -> Callback:
    parts = data.split(":")
    if len(parts) == 2:
        return Callback(parts[0], parts[1])
    if len(parts) == 3:
        try:
            return Callback(parts[0], parts[1], int(parts[2]))
        except ValueError:
            raise ValueError(f"bad option index in {data!r}") from None
    raise ValueError(f"malformed callback_data: {data!r}")


class JobRegistry:
    """In-memory store of live jobs, keyed by a short id fit for a button."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        ttl_s: float = 3600.0,
    ):
        self._clock = clock
        self._ttl_s = ttl_s
        self._jobs: dict[str, Job] = {}
        self._counter = 0
        self._lock = threading.RLock()

    def create(
        self, kind: str, chat_id: int, user_id: int, **payload: object
    ) -> Job:
        with self._lock:
            self.purge()
            self._counter += 1
            job = Job(
                id=_base36(self._counter),
                kind=kind,
                chat_id=chat_id,
                user_id=user_id,
                created_at=self._clock(),
                payload=dict(payload),
            )
            self._jobs[job.id] = job
            return job

    def get(self, job_id: str) -> Job:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise JobUnknown(job_id)
            return job

    def ask(self, job: Job, question: Question) -> Question:
        with self._lock:
            job.state = JobState.WAITING
            job.question = question
            return question

    def answer(self, job_id: str, option_index: int) -> tuple[Job, Option]:
        with self._lock:
            job = self.get(job_id)
            if job.question is None:
                raise JobUnknown(job_id)
            for option in job.question.options:
                if option.index == option_index:
                    job.question = None
                    job.state = JobState.PENDING
                    return job, option
            raise JobUnknown(job_id)

    def answer_text(self, job_id: str, text: str) -> Job:
        with self._lock:
            job = self.get(job_id)
            if job.question is None or not job.question.free_text:
                raise JobUnknown(job_id)
            job.payload["answer"] = text
            job.question = None
            job.state = JobState.PENDING
            return job

    def waiting_for_text(self, chat_id: int) -> Job | None:
        """The most recent job in this chat that accepts a plain-text reply."""
        with self._lock:
            candidates = [
                job
                for job in self._jobs.values()
                if job.chat_id == chat_id
                and job.state is JobState.WAITING
                and job.question is not None
                and job.question.free_text
            ]
            return max(candidates, key=lambda j: j.created_at, default=None)

    def finish(self, job: Job, result: object = None) -> None:
        with self._lock:
            job.state = JobState.DONE
            job.result = result

    def fail(self, job: Job, error: object = None) -> None:
        with self._lock:
            job.state = JobState.FAILED
            job.result = error

    def purge(self) -> int:
        """Drop settled jobs older than the TTL. Running jobs are kept."""
        now = self._clock()
        with self._lock:
            expired = [
                job_id
                for job_id, job in self._jobs.items()
                if now - job.created_at > self._ttl_s
                and job.state in (JobState.DONE, JobState.FAILED)
            ]
            for job_id in expired:
                del self._jobs[job_id]
            return len(expired)

    def __len__(self) -> int:
        with self._lock:
            return len(self._jobs)


def _base36(value: int) -> str:
    if value == 0:
        return "0"
    digits: list[str] = []
    while value:
        value, remainder = divmod(value, 36)
        digits.append(_ALPHABET[remainder])
    return "".join(reversed(digits))


Task = Callable[[Job, Callable[[Event], None]], object]


class WorkerPool:
    """Fixed thread pool. Handlers submit here instead of blocking polling."""

    def __init__(
        self,
        registry: JobRegistry,
        on_event: Callable[[Event], None],
        *,
        workers: int = 2,
    ):
        self._registry = registry
        self._on_event = on_event
        self._queue: queue.Queue[tuple[Job, Task] | None] = queue.Queue()
        self._threads = [
            threading.Thread(target=self._run, name=f"worker-{i}", daemon=True)
            for i in range(workers)
        ]
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        for thread in self._threads:
            thread.start()

    def submit(self, job: Job, task: Task) -> None:
        self._queue.put((job, task))

    def stop(self, timeout: float | None = 5.0) -> None:
        for _ in self._threads:
            self._queue.put(None)
        for thread in self._threads:
            if thread.is_alive():
                thread.join(timeout)
        self._started = False

    def join(self) -> None:
        """Block until the queue is drained (tests)."""
        self._queue.join()

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                self._queue.task_done()
                return
            job, task = item
            try:
                self._execute(job, task)
            finally:
                self._queue.task_done()

    def _execute(self, job: Job, task: Task) -> None:
        job.state = JobState.RUNNING
        try:
            result = task(job, self._emit)
        except TgMusicError as error:
            self._registry.fail(job, error)
            self._emit(Failed(job.id, error.code, error.params))
        except Exception as error:  # noqa: BLE001 — a worker must never die
            self._registry.fail(job, error)
            self._emit(Failed(job.id, "error.generic", {"reason": str(error)}))
        else:
            if isinstance(result, (Question, Progress)):
                if isinstance(result, Question):
                    self._registry.ask(job, result)
                self._emit(result)
                return
            if isinstance(result, (Done, Failed)):
                self._registry.finish(job, result)
                self._emit(result)
                return
            self._registry.finish(job, result)
            self._emit(Done(job.id, "done.generic"))

    def _emit(self, event: Event) -> None:
        try:
            self._on_event(event)
        except Exception:  # noqa: BLE001 — a broken renderer must not kill the job
            pass
