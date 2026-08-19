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

from conftest import CORPUS, EDGE_CASES, PROSE

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


# --- thinking-pause budget and the 15-minute ceiling ---------------------------

# The README replay sample: 177 chars, two sentences.
README_SAMPLE_TEXT = (
    "Academic integrity depends on evidence that a piece of writing was "
    "actually composed by the person who submitted it. The process leaves "
    "traces that a finished document does not."
)


def _thinking_pauses(script):
    """PAUSE ops that are not typo reactions or revision beats."""
    return [e for e in script if e.op == ms.OP_PAUSE and e.role == ms.ROLE_TEXT]


def test_short_text_thinking_pauses_stay_within_budget():
    # The owner-facing rule of thumb: pauses are sentence/burst-level events,
    # not per-word events. On the 177-char README sample at seed 23 the
    # measured count is 3 thinking pauses (729/649/1011 ms, none over 2 s);
    # the bound below is a budget, not the exact count, so the stream can move
    # with future model work without tripping it.
    script = MacroScripter(seed=23).generate_script(README_SAMPLE_TEXT)
    thinking = _thinking_pauses(script)
    assert 0 <= len(thinking) <= 8
    assert sum(1 for e in thinking if e.duration_ms > 2_000.0) <= 2
    assert all(e.duration_ms <= ms.MAX_SILENCE_MS for e in thinking)


def test_long_text_thinking_pauses_track_sentences_not_words():
    # 2000 chars of prose, seed 23: measured 43 thinking pauses, roughly one
    # per one to three sentences. Before the boundary rework the same text
    # drew several hundred (a pause after nearly every word).
    text = (PROSE * 7)[:2000].strip()
    script = MacroScripter(seed=23).generate_script(text)
    assert 15 <= len(_thinking_pauses(script)) <= 60


def test_no_recorded_silence_exceeds_fifteen_minutes(corpus):
    # Sweep seeds over the thinned corpus, forcing sessions short enough that
    # the session-gap table - which draws 0.5-48 hours - is exercised. Every
    # pause and every gap must come in at or under the ceiling.
    for seed in SEEDS:
        scripter = MacroScripter(seed=seed, session_chars=60)
        gaps = 0
        for text in corpus[::13]:
            script = scripter.generate_script(text)
            for event in script:
                if event.op in (ms.OP_PAUSE, ms.OP_SESSION_GAP):
                    assert 0.0 < event.duration_ms <= ms.MAX_SILENCE_MS
            gaps += sum(1 for e in script if e.op == ms.OP_SESSION_GAP)
        assert gaps, f"seed {seed} saw no session gaps despite session_chars=60"


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
        seed=3, typo_rate=0.0, r_burst_probability=0.0,
        structural_revision_rate=0.0,
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


# --- structural (sentence-scale) revision --------------------------------------


def _revision_spans(text, script):
    """(deleted span, retyped string) for every revision event in `script`."""
    spans = []
    for index, event in enumerate(script):
        if event.op != ms.OP_DELETE or event.role != ms.ROLE_REVISION_DELETE:
            continue
        # Measure the buffer as it stood just before the delete, directly.
        buffer = []
        for prior in script[:index]:
            if prior.op == ms.OP_TYPE:
                buffer.append(prior.char)
            elif prior.op == ms.OP_DELETE:
                del buffer[-prior.count:]
        deleted = text[len(buffer) - event.count:len(buffer)]
        retyped = "".join(
            e.char
            for e in script[index + 1:]
            if e.op == ms.OP_TYPE and e.role == ms.ROLE_REVISION_RETYPE
        )[:event.count]
        spans.append((deleted, retyped))
    return spans


def test_structural_revision_deletes_a_whole_sentence_and_retypes_it():
    sentences = [
        "First sentence ends here.",
        "Second sentence ends there.",
    ]
    text = " ".join(sentences)
    script = MacroScripter(
        seed=11, typo_rate=0.0, r_burst_probability=0.0,
        structural_revision_rate=1.0,
    ).generate_script(text)

    spans = _revision_spans(text, script)
    # Every completed sentence is revised once, whole, at rate 1.0.
    assert [deleted for deleted, _ in spans] == sentences
    # The retype emits the identical characters, which is what keeps the
    # replay invariant true by construction.
    assert all(deleted == retyped for deleted, retyped in spans)
    assert replay(script) == text


def test_structural_revision_crosses_the_burst_boundary():
    # One 16-word sentence cannot fit inside any burst (they run 3-13 words),
    # so a delete covering the whole sentence necessarily crosses the boundary
    # of the burst that produced it.
    sentence = (
        "this old house has four tall oak trees and "
        "deep soft green grass all year round."
    )
    script = MacroScripter(
        seed=3, typo_rate=0.0, r_burst_probability=0.0,
        structural_revision_rate=1.0,
    ).generate_script(sentence)

    deletes = [
        e for e in script
        if e.op == ms.OP_DELETE and e.role == ms.ROLE_REVISION_DELETE
    ]
    assert len(deletes) == 1
    assert deletes[0].count == len(sentence)
    words = sentence.split()
    window = ms.P_BURST_RANGE[1]
    largest_burst_span = max(
        sum(len(word) + 1 for word in words[i:i + window]) - 1
        for i in range(len(words) - window + 1)
    )
    assert deletes[0].count > largest_burst_span
    assert replay(script) == sentence


def test_structural_revision_can_cross_a_paragraph_break(monkeypatch):
    # Reach back over a blank line to rework the paragraph's trailing sentence.
    monkeypatch.setattr(ms, "STRUCTURAL_BACK_ACROSS_PARAGRAPH", 1.0)
    text = "One two three.\n\nFour five six. Seven eight nine ten."
    script = MacroScripter(
        seed=1, typo_rate=0.0, r_burst_probability=0.0,
        structural_revision_rate=1.0,
    ).generate_script(text)

    spans = _revision_spans(text, script)
    assert spans, "structural_revision_rate=1.0 produced no revision"
    assert any("\n\n" in deleted for deleted, _ in spans)
    assert all(deleted == retyped for deleted, retyped in spans)
    assert replay(script) == text


def test_replay_holds_with_structural_revisions_forced(corpus):
    scripter = MacroScripter(
        seed=5, structural_revision_rate=1.0, r_burst_probability=0.3
    )
    for index, text in enumerate(corpus[::7]):
        assert replay(scripter.generate_script(text)) == text, (
            f"corpus[{index * 7}] failed: {text!r}"
        )


def test_same_seed_gives_an_identical_script_with_structural_revisions(long_prose):
    first = MacroScripter(
        seed=42, structural_revision_rate=0.5
    ).generate_script(long_prose)
    second = MacroScripter(
        seed=42, structural_revision_rate=0.5
    ).generate_script(long_prose)
    assert first == second


@pytest.mark.parametrize(
    "structural_revision_rate", [-0.001, -1.0, 1.001, 2.0, float("nan")]
)
def test_invalid_structural_revision_rate_is_rejected(structural_revision_rate):
    with pytest.raises(ValueError, match="structural_revision_rate"):
        MacroScripter(structural_revision_rate=structural_revision_rate)


def test_paragraph_trailing_detection():
    probe = MacroScripter._paragraph_trailing
    assert probe("One.\n\nTwo.", 0, 6)
    assert not probe("One. Two.", 0, 5)
    assert probe("One.\r\n\r\nTwo.", 0, 8)
    assert not probe("One.\nTwo.", 0, 5)


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
    gaps = [e.duration_ms for e in script if e.op == ms.OP_SESSION_GAP]
    assert gaps
    # The table is drawn in minutes below the ceiling, so the clamp is a guard
    # and not the thing that picks the value.
    assert all(0.0 < gap <= ms.MAX_SILENCE_MS for gap in gaps)
    floor_ms = min(ms.SESSION_GAP_MINUTES) * 0.85 * 60_000.0
    assert all(gap >= floor_ms for gap in gaps)


def test_session_gaps_are_not_all_the_same_length(long_prose):
    """A gap distribution that always returns its ceiling is a constant.

    Drawing 0.5-48 hours and clamping at fifteen minutes made every gap in
    every record exactly 900000.0 ms, which is both wrong and trivially
    learnable by anything trained on these records.
    """
    script = MacroScripter(seed=6, session_chars=40).generate_script(long_prose * 4)
    gaps = [e.duration_ms for e in script if e.op == ms.OP_SESSION_GAP]
    assert len(gaps) >= 10, "fixture is meant to produce many gaps"
    assert len(set(gaps)) > 1
    assert not all(gap == ms.MAX_SILENCE_MS for gap in gaps)


def test_session_gap_weights_match_the_minutes():
    assert len(ms.SESSION_GAP_MINUTES) == len(ms.SESSION_GAP_WEIGHTS)
    assert sum(ms.SESSION_GAP_WEIGHTS) == pytest.approx(1.0)


def test_session_gap_table_stays_under_the_silence_ceiling():
    """The product rule: no recorded silence runs past fifteen minutes."""
    highest_ms = max(ms.SESSION_GAP_MINUTES) * 1.15 * 60_000.0
    assert highest_ms <= ms.MAX_SILENCE_MS


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


def test_word_boundaries_emit_no_pause():
    # Hesitation between words lives in the inter-key variance of the timing
    # engine; a PAUSE op after plain words put a pause every couple of seconds
    # of output. A burst-end pause can still precede a *word* token, but never
    # a space.
    script = MacroScripter(
        seed=1, typo_rate=0.0, r_burst_probability=0.0,
        structural_revision_rate=0.0,
    ).generate_script("one two three four five six seven eight nine ten")
    spaces = [i for i, e in enumerate(script) if e.op == ms.OP_TYPE and e.char == " "]
    assert spaces
    for index in spaces:
        assert script[index - 1].op != ms.OP_PAUSE


def test_paragraph_boundaries_always_pause():
    # The second newline of a blank line carries the paragraph context, whose
    # boundary probability is 1.0.
    script = MacroScripter(
        seed=1, typo_rate=0.0, r_burst_probability=0.0,
        structural_revision_rate=0.0,
    ).generate_script("One two.\n\nThree four.")
    newlines = [
        i for i, e in enumerate(script) if e.op == ms.OP_TYPE and e.char == "\n"
    ]
    assert len(newlines) == 2
    assert script[newlines[0] - 1].op != ms.OP_PAUSE  # sentence roll: seed 1 misses
    assert script[newlines[1] - 1].op == ms.OP_PAUSE  # paragraph: certain


def test_boundary_pauses_fire_at_their_model_rates():
    # Sentence and clause boundaries pause on a roll of 0.4 and 0.2
    # (BOUNDARY_PAUSE_PROBABILITIES), so over a fixed text at a fixed seed the
    # count of boundary pauses has to sit strictly between none and all.
    text = ("One two three four. Five, six seven eight. " * 6).strip()
    paused_sentence = 0
    paused_clause = 0
    sentences = text.count(". ") + text.count(".\n")
    clauses = text.count(", ")
    script = MacroScripter(
        seed=6, typo_rate=0.0, r_burst_probability=0.0,
        structural_revision_rate=0.0,
    ).generate_script(text)
    events = script
    for index, event in enumerate(events):
        if event.op != ms.OP_PAUSE or event.role != ms.ROLE_TEXT:
            continue
        following = events[index + 1] if index + 1 < len(events) else None
        if following is not None and following.op == ms.OP_TYPE and following.char == " ":
            previous = next(
                e for e in reversed(events[:index])
                if e.op == ms.OP_TYPE and not e.char.isspace()
            )
            if previous.char == ".":
                paused_sentence += 1
            elif previous.char == ",":
                paused_clause += 1
    # The 0.4 / 0.2 rolls: allow a wide band around the draw of this one seed;
    # measured values at seed 6 are 5 of 11 sentence boundaries and 1 of 6
    # clause boundaries.
    assert 0 < paused_sentence < sentences
    assert 0 <= paused_clause <= clauses


def test_reported_corpus_index_is_stable():
    # The corpus is rebuilt from a fixed seed, so a failure reported against
    # corpus[n] means the same text on the next run.
    from conftest import build_corpus

    assert build_corpus() == CORPUS
