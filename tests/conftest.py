"""Shared fixtures for the Aletheia test suite.

The central invariant of this project is that a generated script, replayed,
reproduces the target text exactly. Testing it needs a corpus wide enough to
hit the cases that break naive implementations: empty input, lone whitespace,
combining marks, astral-plane characters, and a literal U+0008 that must not be
mistaken for a backspace keystroke.

The corpus is built once from a fixed seed and shared by every test module, so
a failure is reproducible from the text alone and the several thousand
generated texts are not rebuilt per test.
"""

import random
from typing import List

import pytest

# Texts that have historically broken text-processing code, each with a name so
# a parametrised failure says which case it was rather than printing a control
# character.
EDGE_CASES = {
    "empty": "",
    "single_char": "a",
    "single_space": " ",
    "only_spaces": "     ",
    "single_tab": "\t",
    "tabs_and_spaces": " \t \t  ",
    "single_newline": "\n",
    "crlf": "\r\n",
    "lone_cr": "\r",
    "mixed_whitespace": "  \n\t \r\n  \n",
    "leading_trailing_space": "   hello   ",
    "all_punctuation": "!!!???...,,,;;;:::",
    "typographic_punctuation": "—…«»‘’“”",
    "very_long_word": "x" * 500,
    "long_pseudoword": "supercalifragilisticexpialidocious" * 12,
    "accented": "café naïve résumé Ünicöde ça va",
    "cjk": "日本語のテキストです。"
           "漢字テスト。",
    "cjk_no_spaces": "中文测试文本没有空格",
    "greek": "Το γρήγορο "
             "καφέ αλεπού",
    "cyrillic": "Съешь же ещё "
                "этих булок",
    "emoji": "hello \U0001f642\U0001f643 world \U0001f389",
    "emoji_zwj": "family \U0001f469‍\U0001f469‍\U0001f467‍\U0001f466 here",
    "combining_marks": "école à côté",
    "combining_run": "á̂̃̄" * 20,
    "rtl": "مرحبا بالعالم",
    "nbsp": "hard space here",
    # U+0008 is what the keystroke stream uses for a backspace. As *input text*
    # it is an ordinary character and must survive round-tripping.
    "literal_backspace": "a\bb\bc",
    "null_ish_controls": "a\x0bb\x0cc",
    "digits_and_symbols": "1234567890 !@#$%^&*() _+-={}|[]\\:\";'<>?,./",
    "shifted_heavy": "THE QUICK BROWN FOX JUMPS OVER THE LAZY DOG",
    "single_newline_between_words": "one\ntwo\nthree",
    "paragraphs": "First paragraph here.\n\nSecond paragraph here.\n\nThird.",
}

_LATIN_WORDS = (
    "the quick brown fox jumps over a lazy dog academic integrity depends on "
    "evidence that writing was actually composed by the person who submitted "
    "it and the process leaves traces which a finished document does not "
    "keystroke timing pauses revisions bursts detectors corpora synthetic"
).split()

_ACCENTED_WORDS = [
    "café", "naïve", "résumé", "sûr", "mañana",
    "Grüße", "élève", "île", "coût",
]

_CJK_WORDS = [
    "日本語", "漢字", "測試", "文字",
    "中文", "テキスト", "こんにちは",
]

_CYRILLIC_WORDS = [
    "привет", "мир",
    "текст", "проверка",
]

_GREEK_WORDS = [
    "αλφα", "βήτα",
    "γάμμα", "δέλτα",
]

_EMOJI = ["\U0001f642", "\U0001f389", "\U0001f9d1‍\U0001f4bb", "✨"]

_TERMINATORS = [".", ".", ".", "!", "?", ",", ";", ":", "", "", ""]
_SEPARATORS = [" ", " ", " ", " ", " ", "  ", "\n", "\t", "\r\n", "\n\n"]


def _random_text(rng: random.Random) -> str:
    """One randomised text, mixing scripts, whitespace and punctuation."""
    pools = [_LATIN_WORDS] * 6 + [
        _ACCENTED_WORDS, _CJK_WORDS, _CYRILLIC_WORDS, _GREEK_WORDS, _EMOJI
    ]
    # A few texts are drawn from a single non-Latin pool, so the suite covers
    # scripts the keyboard model has no finger mapping for.
    mixed = rng.random() < 0.75

    pieces: List[str] = []
    for position in range(rng.randint(1, 45)):
        pool = rng.choice(pools) if mixed else pools[rng.randrange(len(pools))]
        word = rng.choice(pool)
        if rng.random() < 0.04:
            # An occasional word long enough to span several bursts on its own.
            word = word * rng.randint(8, 30)
        if rng.random() < 0.05:
            word = word.upper()
        pieces.append(word + rng.choice(_TERMINATORS))
        if position:
            pieces.insert(-1, rng.choice(_SEPARATORS))

    text = "".join(pieces)
    if rng.random() < 0.08:
        text = rng.choice(_SEPARATORS) + text
    if rng.random() < 0.08:
        text = text + rng.choice(_SEPARATORS)
    return text


def build_corpus(count: int = 520, seed: int = 20240617) -> List[str]:
    """The edge cases followed by `count` randomised texts from a fixed seed."""
    rng = random.Random(seed)
    return list(EDGE_CASES.values()) + [_random_text(rng) for _ in range(count)]


# Built at import time rather than inside the fixture so that parametrised
# tests can index into it while collecting.
CORPUS = build_corpus()

# The full corpus is more text than the timing engine needs to walk on every
# run of the fast suite; SAMPLE_CORPUS keeps the edge cases and thins the
# randomised bulk.
SAMPLE_CORPUS = list(EDGE_CASES.values()) + CORPUS[len(EDGE_CASES)::6]

PROSE = (
    "Academic integrity depends on evidence that a piece of writing was "
    "actually composed by the person who submitted it, and the writing "
    "process leaves traces that a finished document does not. A keystroke "
    "log records pauses, revisions and corrections; a finished essay records "
    "none of them. That asymmetry is what makes process data useful. "
)


@pytest.fixture(scope="session")
def corpus() -> List[str]:
    """Every text in the suite: edge cases plus 520 randomised ones."""
    return CORPUS


@pytest.fixture(scope="session")
def sample_corpus() -> List[str]:
    """A thinned corpus for tests whose per-text cost is high."""
    return SAMPLE_CORPUS


@pytest.fixture(scope="session")
def prose() -> str:
    """Ordinary English prose, for tests about typing rates and rhythm."""
    return PROSE


@pytest.fixture
def long_prose() -> str:
    """Enough prose to contain many bursts, typos and revisions."""
    return (PROSE * 8).strip()
