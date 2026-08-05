"""Repairing mis-decoded tag text.

Two mistakes account for nearly all of the damage in an old collection:

* cp1251 bytes read as latin-1 — ``Пинк Флойд`` arrives as ``Ïèíê Ôëîéä``
* utf-8 bytes read as cp1251 — the same text arrives as ``РџРёРЅРє``

Both are reversible, and both are *guesses*: the only safe way to apply one is
to score the candidate against the original and let the user see the diff.
The scoring below is deliberately conservative — a string that is already fine
must never be "repaired" (``Sigur Rós`` is the canonical trap: it round-trips
into the plausible-looking but wrong ``Sigur Rуs``).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

MARGIN = 0.1
"""How much better a candidate must score before it is worth proposing."""

_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)

_REPAIRS: tuple[tuple[str, str, str], ...] = (
    # label, decode-as, encode-as
    ("cp1251-as-latin1", "latin-1", "cp1251"),
    ("utf8-as-cp1251", "cp1251", "utf-8"),
    ("utf8-as-latin1", "latin-1", "utf-8"),
)


@dataclass(frozen=True, slots=True)
class Repair:
    text: str
    method: str | None = None

    @property
    def changed(self) -> bool:
        return self.method is not None


def is_cyrillic(char: str) -> bool:
    return "Ѐ" <= char <= "ӿ"


def score(text: str) -> float:
    """Higher is more plausible as human-written text.

    Three signals, all of which mojibake trips and ordinary text does not:
    letters outside ASCII-plus-Cyrillic, scripts mixed inside one word, and
    capitals in the middle of a word.
    """
    letters = [char for char in text if char.isalpha()]
    if not letters:
        return 0.0

    ascii_letters = sum(1 for char in letters if char.isascii())
    cyrillic = sum(1 for char in letters if is_cyrillic(char))
    exotic = len(letters) - ascii_letters - cyrillic

    words = _WORD.findall(text)
    mixed = sum(1 for word in words if _mixes_scripts(word))
    internal_caps = sum(_internal_caps(word) for word in words)

    return (
        (ascii_letters + cyrillic - 3 * exotic) / len(letters)
        - 0.5 * mixed
        - 2 * internal_caps / len(letters)
    )


def _mixes_scripts(word: str) -> bool:
    has_cyrillic = any(is_cyrillic(char) for char in word)
    has_latin = any(
        char.isalpha() and unicodedata.name(char, "").startswith("LATIN")
        for char in word
    )
    return has_cyrillic and has_latin


def _internal_caps(word: str) -> int:
    """Capitals after the first letter — the signature of ``РџРёРЅРє``."""
    return sum(1 for char in word[1:] if char.isupper())


def repair(text: str | None) -> Repair:
    """Best-effort fix. Returns the original unchanged when unsure."""
    if not text:
        return Repair(text or "")

    best = Repair(text)
    best_score = score(text)
    for label, wrong, right in _REPAIRS:
        candidate = _reinterpret(text, wrong, right)
        if candidate is None or candidate == text:
            continue
        candidate_score = score(candidate)
        if candidate_score > best_score + MARGIN:
            best, best_score = Repair(candidate, label), candidate_score
    return best


def _reinterpret(text: str, wrong: str, right: str) -> str | None:
    try:
        return text.encode(wrong).decode(right)
    except (UnicodeEncodeError, UnicodeDecodeError):
        return None
