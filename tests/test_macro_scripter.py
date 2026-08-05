"""Tests for macro_scripter.

The module has one invariant that everything downstream depends on:
`replay(generate_script(text)) == text`. Most of what follows exists to attack
it from a different angle - unusual characters, forced typos, forced revisions,
forced session gaps - because a script that loses a character is worse than one
that fails loudly.
"""

import math
import random
import statistics

import pytest

import macro_scripter as ms
from macro_scripter import (
    MacroScripter,
    ScriptEvent,
    replay,
    tokenize,
)

from conftest import CORPUS, EDGE_CASES

# Seeds used wherever a test needs several independent runs. Fixed, so a
# failure is always reproducible.
SEEDS = (0, 1, 7, 42, 12345)


@pytest.fixture
def scripter() -> MacroScripter:
    return MacroScripter(seed=20240617)


# --- the central invariant ---------------------------------------------------


@pytest.mark.parametrize("name", sorted(EDGE_CASES))
@pytest.mark.parametrize("seed", (0, 3))
def test_replay_reproduces_edge_case(name, seed):
    text = EDGE_CASES[name]
    script = MacroScripter(seed=seed).generate_script(text)
    assert replay(script) == text


def test_corpus_is_large_enough(corpus):
    # The invariant below is only convincing over a wide corpus, so the size is
    # asserted rather than assumed.
    assert len(corpus) >= 500


def test_replay_reproduces_corpus(corpus):
    scripter = MacroScripter(seed=99)
    for index, text in enumerate(corpus):
        script = scripter.generate_script(text)
        assert replay(script) == text, f"corpus[{index}] failed: {text!r}"


@pytest.mark.parametrize(
    "typo_rate,r_burst_probability",
    [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (1.0, 1.0), (0.5, 0.5)],
)
def test_replay_holds_at_parameter_extremes(
    corpus, typo_rate, r_burst_probability
):
    """Turning typos and revisions to their limits must not lose a character."""
    scripter = MacroScripter(
        seed=5,
        typo_rate=typo_rate,
        r_burst_probability=r_burst_probability,
        session_chars=40,
    )
    for text in corpus[::7]:
        assert replay(scripter.generate_script(text)) == text


def test_verify_script_agrees_with_replay(corpus, scripter):
    for text in corpus[::11]:
        script = scripter.generate_script(text)
        assert scripter.verify_script(text, script)


def test_verify_script_is_false_for_a_corrupted_script(scripter):
    script = scripter.generate_script("hello world")
    script.append(ScriptEvent(ms.OP_TYPE, char="!"))
    assert not scripter.verify_script("hello world", script)


def test_verify_script_is_false_when_replay_raises(scripter):
    # verify_script swallows the ValueError rather than propagating it, which
    # is the difference between "this script is wrong" and "this script is
    # malformed" - callers of a boolean predicate want the former.
    assert not scripter.verify_script("hi", [ScriptEvent(ms.OP_DELETE, count=9)])


# --- tokenize ----------------------------------------------------------------


def test_tokenize_roundtrips_corpus(corpus):
    for index, text in enumerate(corpus):
        assert "".join(tokenize(text)) == text, f"corpus[{index}]"


@pytest.mark.parametrize("name", sorted(EDGE_CASES))
def test_tokenize_roundtrips_edge_case(name):
    text = EDGE_CASES[name]
    assert "".join(tokenize(text)) == text


def test_tokenize_emits_each_whitespace_character_separately():
    assert tokenize("a \t\nb") == ["a", " ", "\t", "\n", "b"]


def test_tokenize_groups_non_whitespace_runs():
    assert tokenize("hello, world!") == ["hello,", " ", "world!"]


def test_tokenize_of_empty_text_is_empty():
    assert tokenize("") == []


def test_tokenize_never_produces_empty_tokens(corpus):
    for text in corpus:
        assert all(token for token in tokenize(text))


# --- replay error handling ---------------------------------------------------


def test_replay_rejects_delete_larger_than_buffer():
    script = [ScriptEvent(ms.OP_TYPE, char="a"), ScriptEvent(ms.OP_DELETE, count=2)]
    with pytest.raises(ValueError, match="exceeds buffer length"):
        replay(script)


def test_replay_rejects_delete_on_an_empty_buffer():
    with pytest.raises(ValueError, match="exceeds buffer length"):
        replay([ScriptEvent(ms.OP_DELETE, count=1)])


def test_replay_rejects_negative_delete():
    with pytest.raises(ValueError, match="must be >= 0"):
        replay([ScriptEvent(ms.OP_DELETE, count=-1)])


def test_replay_rejects_type_without_char():
    with pytest.raises(ValueError, match="TYPE event has no char"):
        replay([ScriptEvent(ms.OP_TYPE)])


def test_replay_ignores_pauses_and_gaps():
    script = [
        ScriptEvent(ms.OP_PAUSE, duration_ms=500.0),
        ScriptEvent(ms.OP_TYPE, char="h"),
        ScriptEvent(ms.OP_SESSION_GAP, duration_ms=3_600_000.0),
        ScriptEvent(ms.OP_TYPE, char="i"),
    ]
    assert replay(script) == "hi"


def test_delete_of_zero_is_a_no_op():
    script = [ScriptEvent(ms.OP_TYPE, char="a"), ScriptEvent(ms.OP_DELETE, count=0)]
    assert replay(script) == "a"


# --- ScriptEvent -------------------------------------------------------------


def test_to_dict_carries_only_the_meaningful_payload():
    assert ScriptEvent(ms.OP_TYPE, char="x").to_dict() == {
        "op": ms.OP_TYPE, "role": ms.ROLE_TEXT, "char": "x",
    }
    assert ScriptEvent(ms.OP_DELETE, count=3).to_dict() == {
        "op": ms.OP_DELETE, "role": ms.ROLE_TEXT, "count": 3,
    }
    assert ScriptEvent(ms.OP_PAUSE, duration_ms=1.23456).to_dict() == {
        "op": ms.OP_PAUSE, "role": ms.ROLE_TEXT, "duration_ms": 1.235,
    }
    assert ScriptEvent(ms.OP_SESSION_GAP, duration_ms=10.0).to_dict() == {
        "op": ms.OP_SESSION_GAP, "role": ms.ROLE_TEXT, "duration_ms": 10.0,
    }


# --- determinism and RNG isolation -------------------------------------------


@pytest.mark.parametrize("seed", SEEDS)
def test_same_seed_gives_an_identical_script(seed, long_prose):
    first = MacroScripter(seed=seed).generate_script(long_prose)
    second = MacroScripter(seed=seed).generate_script(long_prose)
    assert first == second


def test_different_seeds_give_different_scripts(long_prose):
    scripts = [
        MacroScripter(seed=seed).generate_script(long_prose) for seed in SEEDS
    ]
    for index, other in enumerate(scripts[1:], start=1):
        assert other != scripts[0], f"seed {SEEDS[index]} matched seed {SEEDS[0]}"


def test_interleaving_another_scripter_does_not_change_the_output(long_prose):
    baseline = MacroScripter(seed=7).generate_script(long_prose)

    subject = MacroScripter(seed=7)
    noise = MacroScripter(seed=8)
    noise.generate_script(long_prose)
    assert subject.generate_script(long_prose) == baseline


def test_churning_the_global_rng_does_not_change_the_output(long_prose):
    baseline = MacroScripter(seed=7).generate_script(long_prose)

    random.seed(1)
    for _ in range(1000):
        random.random()
    subject = MacroScripter(seed=7)
    random.seed(2)
    for _ in range(500):
        random.gauss(0.0, 1.0)

    assert subject.generate_script(long_prose) == baseline


def test_generating_does_not_disturb_the_global_rng(long_prose):
    random.seed(4242)
    before = random.getstate()
    MacroScripter(seed=7).generate_script(long_prose)
    assert random.getstate() == before


# --- pause distributions -----------------------------------------------------

# The asymptotic standard error of a sample median from a lognormal is
# median * sigma * 1.2533 / sqrt(n). At n = 200000 and the widest sigma here
# (0.7) that is 0.20% of the median, so the 2% tolerance below is ten standard
# errors and the test is not a coin flip. Measured deviations at this seed are
# all under 0.4%.
PAUSE_SAMPLES = 200_000
PAUSE_MEDIAN_TOLERANCE = 0.02


@pytest.mark.parametrize(
    "context,parameters",
    [
        ("word", ms.PAUSE_WORD),
        ("clause", ms.PAUSE_CLAUSE),
        ("sentence", ms.PAUSE_SENTENCE),
        ("paragraph", ms.PAUSE_PARAGRAPH),
    ],
)
def test_pause_median_matches_the_lognormal_median(context, parameters):
    mu, _sigma = parameters
    sampler = MacroScripter(seed=20240617)
    draws = [sampler._pause_ms(context) for _ in range(PAUSE_SAMPLES)]

    expected = math.exp(mu)
    observed = statistics.median(draws)
    assert observed == pytest.approx(expected, rel=PAUSE_MEDIAN_TOLERANCE)


def test_documented_pause_medians_are_the_ones_in_the_constants():
    # The module comment names 90/181/493/1097ms; if a constant is retuned the
    # comment has to move with it.
    expected = [90, 181, 493, 1097]
    actual = [
        round(math.exp(mu))
        for mu, _ in (
            ms.PAUSE_WORD, ms.PAUSE_CLAUSE, ms.PAUSE_SENTENCE, ms.PAUSE_PARAGRAPH
        )
    ]
    assert actual == expected


def test_pauses_are_ordered_by_syntactic_boundary():
    for smaller, larger in (
        (ms.PAUSE_WORD, ms.PAUSE_CLAUSE),
        (ms.PAUSE_CLAUSE, ms.PAUSE_SENTENCE),
        (ms.PAUSE_SENTENCE, ms.PAUSE_PARAGRAPH),
    ):
        assert smaller[0] < larger[0]


@pytest.mark.parametrize(
    "token,expected",
    [
        ("word", "word"),
        ("end.", "sentence"),
        ('end."', "sentence"),
        ("end!)", "sentence"),
        ("clause,", "clause"),
        ("clause;", "clause"),
        ("", "word"),
        ("word-", "word"),
    ],
)
def test_context_after_classifies_the_terminator(token, expected):
    assert MacroScripter._context_after(token) == expected


# --- bursts, typos and revisions ---------------------------------------------


def test_r_burst_probability_one_produces_revisions(long_prose):
    script = MacroScripter(
        seed=3, r_burst_probability=1.0, typo_rate=0.0
    ).generate_script(long_prose)

    deletes = [
        e for e in script
        if e.op == ms.OP_DELETE and e.role == ms.ROLE_REVISION_DELETE
    ]
    retypes = [e for e in script if e.role == ms.ROLE_REVISION_RETYPE]
    assert deletes, "no revision deletes with r_burst_probability=1.0"
    # Every deleted character is retyped, or the replay invariant could not hold.
    assert sum(e.count for e in deletes) == sum(
        1 for e in retypes if e.op == ms.OP_TYPE
    )
    assert replay(script) == long_prose


def test_no_deletions_without_typos_or_revisions(long_prose):
    script = MacroScripter(
        seed=3, typo_rate=0.0, r_burst_probability=0.0
    ).generate_script(long_prose)

    assert [e for e in script if e.op == ms.OP_DELETE] == []
    assert all(e.role == ms.ROLE_TEXT for e in script)


def test_typos_appear_as_a_four_event_correction_sequence(long_prose):
    script = MacroScripter(
        seed=11, typo_rate=0.35, r_burst_probability=0.0
    ).generate_script(long_prose)

    typo_positions = [
        i for i, e in enumerate(script)
        if e.op == ms.OP_TYPE and e.role == ms.ROLE_TYPO
    ]
    assert len(typo_positions) > 20, "not enough typos to test the sequence"

    for position in typo_positions:
        typed, pause, delete, retyped = script[position:position + 4]

        assert pause.op == ms.OP_PAUSE and pause.role == ms.ROLE_TYPO
        assert (
            ms.TYPO_REACTION_RANGE[0]
            <= pause.duration_ms
            <= ms.TYPO_REACTION_RANGE[1]
        )
        assert delete.op == ms.OP_DELETE
        assert delete.count == 1 and delete.role == ms.ROLE_CORRECTION
        assert retyped.op == ms.OP_TYPE and retyped.role == ms.ROLE_CORRECTION

        # A typo is a neighbouring key, and never the character it replaces.
        assert typed.char != retyped.char
        neighbours = ms.NEIGHBOR_KEYS[retyped.char.lower()]
        assert typed.char.lower() in neighbours
        assert typed.char.isupper() == retyped.char.isupper()


def test_typos_only_happen_on_keys_the_neighbour_table_knows():
    # Every typo has to come out of NEIGHBOR_KEYS, so a CJK or emoji character
    # can never acquire one.
    script = MacroScripter(seed=2, typo_rate=1.0).generate_script(
        "日本語 \U0001f642 привет 1234 ...."
    )
    assert not [e for e in script if e.role == ms.ROLE_TYPO]


def test_typo_rate_zero_produces_no_typos(long_prose):
    script = MacroScripter(seed=4, typo_rate=0.0).generate_script(long_prose)
    assert not [e for e in script if e.role == ms.ROLE_TYPO]


def test_burst_ranges_are_ordered():
    # An R-burst is by definition the shorter one; if the ranges ever cross,
    # the "revision burst" name stops meaning anything.
    assert ms.R_BURST_RANGE[1] < ms.P_BURST_RANGE[0]


# --- sessions ----------------------------------------------------------------


def test_session_gaps_appear_when_sessions_are_short(long_prose):
    script = MacroScripter(seed=6, session_chars=100).generate_script(long_prose)
    gaps = [e for e in script if e.op == ms.OP_SESSION_GAP]
    assert len(gaps) >= 3
    assert all(gap.duration_ms > 0.0 for gap in gaps)
    assert replay(script) == long_prose


def test_short_texts_contain_no_session_gaps():
    # A session runs 20-90 minutes at 160 chars/min, so a couple of thousand
    # characters cannot exhaust one.
    script = MacroScripter(seed=6).generate_script("A short sentence. " * 20)
    assert not [e for e in script if e.op == ms.OP_SESSION_GAP]


def test_a_session_gap_is_never_the_last_event(long_prose):
    for seed in SEEDS:
        script = MacroScripter(seed=seed, session_chars=30).generate_script(
            long_prose
        )
        assert script[-1].op != ms.OP_SESSION_GAP


def test_session_gap_durations_are_plausible(long_prose):
    script = MacroScripter(seed=6, session_chars=60).generate_script(long_prose)
    gaps = [e.duration_ms / 3_600_000.0 for e in script if e.op == ms.OP_SESSION_GAP]
    assert gaps
    lowest = min(ms.SESSION_GAP_HOURS) * 0.85
    highest = max(ms.SESSION_GAP_HOURS) * 1.15
    assert all(lowest <= hours <= highest for hours in gaps)


def test_session_gap_weights_match_the_hours():
    assert len(ms.SESSION_GAP_HOURS) == len(ms.SESSION_GAP_WEIGHTS)
    assert sum(ms.SESSION_GAP_WEIGHTS) == pytest.approx(1.0)


# --- constructor validation --------------------------------------------------


@pytest.mark.parametrize("typo_rate", [-0.001, -1.0, 1.001, 2.0, float("nan")])
def test_invalid_typo_rate_is_rejected(typo_rate):
    with pytest.raises(ValueError, match="typo_rate"):
        MacroScripter(typo_rate=typo_rate)


@pytest.mark.parametrize(
    "r_burst_probability", [-0.001, -1.0, 1.001, 2.0, float("nan")]
)
def test_invalid_r_burst_probability_is_rejected(r_burst_probability):
    with pytest.raises(ValueError, match="r_burst_probability"):
        MacroScripter(r_burst_probability=r_burst_probability)


@pytest.mark.parametrize("session_chars", [0, -1, -1000])
def test_invalid_session_chars_is_rejected(session_chars):
    with pytest.raises(ValueError, match="session_chars"):
        MacroScripter(session_chars=session_chars)


@pytest.mark.parametrize("typo_rate", [0.0, 0.5, 1.0])
def test_boundary_rates_are_accepted(typo_rate):
    MacroScripter(typo_rate=typo_rate, r_burst_probability=typo_rate)


def test_session_chars_none_is_accepted():
    assert MacroScripter(session_chars=None).session_chars is None


# --- structure ---------------------------------------------------------------


def test_empty_text_produces_an_empty_script(scripter):
    assert scripter.generate_script("") == []


def test_every_event_uses_a_known_op_and_role(corpus):
    ops = {ms.OP_TYPE, ms.OP_DELETE, ms.OP_PAUSE, ms.OP_SESSION_GAP}
    roles = {
        ms.ROLE_TEXT, ms.ROLE_TYPO, ms.ROLE_CORRECTION,
        ms.ROLE_REVISION_DELETE, ms.ROLE_REVISION_RETYPE,
    }
    scripter = MacroScripter(seed=1, typo_rate=0.2, r_burst_probability=0.5)
    for text in corpus[::9]:
        for event in scripter.generate_script(text):
            assert event.op in ops
            assert event.role in roles
            if event.op == ms.OP_TYPE:
                assert event.char is not None
            if event.op == ms.OP_DELETE:
                assert event.count > 0


def test_pause_durations_are_positive_and_finite(long_prose):
    script = MacroScripter(seed=1, session_chars=200).generate_script(long_prose)
    for event in script:
        if event.op in (ms.OP_PAUSE, ms.OP_SESSION_GAP):
            assert event.duration_ms > 0.0
            assert math.isfinite(event.duration_ms)


def test_a_pause_precedes_every_whitespace_keystroke():
    # Whitespace is where the scripter places its pause hierarchy; if a space
    # were typed without one the pause distribution would never be exercised.
    script = MacroScripter(
        seed=1, typo_rate=0.0, r_burst_probability=0.0
    ).generate_script("one two three")
    spaces = [i for i, e in enumerate(script) if e.op == ms.OP_TYPE and e.char == " "]
    assert spaces
    for index in spaces:
        assert script[index - 1].op == ms.OP_PAUSE


def test_reported_corpus_index_is_stable():
    # The corpus is rebuilt from a fixed seed, so a failure reported against
    # corpus[n] means the same text on the next run.
    from conftest import build_corpus

    assert build_corpus() == CORPUS
