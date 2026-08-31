"""Guards on the i18n boundary.

These are the tests that make deferring the wording safe: they fail the moment
the core starts producing prose, or an event references a key nobody wrote.
"""

import inspect
import pkgutil
from pathlib import Path

import pytest

import tgmusicbot
from tgmusicbot import errors, ingest
from tgmusicbot.bot import texts

CORE_DIR = Path(tgmusicbot.__file__).parent


def core_modules():
    """Every module outside ``bot/``, packages included."""
    import importlib.util

    for module in pkgutil.walk_packages([str(CORE_DIR)], prefix="tgmusicbot."):
        if module.name.startswith("tgmusicbot.bot"):
            continue
        origin = importlib.util.find_spec(module.name).origin
        if origin and origin.endswith(".py"):
            yield module.name, Path(origin)


def imported_names(path: Path) -> set[str]:
    import ast

    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names.add(module)
            names.update(f"{module}.{alias.name}" for alias in node.names)
    return names


def test_core_never_imports_the_text_catalogue():
    """If the core could reach ``texts``, localisation would leak out of ``bot/``."""
    offenders = [
        name
        for name, path in core_modules()
        if any(
            part.endswith("texts") or part.endswith("bot.handlers")
            for part in imported_names(path)
        )
    ]
    assert offenders == []


def test_the_boundary_test_actually_sees_every_core_module():
    """A guard that silently checks nothing is worse than no guard."""
    names = {name for name, _ in core_modules()}
    assert {
        "tgmusicbot.library",
        "tgmusicbot.ingest",
        "tgmusicbot.dlservice",
        "tgmusicbot.tagfix",
        "tgmusicbot.sources.youtube",
    } <= names


@pytest.mark.parametrize(
    "error_class",
    [
        value
        for value in vars(errors).values()
        if inspect.isclass(value) and issubclass(value, errors.TgMusicError)
    ],
)
def test_every_error_code_has_a_string(error_class):
    assert error_class.code in texts.CATALOGUE


def test_every_key_the_ingest_flow_can_emit_has_a_string():
    keys = set(ingest._STATUS_KEYS.values())
    for question_key, options in ingest._QUESTIONS.values():
        keys.add(question_key)
        keys.update(option.key for option in options)
    keys.add("ask.cancelled")
    assert keys <= set(texts.CATALOGUE)


def test_rendering_a_real_error_interpolates_its_params():
    error = errors.TooLarge(size=100, limit=10)
    rendered = texts.t(error.code, **error.params)
    assert "100" in rendered and "10" in rendered
    assert "{" not in rendered


def test_missing_key_and_missing_param_are_visible_not_silent():
    assert texts.t("no.such.key") == "[no.such.key]"
    assert texts.t("error.too_large", size=1) == "[error.too_large: missing limit]"


def test_catalogue_templates_are_all_formattable():
    for key, template in texts.CATALOGUE.items():
        assert "{{" not in template, key


def test_every_key_the_tag_flow_can_emit_has_a_string():
    from tgmusicbot import tagservice

    keys = {
        option.key
        for option in (
            tagservice.APPLY,
            tagservice.FROM_DIRECTORY,
            tagservice.FROM_FILENAMES,
        )
    }
    keys.update(
        {
            "ask.tags.confirm",
            "tags.nothing_to_do",
            "tags.applied",
            "tags.applied_with_failures",
            "tags.diff.header",
            "tags.diff.more",
            "cmd.tags.usage",
        }
    )
    assert keys <= set(texts.CATALOGUE)


def test_every_key_the_download_flow_can_emit_has_a_string():
    from tgmusicbot import dlservice

    keys = {
        "ask.dl.choose",
        "ask.dl.candidate",
        "dl.candidates.header",
        "dl.nothing_found",
        "cmd.dl.usage",
        dlservice.STAGE_FETCH,
    }
    assert keys <= set(texts.CATALOGUE)


def test_every_key_the_link_flow_can_emit_has_a_string():
    from tgmusicbot import dlservice

    keys = {
        "ask.dl.confirm",
        "ask.dl.specify_path",
        dlservice.SAVE.key,
        dlservice.SPECIFY.key,
        dlservice.STAGE_INSPECT,
        dlservice.STAGE_FETCH,
    }
    assert keys <= set(texts.CATALOGUE)
