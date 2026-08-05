import pytest

from tgmusicbot.encoding import repair, score

CYRILLIC = "Пинк Флойд"


@pytest.mark.parametrize(
    "broken,expected",
    [
        (CYRILLIC.encode("cp1251").decode("latin-1"), CYRILLIC),
        (CYRILLIC.encode("utf-8").decode("cp1251"), CYRILLIC),
        ("Времена года".encode("cp1251").decode("latin-1"), "Времена года"),
        ("Ария".encode("cp1251").decode("latin-1"), "Ария"),
    ],
)
def test_repairs_the_two_common_mistakes(broken, expected):
    result = repair(broken)
    assert result.text == expected
    assert result.changed


@pytest.mark.parametrize(
    "text",
    [
        "Pink Floyd",
        "Пинк Флойд",
        "Sigur Rós",  # round-trips into a plausible but wrong "Sigur Rуs"
        "Motörhead",
        "Blue Öyster Cult",
        "Beyoncé",
        "Erik Satie – Gymnopédie No.1",
        "AC/DC",
        "TIME",
        "",
        "01",
    ],
)
def test_leaves_correct_text_alone(text):
    result = repair(text)
    assert result.text == text
    assert not result.changed


def test_none_is_not_a_crash():
    assert repair(None).text == ""


def test_score_prefers_the_repaired_form():
    broken = CYRILLIC.encode("cp1251").decode("latin-1")
    assert score(broken) < score(CYRILLIC)


def test_repair_reports_which_mistake_it_undid():
    assert repair(CYRILLIC.encode("cp1251").decode("latin-1")).method == "cp1251-as-latin1"
    assert repair(CYRILLIC.encode("utf-8").decode("cp1251")).method == "utf8-as-cp1251"
