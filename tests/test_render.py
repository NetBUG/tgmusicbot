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


def test_progress_without_a_total_shows_no_bogus_percentage():
    """``download… ?%`` was the old output when the size was unknown."""
    rendered = render.render(Progress("1", "stage.download"))
    assert rendered == "Downloading…"
    assert "%" not in rendered


def test_progress_with_a_total_shows_a_bar_and_a_size():
    rendered = render.render(Progress("1", "stage.download", 512 * 1024, 1024 * 1024))
    assert "50%" in rendered
    assert "1.0 MB" in rendered
    assert render.BAR_FULL in rendered and render.BAR_EMPTY in rendered


def test_progress_stage_is_a_catalogue_key_not_an_internal_word():
    assert "[stage.download]" not in render.render(Progress("1", "stage.download"))


@pytest.mark.parametrize(
    "fraction,filled",
    [(0.0, 0), (0.5, 6), (1.0, 12), (-1.0, 0), (2.0, 12)],
)
def test_bar_never_overflows(fraction, filled):
    assert render.bar(fraction).count(render.BAR_FULL) == filled
    assert len(render.bar(fraction)) == render.BAR_WIDTH


@pytest.mark.parametrize(
    "size,expected",
    [(None, "?"), (0, "?"), (512, "512 B"), (1536, "1.5 KB"), (5 * 1024**2, "5.0 MB")],
)
def test_human_size(size, expected):
    assert render.human_size(size) == expected


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


def make_proposal(tmp_path, changes=3):
    from tgmusicbot.tagfix import Proposal, Source, TrackProposal
    from tgmusicbot.tags import TrackTags

    tracks = tuple(
        TrackProposal(
            path=tmp_path / f"{i:02d} - Song.flac",
            current=TrackTags(artist="Ïèíê", title=None),
            proposed=TrackTags(artist="Пинк", title=f"Song {i}"),
        )
        for i in range(1, changes + 1)
    )
    return Proposal(target=tmp_path / "Meddle", source=Source.TAGS, tracks=tracks)


def test_proposal_table_shows_before_and_after(tmp_path):
    table = render.proposal_table(make_proposal(tmp_path, 1))
    assert "Ïèíê → <b>Пинк</b>" in table
    assert "— → <b>Song 1</b>" in table  # an empty field reads as a dash
    assert "01 - Song.flac" in table


def test_proposal_table_truncates_instead_of_hitting_the_4096_limit(tmp_path):
    table = render.proposal_table(make_proposal(tmp_path, 40), limit=5)
    assert "and 35 more" in table
    assert len(table) < 4096


def test_proposal_table_escapes_filenames(tmp_path):
    proposal = make_proposal(tmp_path, 1)
    evil = proposal.tracks[0].path.with_name("<script>.flac")
    object.__setattr__(proposal.tracks[0], "path", evil)
    assert "&lt;script&gt;.flac" in render.proposal_table(proposal)
