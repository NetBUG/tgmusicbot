import pytest

from tgmusicbot.bot import render
from tgmusicbot.events import Done, Failed, Option, Progress, Question


class ManualClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_paths_with_markdown_characters_are_escaped():
    """Bug 8: Markdown plus a manual ``_`` replace broke on ``*``, ``[`` and ``` ` ```."""
    event = Done("1", "ingest.created", {"path": "/m/AC-DC/Back*In[Black]/01 - a_b.mp3"})
    rendered = render.render(event)
    assert "AC-DC/Back*In[Black]/01 - a_b.mp3" in rendered
    assert "<code>" in rendered


def test_html_in_a_filename_cannot_inject_markup():
    event = Done("1", "ingest.created", {"path": "<b>x</b>"})
    assert "&lt;b&gt;x&lt;/b&gt;" in render.render(event)


def test_progress_without_a_total():
    assert "?" in render.render(Progress("1", "download"))
    assert "50" in render.render(Progress("1", "download", 1, 2))


def test_failed_renders_its_error_code():
    assert render.render(Failed("1", "error.job_unknown")) != "[error.job_unknown]"


def test_unknown_event_type_is_a_bug_not_a_message():
    with pytest.raises(TypeError):
        render.render(object())


def test_throttle_limits_edits_but_lets_the_first_through():
    clock = ManualClock()
    throttle = render.ProgressThrottle(3.0, clock=clock)
    assert throttle.should_send("j1") is True
    assert throttle.should_send("j1") is False
    clock.now = 2.9
    assert throttle.should_send("j1") is False
    clock.now = 3.0
    assert throttle.should_send("j1") is True


def test_throttle_is_per_job_and_forgettable():
    clock = ManualClock()
    throttle = render.ProgressThrottle(3.0, clock=clock)
    assert throttle.should_send("j1") is True
    assert throttle.should_send("j2") is True
    assert throttle.should_send("j1", force=True) is True
    throttle.forget("j1")
    assert throttle.should_send("j1") is True


def test_keyboard_payloads_are_ids_not_labels():
    question = Question("1f", "ask.album", (Option(0, "ask.album.singles"),))
    markup = render.keyboard(question)
    button = markup.keyboard[0][0]
    assert button.callback_data == "ans:1f:0"
    assert button.text == "Singles"
