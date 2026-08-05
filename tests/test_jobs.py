import pytest

from tgmusicbot.errors import JobUnknown, TgMusicError
from tgmusicbot.events import Done, Failed, Option, Progress, Question
from tgmusicbot.jobs import (
    CALLBACK_LIMIT,
    JobRegistry,
    JobState,
    WorkerPool,
    encode_callback,
    parse_callback,
)


class ManualClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


@pytest.fixture
def registry():
    return JobRegistry(clock=ManualClock(), ttl_s=100.0)


def test_ids_are_short_enough_for_a_button(registry):
    for _ in range(2000):
        job = registry.create("ingest", 1, 1)
    data = encode_callback("ans", job.id, 3)
    assert len(data.encode()) <= CALLBACK_LIMIT
    assert len(job.id) <= 3


def test_callback_roundtrip():
    assert parse_callback(encode_callback("ans", "1f", 2)) == parse_callback("ans:1f:2")
    callback = parse_callback("ans:1f:2")
    assert (callback.action, callback.job_id, callback.option) == ("ans", "1f", 2)
    assert parse_callback("cancel:1f").option is None


def test_callback_rejects_oversized_payload():
    """The 64-byte cap is why state is addressed by id and not by text."""
    with pytest.raises(ValueError, match="exceeds"):
        encode_callback("ans", "x" * 70)
    with pytest.raises(ValueError):
        encode_callback("a:b", "1")
    with pytest.raises(ValueError):
        parse_callback("garbage")


def test_question_answer_cycle(registry):
    job = registry.create("ingest", 10, 20)
    question = Question(job.id, "ask.album", (Option(0, "ask.album.singles"),))
    registry.ask(job, question)
    assert job.state is JobState.WAITING

    answered, option = registry.answer(job.id, 0)
    assert answered is job and option.key == "ask.album.singles"
    assert job.question is None and job.state is JobState.PENDING

    with pytest.raises(JobUnknown):
        registry.answer(job.id, 0)  # already answered


def test_unknown_job_and_option(registry):
    with pytest.raises(JobUnknown):
        registry.get("nope")
    job = registry.create("ingest", 1, 1)
    registry.ask(job, Question(job.id, "ask.album", (Option(0, "k"),)))
    with pytest.raises(JobUnknown):
        registry.answer(job.id, 7)


def test_free_text_answer_targets_the_latest_waiting_job(registry):
    old = registry.create("ingest", 5, 1)
    registry.ask(old, Question(old.id, "ask.album", (), free_text=True))
    registry._clock.now = 50.0
    new = registry.create("ingest", 5, 1)
    registry.ask(new, Question(new.id, "ask.album", (), free_text=True))

    assert registry.waiting_for_text(5) is new
    assert registry.waiting_for_text(999) is None

    registry.answer_text(new.id, "Meddle")
    assert new.payload["answer"] == "Meddle"
    assert registry.waiting_for_text(5) is old


def test_free_text_rejected_when_question_does_not_allow_it(registry):
    job = registry.create("ingest", 1, 1)
    registry.ask(job, Question(job.id, "ask.album", (), free_text=False))
    with pytest.raises(JobUnknown):
        registry.answer_text(job.id, "x")


def test_purge_keeps_unsettled_jobs(registry):
    settled = registry.create("ingest", 1, 1)
    waiting = registry.create("ingest", 1, 1)
    registry.finish(settled, None)
    registry.ask(waiting, Question(waiting.id, "ask.album", ()))

    registry._clock.now = 1000.0
    assert registry.purge() == 1
    assert len(registry) == 1
    assert registry.get(waiting.id) is waiting


def collect(pool_events):
    return [type(event).__name__ for event in pool_events]


def run_pool(registry, task):
    events = []
    pool = WorkerPool(registry, events.append, workers=1)
    pool.start()
    job = registry.create("ingest", 1, 1)
    pool.submit(job, task)
    pool.join()
    pool.stop(timeout=1)
    return job, events


def test_pool_wraps_a_plain_return_in_done(registry):
    job, events = run_pool(registry, lambda job, emit: "whatever")
    assert collect(events) == ["Done"]
    assert job.state is JobState.DONE


def test_pool_forwards_progress_and_question(registry):
    def task(job, emit):
        emit(Progress(job.id, "download", 1, 2))
        return Question(job.id, "ask.album", (Option(0, "ask.album.singles"),))

    job, events = run_pool(registry, task)
    assert collect(events) == ["Progress", "Question"]
    assert job.state is JobState.WAITING


def test_pool_turns_a_domain_error_into_a_failed_event(registry):
    class Boom(TgMusicError):
        code = "error.too_large"

    job, events = run_pool(registry, lambda job, emit: (_ for _ in ()).throw(Boom()))
    assert collect(events) == ["Failed"]
    assert events[0].code == "error.too_large"
    assert job.state is JobState.FAILED


def test_pool_survives_an_unexpected_exception(registry):
    job, events = run_pool(registry, lambda job, emit: 1 / 0)
    assert isinstance(events[0], Failed)
    assert events[0].code == "error.generic"


def test_pool_survives_a_broken_renderer(registry):
    def explode(event):
        raise RuntimeError("renderer is down")

    pool = WorkerPool(registry, explode, workers=1)
    pool.start()
    job = registry.create("ingest", 1, 1)
    pool.submit(job, lambda j, emit: Done(j.id, "done.generic"))
    pool.join()
    pool.stop(timeout=1)
    assert job.state is JobState.DONE
