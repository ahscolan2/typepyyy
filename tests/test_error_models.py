"""Tests for error_models.

The module is an optional extra - nothing else in the project imports it - but
it emits macro_scripter events, so it inherits the project's one invariant:
`replay(build_error_script(text)) == text`.

Most of the rest of this file pins down the defects the rewrite fixed, because
each of them produced plausible-looking output rather than an error:

  * an error edit has to describe exactly the characters it mutates, or
    applying it silently corrupts a neighbouring character;
  * a correction can only be expressed as backspaces when the difference
    reaches the end of both strings, since the op vocabulary has no cursor
    movement;
  * a lexical substitution has to rewrite the occurrence it selected, not the
    first substring that matches, which used to rewrite the middle of "bits"
    when it meant to replace the word "its";
  * the confusion table lists lexical confusions only, and no longer contains
    the "public" -> "pubic" entry that was neither a homophone nor a typo
    anyone makes on purpose.
"""

import random
import statistics

import pytest

import macro_scripter as ms
from error_models import (
    ANTICIPATION_DISTANCES,
    CONFUSION_SETS,
    CONFUSIONS,
    DEFAULT_ERROR_RATE,
    KIND_ANTICIPATION,
    KIND_EXCHANGE,
    KIND_PERSEVERATION,
    KIND_STUTTER,
    PERSEVERATION_DISTANCES,
    STUTTER_KEYS,
    CognitiveErrorModel,
    ErrorEdit,
    SemanticSubstitution,
    Substitution,
    is_suffix_edit,
)
from macro_scripter import replay

from conftest import EDGE_CASES

# Fixed, so a failure is reproducible from the test name alone.
SEEDS = (0, 1, 7, 42, 12345)

KINDS = (KIND_ANTICIPATION, KIND_PERSEVERATION, KIND_EXCHANGE, KIND_STUTTER)

# Ordinary prose with plenty of eligible positions, used wherever a test needs
# enough errors to say something about their distribution.
LETTERS = (
    "Academic integrity depends on evidence that a piece of writing was "
    "actually composed by the person who submitted it, and the writing "
    "process leaves traces that a finished document does not. "
)

# Texts that each contain at least one word from the confusion table. The
# shared corpus does not - its vocabulary was chosen for keystroke coverage,
# not for homophones - so the substitution tests bring their own.
CONFUSABLE_TEXTS = (
    "The book lost its cover.",
    "Their house is over there.",
    "It is easier to accept the effect than to affect the cause.",
    "You're going to lose the thread if the argument is too loose.",
    "The principal complained about the weather, whether or not it mattered.",
)


@pytest.fixture
def model() -> CognitiveErrorModel:
    return CognitiveErrorModel(error_rate=0.15, seed=20240617)


def covered_positions(edits) -> set:
    """Every character index the edits describe."""
    return {
        index
        for edit in edits
        for index in range(edit.index, edit.index + len(edit.intended))
    }


# --- the composition invariant -----------------------------------------------


def test_build_error_script_reproduces_the_corpus(corpus):
    model = CognitiveErrorModel(error_rate=0.1, seed=99)
    for index, text in enumerate(corpus):
        script = model.build_error_script(text)
        assert replay(script) == text, f"corpus[{index}]: {text!r}"


@pytest.mark.parametrize("error_rate", [0.0, 0.5, 1.0])
def test_build_error_script_holds_at_rate_extremes(sample_corpus, error_rate):
    model = CognitiveErrorModel(error_rate=error_rate, seed=5)
    for text in sample_corpus:
        assert replay(model.build_error_script(text)) == text


@pytest.mark.parametrize("name", sorted(EDGE_CASES))
def test_build_error_script_reproduces_edge_cases(name):
    text = EDGE_CASES[name]
    model = CognitiveErrorModel(error_rate=1.0, seed=3)
    assert replay(model.build_error_script(text)) == text


def test_an_empty_text_produces_an_empty_script(model):
    assert model.build_error_script("") == []


def test_no_errors_at_rate_zero():
    model = CognitiveErrorModel(error_rate=0.0, seed=1)
    script = model.build_error_script(LETTERS)
    assert [event.char for event in script] == list(LETTERS)
    assert {event.op for event in script} == {ms.OP_TYPE}
    assert {event.role for event in script} == {ms.ROLE_TEXT}


def test_the_script_uses_only_known_ops_and_roles(sample_corpus):
    model = CognitiveErrorModel(error_rate=0.4, seed=8)
    ops = {ms.OP_TYPE, ms.OP_DELETE, ms.OP_PAUSE}
    roles = {ms.ROLE_TEXT, ms.ROLE_TYPO, ms.ROLE_CORRECTION}
    for text in sample_corpus:
        for event in model.build_error_script(text):
            assert event.op in ops
            assert event.role in roles


def test_every_error_in_the_script_is_fixed_before_typing_continues():
    # The error is corrected immediately, which is what keeps the deletion at
    # the end of the buffer where a backspace can reach it.
    model = CognitiveErrorModel(error_rate=1.0, seed=4)
    script = model.build_error_script(LETTERS)
    typo_runs = [
        index for index, event in enumerate(script)
        if event.role == ms.ROLE_TYPO and event.op == ms.OP_TYPE
        and (index == 0 or script[index - 1].role != ms.ROLE_TYPO)
    ]
    assert len(typo_runs) > 20, "not enough errors to test the structure"

    for start in typo_runs:
        typed = []
        index = start
        while script[index].op == ms.OP_TYPE and script[index].role == ms.ROLE_TYPO:
            typed.append(script[index].char)
            index += 1

        pause = script[index]
        assert pause.op == ms.OP_PAUSE and pause.role == ms.ROLE_TYPO
        assert (
            ms.TYPO_REACTION_RANGE[0] <= pause.duration_ms <= ms.TYPO_REACTION_RANGE[1]
        )

        delete = script[index + 1]
        assert delete.op == ms.OP_DELETE and delete.role == ms.ROLE_CORRECTION
        # Exactly what was typed comes back off, no more and no less.
        assert delete.count == len(typed)

        retyped = []
        cursor = index + 2
        while (
            cursor < len(script)
            and script[cursor].op == ms.OP_TYPE
            and script[cursor].role == ms.ROLE_CORRECTION
        ):
            retyped.append(script[cursor].char)
            cursor += 1
        assert retyped and "".join(retyped) != "".join(typed)


# --- error planning ----------------------------------------------------------


def test_edits_are_sorted_and_never_overlap(sample_corpus):
    model = CognitiveErrorModel(error_rate=0.5, seed=6)
    for text in sample_corpus:
        edits = model.plan_errors(text)
        assert [edit.index for edit in edits] == sorted(edit.index for edit in edits)
        cursor = -1
        for edit in edits:
            assert edit.index > cursor
            cursor = edit.index + len(edit.intended) - 1


def test_every_edit_describes_the_text_it_sits_on(sample_corpus):
    # An edit that misdescribes its own span mutates a character it never
    # looked at, which is how a "corrected" error can corrupt the text.
    model = CognitiveErrorModel(error_rate=0.5, seed=6)
    checked = 0
    for text in sample_corpus:
        for edit in model.plan_errors(text):
            checked += 1
            span = text[edit.index:edit.index + len(edit.intended)]
            assert span == edit.intended
            assert edit.typed != edit.intended
            assert edit.kind in KINDS
    assert checked > 500, "too few errors planned to have tested anything"


def test_introduce_errors_is_apply_edits_of_plan_errors():
    first = CognitiveErrorModel(error_rate=0.2, seed=11)
    second = CognitiveErrorModel(error_rate=0.2, seed=11)
    assert first.introduce_errors(LETTERS) == second.apply_edits(
        LETTERS, second.plan_errors(LETTERS)
    )


def test_errors_never_land_on_a_non_letter(sample_corpus):
    # A space is struck by the thumb and is not part of a letter sequence.
    model = CognitiveErrorModel(error_rate=1.0, seed=2)
    for text in sample_corpus:
        for edit in model.plan_errors(text):
            assert text[edit.index].isalpha()


def test_a_text_of_digits_and_punctuation_gets_no_errors():
    model = CognitiveErrorModel(error_rate=1.0, seed=3)
    assert model.plan_errors("1234 !!! ... 5678 @#$%") == []
    assert model.introduce_errors("1234 !!! ... 5678 @#$%") == "1234 !!! ... 5678 @#$%"


def test_rate_zero_plans_nothing(sample_corpus):
    model = CognitiveErrorModel(error_rate=0.0, seed=1)
    for text in sample_corpus:
        assert model.plan_errors(text) == []
        assert model.introduce_errors(text) == text


def test_rate_one_uses_every_eligible_position(sample_corpus):
    # At rate 1.0 the Bernoulli trial always fires, so a position is left alone
    # only when no error kind is physically applicable there. The previous
    # version chose a kind first and gave up silently when it did not fit, which
    # is what made the achieved rate unrelated to the configured one.
    saturated = CognitiveErrorModel(error_rate=1.0, seed=2)
    for text in sample_corpus:
        covered = covered_positions(saturated.plan_errors(text))
        for index in range(len(text)):
            if index not in covered:
                assert saturated._candidates(text, index) == []


# --- error kinds -------------------------------------------------------------


def candidates_by_kind(model, text, index) -> dict:
    return {edit.kind: edit for edit in model._candidates(text, index)}


def test_no_candidates_at_a_non_letter(model):
    for index, char in enumerate("ab cd,ef"):
        if not char.isalpha():
            assert model._candidates("ab cd,ef", index) == []


def test_an_exchange_swaps_two_adjacent_letters(model):
    edit = candidates_by_kind(model, "the", 0)[KIND_EXCHANGE]
    assert edit.index == 0
    assert edit.intended == "th"
    assert edit.typed == "ht"
    assert model.apply_edits("the", [edit]) == "hte"


def test_an_exchange_needs_two_different_letters(model):
    # "ll" exchanged with itself is not an error anyone can observe.
    assert KIND_EXCHANGE not in candidates_by_kind(model, "ll ", 0)
    assert KIND_EXCHANGE not in candidates_by_kind(model, "a b", 0)


def test_a_stutter_doubles_the_key(model):
    edit = candidates_by_kind(model, "to", 0)[KIND_STUTTER]
    assert edit.intended == "t"
    assert edit.typed == "tt"
    assert model.apply_edits("to", [edit]) == "tto"


def test_a_stutter_only_happens_on_the_documented_keys(sample_corpus):
    model = CognitiveErrorModel(error_rate=1.0, seed=9)
    for text in sample_corpus:
        for edit in model.plan_errors(text):
            if edit.kind == KIND_STUTTER:
                assert edit.intended.lower() in STUTTER_KEYS


def test_an_anticipation_pulls_a_character_from_ahead(model):
    text = "abcde"
    edits = [
        edit for edit in model._candidates(text, 0)
        if edit.kind == KIND_ANTICIPATION
    ]
    assert {edit.typed for edit in edits} == {
        text[distance] for distance in ANTICIPATION_DISTANCES
    }


def test_a_perseveration_repeats_a_character_from_behind(model):
    text = "abcde"
    edits = [
        edit for edit in model._candidates(text, 3)
        if edit.kind == KIND_PERSEVERATION
    ]
    assert {edit.typed for edit in edits} == {
        text[3 - distance] for distance in PERSEVERATION_DISTANCES
    }


def test_an_anticipation_validates_the_index_it_actually_mutates(model):
    # The edit replaces one character, so it has to describe one character.
    # Describing a longer span would make apply_edits check - and overwrite -
    # characters the error never touched.
    text = "abcde"
    for index in range(len(text)):
        for edit in model._candidates(text, index):
            if edit.kind != KIND_ANTICIPATION:
                continue
            assert len(edit.intended) == 1
            assert edit.intended == text[edit.index]
            flawed = model.apply_edits(text, [edit])
            assert flawed == text[:index] + edit.typed + text[index + 1:]
            # The character it was anticipated from is still where it was.
            assert len(flawed) == len(text)


def test_an_anticipation_never_looks_past_the_end(model):
    # "abc" is shorter than the longest anticipation distance, so only the first
    # position has anything to anticipate from.
    def anticipations(index):
        return [
            edit.typed for edit in model._candidates("abc", index)
            if edit.kind == KIND_ANTICIPATION
        ]

    assert anticipations(0) == ["c"]
    assert anticipations(1) == []
    assert anticipations(2) == []


def test_kinds_are_only_offered_where_they_apply(model):
    # A lone letter between spaces has no neighbour to exchange with, nothing
    # ahead to anticipate and nothing behind to perseverate.
    assert model._candidates(" a ", 1) == []
    assert [edit.kind for edit in model._candidates(" t ", 1)] == [KIND_STUTTER]


# --- apply_edits validation --------------------------------------------------


def test_apply_edits_of_nothing_returns_the_text(model):
    assert model.apply_edits(LETTERS, []) == LETTERS


def test_apply_edits_rejects_an_edit_that_misdescribes_the_text(model):
    wrong = ErrorEdit(KIND_ANTICIPATION, 0, "zz", "xx")
    with pytest.raises(ValueError, match="does not match the text it describes"):
        model.apply_edits("abcdef", [wrong])


def test_apply_edits_rejects_overlapping_edits(model):
    edits = [
        ErrorEdit(KIND_EXCHANGE, 0, "ab", "ba"),
        ErrorEdit(KIND_STUTTER, 1, "b", "bb"),
    ]
    with pytest.raises(ValueError, match="overlap"):
        model.apply_edits("abcdef", edits)


def test_apply_edits_applies_several_edits_left_to_right(model):
    edits = [
        ErrorEdit(KIND_EXCHANGE, 0, "ab", "ba"),
        ErrorEdit(KIND_STUTTER, 4, "e", "ee"),
    ]
    assert model.apply_edits("abcdef", edits) == "bacdeef"


# --- correction: is_suffix_edit ----------------------------------------------


@pytest.mark.parametrize(
    "original,flawed",
    [
        ("hello", "helol"),      # transposition of the last two characters
        ("hello", "hellp"),      # the final character is wrong
        ("hello", "hell"),       # one short: type the rest
        ("hello", "helloo"),     # one too many: delete it
        ("ab", "ba"),
        ("", ""),
        ("a", ""),
        ("", "a"),
        ("abc", "abcdef"),
    ],
)
def test_is_suffix_edit_accepts_an_edit_that_reaches_the_end(original, flawed):
    assert is_suffix_edit(original, flawed)


@pytest.mark.parametrize(
    "original,flawed",
    [
        ("hello world", "helol world"),  # the transposition is behind " world"
        ("abc", "axc"),
        ("cat", "cot"),
        ("the cat sat", "teh cat sat"),
        ("hello", "hell0o"),  # a stray character before a shared final "o"
        ("aab", "ab"),
    ],
)
def test_is_suffix_edit_rejects_an_interior_edit(original, flawed):
    # A DELETE is a backspace at the end of the buffer; reaching an interior
    # difference would mean destroying the shared suffix and retyping it, which
    # is not what a writer does and not what the op vocabulary models.
    assert not is_suffix_edit(original, flawed)


def test_is_suffix_edit_is_true_when_nothing_differs():
    assert is_suffix_edit("hello", "hello")


# --- correction: scripts -----------------------------------------------------


def type_out(text: str):
    """The events that type `text` from an empty buffer."""
    return [ms.ScriptEvent(ms.OP_TYPE, char=char) for char in text]


@pytest.mark.parametrize(
    "original,flawed",
    [
        ("hello", "helol"),
        ("hello", "hellp"),
        ("hello", "hell"),
        ("hello", "helloo"),
        ("writing", "writign"),
        ("a", ""),
    ],
)
def test_a_correction_script_turns_the_flawed_buffer_into_the_original(
    original, flawed
):
    model = CognitiveErrorModel(seed=1)
    events = model.generate_correction_script(original, flawed)
    assert replay(type_out(flawed) + events) == original


def test_a_correction_script_is_empty_when_nothing_is_wrong():
    assert CognitiveErrorModel(seed=1).generate_correction_script("same", "same") == []


def test_a_correction_script_deletes_only_back_to_the_divergence():
    model = CognitiveErrorModel(seed=1)
    events = model.generate_correction_script("hello", "helol")
    deletes = [event for event in events if event.op == ms.OP_DELETE]
    assert [event.count for event in deletes] == [2]
    assert [event.char for event in events if event.op == ms.OP_TYPE] == ["l", "o"]
    assert {event.role for event in events} == {ms.ROLE_CORRECTION}


def test_a_correction_script_only_types_when_the_buffer_is_short():
    model = CognitiveErrorModel(seed=1)
    events = model.generate_correction_script("hello", "hel")
    assert [event.op for event in events] == [ms.OP_PAUSE] + [ms.OP_TYPE] * 2


def test_a_correction_script_opens_with_a_reaction_pause():
    model = CognitiveErrorModel(seed=1)
    events = model.generate_correction_script("hello", "helol")
    assert events[0].op == ms.OP_PAUSE
    assert (
        ms.TYPO_REACTION_RANGE[0]
        <= events[0].duration_ms
        <= ms.TYPO_REACTION_RANGE[1]
    )


@pytest.mark.parametrize(
    "original,flawed",
    [
        ("hello world", "helol world"),
        ("the cat sat", "teh cat sat"),
        ("abc", "axc"),
    ],
)
def test_an_interior_correction_is_refused_with_an_explanation(original, flawed):
    model = CognitiveErrorModel(seed=1)
    with pytest.raises(ValueError, match="not expressible as backspaces"):
        model.generate_correction_script(original, flawed)


def test_the_refusal_names_both_strings():
    model = CognitiveErrorModel(seed=1)
    with pytest.raises(ValueError) as excinfo:
        model.generate_correction_script("hello world", "helol world")
    message = str(excinfo.value)
    assert "helol world" in message and "hello world" in message
    assert "DELETE" in message


def test_correction_events_type_notice_delete_and_retype():
    model = CognitiveErrorModel(seed=1)
    edit = ErrorEdit(KIND_EXCHANGE, 0, "th", "ht")
    events = model.correction_events(edit)

    assert [event.op for event in events] == [
        ms.OP_TYPE, ms.OP_TYPE, ms.OP_PAUSE, ms.OP_DELETE, ms.OP_TYPE, ms.OP_TYPE,
    ]
    assert [event.char for event in events[:2]] == ["h", "t"]
    assert events[3].count == 2
    assert [event.char for event in events[4:]] == ["t", "h"]
    assert [event.role for event in events] == [
        ms.ROLE_TYPO, ms.ROLE_TYPO, ms.ROLE_TYPO,
        ms.ROLE_CORRECTION, ms.ROLE_CORRECTION, ms.ROLE_CORRECTION,
    ]


def test_correction_events_delete_exactly_what_was_typed(sample_corpus):
    model = CognitiveErrorModel(error_rate=0.4, seed=13)
    checked = 0
    for text in sample_corpus:
        for edit in model.plan_errors(text):
            checked += 1
            events = model.correction_events(edit)
            typed = [e for e in events if e.op == ms.OP_TYPE and e.role == ms.ROLE_TYPO]
            deleted = sum(e.count for e in events if e.op == ms.OP_DELETE)
            assert deleted == len(typed) == len(edit.typed)
    assert checked > 500, "too few errors planned to have tested anything"


def test_every_planned_error_is_correctable_by_backspacing(sample_corpus):
    # This is the property that lets correction_events use a bare DELETE: the
    # error is at the end of the buffer at the moment it is noticed, so the
    # backspace can reach it without any cursor movement.
    model = CognitiveErrorModel(error_rate=0.5, seed=17)
    checked = 0
    for text in sample_corpus:
        for edit in model.plan_errors(text):
            checked += 1
            prefix = text[:edit.index]
            assert is_suffix_edit(prefix + edit.intended, prefix + edit.typed)
    assert checked > 500, "too few errors planned to have tested anything"


def test_a_correction_script_can_fix_any_planned_error():
    model = CognitiveErrorModel(error_rate=0.5, seed=19)
    edits = model.plan_errors(LETTERS)
    assert edits
    for edit in edits:
        prefix = LETTERS[:edit.index]
        original = prefix + edit.intended
        flawed = prefix + edit.typed
        events = model.generate_correction_script(original, flawed)
        assert replay(type_out(flawed) + events) == original


# --- determinism -------------------------------------------------------------


@pytest.mark.parametrize("seed", SEEDS)
def test_the_same_seed_plans_the_same_errors(seed):
    first = CognitiveErrorModel(error_rate=0.2, seed=seed).plan_errors(LETTERS)
    second = CognitiveErrorModel(error_rate=0.2, seed=seed).plan_errors(LETTERS)
    assert first == second


@pytest.mark.parametrize("seed", SEEDS)
def test_introduce_errors_is_deterministic_under_a_fixed_seed(seed):
    text = LETTERS * 3
    first = CognitiveErrorModel(error_rate=0.08, seed=seed).introduce_errors(text)
    second = CognitiveErrorModel(error_rate=0.08, seed=seed).introduce_errors(text)
    assert first == second
    assert first != text, "no error was introduced, so nothing was tested"


def test_different_seeds_give_different_errors():
    results = [
        CognitiveErrorModel(error_rate=0.15, seed=seed).introduce_errors(LETTERS * 2)
        for seed in SEEDS
    ]
    for index, other in enumerate(results[1:], start=1):
        assert other != results[0], f"seed {SEEDS[index]} matched seed {SEEDS[0]}"


def test_the_same_seed_builds_the_same_script():
    first = CognitiveErrorModel(error_rate=0.2, seed=3).build_error_script(LETTERS)
    second = CognitiveErrorModel(error_rate=0.2, seed=3).build_error_script(LETTERS)
    assert first == second


def test_churning_the_global_rng_does_not_change_the_output():
    baseline = CognitiveErrorModel(error_rate=0.2, seed=7).plan_errors(LETTERS)

    random.seed(1)
    for _ in range(1000):
        random.random()
    subject = CognitiveErrorModel(error_rate=0.2, seed=7)
    random.seed(2)
    for _ in range(500):
        random.gauss(0.0, 1.0)

    assert subject.plan_errors(LETTERS) == baseline


def test_planning_does_not_disturb_the_global_rng():
    random.seed(4242)
    before = random.getstate()
    CognitiveErrorModel(error_rate=0.3, seed=7).build_error_script(LETTERS)
    SemanticSubstitution(seed=7).maybe_substitute("its their to then")
    assert random.getstate() == before


def test_interleaving_another_model_does_not_change_the_output():
    baseline = CognitiveErrorModel(error_rate=0.2, seed=7).plan_errors(LETTERS)

    subject = CognitiveErrorModel(error_rate=0.2, seed=7)
    noise = CognitiveErrorModel(error_rate=0.2, seed=8)
    noise.plan_errors(LETTERS)
    assert subject.plan_errors(LETTERS) == baseline


# --- the achieved error rate -------------------------------------------------

# The rate is per *eligible* position, so the achieved rate is measured against
# the positions where some error kind applies (about 1256 of the 1520 characters
# below) rather than against len(text).
#
# Each figure is averaged over 40 fixed seeds, so the test is deterministic
# rather than a sampling experiment. The Bernoulli standard error at p = 0.08
# over 1256 positions is 0.0077, which the average over 40 seeds brings down to
# 0.0012, so the tolerance below is between three and five standard errors
# depending on the rate. Measured deviations at these seeds are all under
# 0.0007.
RATE_SEEDS = range(40)
RATE_TOLERANCE = 0.004
RATE_TEXT = LETTERS * 8


def achieved_rate(model: CognitiveErrorModel, text: str) -> float:
    eligible = sum(1 for index in range(len(text)) if model._candidates(text, index))
    assert eligible, "the text has no eligible positions"
    return len(model.plan_errors(text)) / eligible


@pytest.mark.slow
@pytest.mark.parametrize("error_rate", [0.01, 0.03, 0.08])
def test_the_achieved_error_rate_matches_the_configured_one(error_rate):
    rates = [
        achieved_rate(CognitiveErrorModel(error_rate=error_rate, seed=seed), RATE_TEXT)
        for seed in RATE_SEEDS
    ]
    assert statistics.mean(rates) == pytest.approx(error_rate, abs=RATE_TOLERANCE)


@pytest.mark.slow
def test_a_high_rate_falls_slightly_short_of_the_configured_one():
    # A position consumed by an error is not tried again, so the achieved rate
    # sits just under the configured one and the gap grows with the rate. At
    # 0.25 it is about 5%; anything larger means errors are being dropped.
    rates = [
        achieved_rate(CognitiveErrorModel(error_rate=0.25, seed=seed), RATE_TEXT)
        for seed in RATE_SEEDS
    ]
    mean = statistics.mean(rates)
    assert 0.9 * 0.25 <= mean < 0.25


def test_the_error_count_scales_with_the_rate():
    counts = [
        len(CognitiveErrorModel(error_rate=rate, seed=21).plan_errors(RATE_TEXT))
        for rate in (0.0, 0.02, 0.05, 0.1, 0.2)
    ]
    assert counts == sorted(counts)
    assert counts[0] == 0 and counts[-1] > counts[1]


@pytest.mark.parametrize("error_rate", [-0.001, 1.001, -1.0, 2.0, float("nan")])
def test_an_out_of_range_error_rate_is_rejected(error_rate):
    with pytest.raises(ValueError, match="error_rate must be in"):
        CognitiveErrorModel(error_rate=error_rate)


@pytest.mark.parametrize("error_rate", [0.0, 0.5, 1.0])
def test_boundary_rates_are_accepted(error_rate):
    assert CognitiveErrorModel(error_rate=error_rate).error_rate == error_rate


def test_the_default_rate_is_the_documented_one():
    assert CognitiveErrorModel().error_rate == DEFAULT_ERROR_RATE == 0.03


def test_an_error_edit_serialises():
    edit = ErrorEdit(KIND_STUTTER, 4, "t", "tt")
    assert edit.to_dict() == {
        "kind": KIND_STUTTER, "index": 4, "intended": "t", "typed": "tt",
    }


# --- the confusion table -----------------------------------------------------


def test_the_public_entry_is_gone():
    # "public" -> "pubic" is a keyboard slip, not a lexical confusion, and it
    # made the table's output unusable in anything anyone would read.
    assert "public" not in CONFUSIONS
    assert "pubic" not in CONFUSIONS
    for word, replacements in CONFUSIONS.items():
        assert "pubic" not in replacements, word


@pytest.mark.parametrize(
    "motor_error", [("form", "from"), ("expert", "erpert"), ("public", "pubic")]
)
def test_motor_errors_are_not_in_the_lexical_table(motor_error):
    # Keyboard slips and transpositions belong to CognitiveErrorModel or to
    # macro_scripter's neighbour-key model, not to a table of word confusions.
    word, slip = motor_error
    assert slip not in CONFUSIONS.get(word, ())


def test_every_set_is_symmetric():
    # An asymmetric mapping is what produced pairs no writer makes; each set is
    # unordered, so every member has to reach every other.
    for group in CONFUSION_SETS:
        for word in group:
            assert set(CONFUSIONS[word]) == set(group) - {word}


def test_no_word_is_confused_with_itself():
    for word, replacements in CONFUSIONS.items():
        assert word not in replacements


def test_no_word_appears_in_two_sets():
    # The table is built by flattening the sets, so a word in two of them would
    # silently keep only the last.
    seen = set()
    for group in CONFUSION_SETS:
        for word in group:
            assert word not in seen, word
            seen.add(word)


def test_every_set_has_at_least_two_members():
    for group in CONFUSION_SETS:
        assert len(group) >= 2


def test_the_table_is_lower_case():
    for word in CONFUSIONS:
        assert word == word.lower()


# --- SemanticSubstitution ----------------------------------------------------


def test_a_text_without_confusable_words_is_returned_unchanged():
    text = "Nothing here can be mistaken for another word."
    assert SemanticSubstitution(seed=1).maybe_substitute(text) == (text, None)


def test_a_confusable_word_is_replaced():
    text, substitution = SemanticSubstitution(seed=1).maybe_substitute(
        "The book lost its cover."
    )
    assert substitution == Substitution(14, "its", "it's")
    assert text == "The book lost it's cover."


def test_its_is_not_matched_inside_bits():
    # The previous version called str.replace(word, replacement, 1), which
    # rewrote the first substring anywhere in the text: substituting "its"
    # rewrote the middle of "bits".
    substituter = SemanticSubstitution(seed=1)
    assert substituter.candidates("bits and bits") == []
    assert substituter.maybe_substitute("bits and bits") == ("bits and bits", None)

    text, substitution = substituter.maybe_substitute("bits and its parts")
    assert substitution.index == 9
    assert text == "bits and it's parts"
    assert text.startswith("bits and ")


@pytest.mark.parametrize("word", ["bits", "pits", "itself", "fits", "orbits"])
def test_a_confusable_word_inside_a_longer_word_is_not_a_candidate(word):
    assert SemanticSubstitution(seed=1).candidates(f"one {word} two") == []


def test_the_occurrence_that_was_selected_is_the_one_replaced():
    # Spliced in by position, so whichever occurrence the model chose is the one
    # that changes and the rest of the text is untouched.
    text = "its cover and its spine"
    chosen = set()
    for seed in range(50):
        new_text, substitution = SemanticSubstitution(seed=seed).maybe_substitute(text)
        chosen.add(substitution.index)
        assert new_text == (
            text[:substitution.index]
            + substitution.replacement
            + text[substitution.index + len(substitution.original):]
        )
        assert text[
            substitution.index:substitution.index + len(substitution.original)
        ] == substitution.original
    # Both occurrences come up over the seed range, so the test is not passing
    # merely because the first one is always chosen.
    assert chosen == {0, 14}


def test_the_substitution_is_spliced_by_position():
    substituted = 0
    for seed in range(12):
        substituter = SemanticSubstitution(seed=seed)
        for text in CONFUSABLE_TEXTS:
            new_text, substitution = substituter.maybe_substitute(text)
            assert substitution is not None, text
            substituted += 1
            assert new_text == (
                text[:substitution.index]
                + substitution.replacement
                + text[substitution.index + len(substitution.original):]
            )
    assert substituted == 12 * len(CONFUSABLE_TEXTS)


def test_maybe_substitute_is_safe_on_any_text(sample_corpus):
    # A caller augmenting a corpus feeds it whatever the corpus holds, so emoji,
    # RTL, CJK, lone whitespace and the empty string all reach this unfiltered.
    substituter = SemanticSubstitution(seed=5)
    for text in sample_corpus:
        new_text, substitution = substituter.maybe_substitute(text)
        if substitution is None:
            assert new_text == text
        else:
            assert new_text == (
                text[:substitution.index]
                + substitution.replacement
                + text[substitution.index + len(substitution.original):]
            )


def test_the_replacement_is_never_the_word_it_replaced():
    for seed in range(30):
        _text, substitution = SemanticSubstitution(seed=seed).maybe_substitute(
            "their there its it's then than to too"
        )
        assert substitution.replacement.lower() != substitution.original.lower()


@pytest.mark.parametrize(
    "text,expected",
    [
        ("its cover", "it's"),
        ("Its cover", "It's"),
        ("ITS COVER", "IT'S"),
    ],
)
def test_the_replacement_takes_the_case_of_the_word_it_replaces(text, expected):
    new_text, substitution = SemanticSubstitution(seed=1).maybe_substitute(text)
    assert substitution.replacement == expected
    assert new_text.startswith(expected)


def test_a_single_upper_case_letter_is_not_treated_as_shouting():
    # len(source) > 1 guards the all-caps branch, so "A" keeps title case.
    text, substitution = SemanticSubstitution(seed=1).maybe_substitute("To the point")
    assert substitution.original == "To"
    assert substitution.replacement == "Too"


def test_a_word_with_a_typographic_apostrophe_is_recognised():
    # \w+ would split "it’s" into two tokens and never match the table.
    text, substitution = SemanticSubstitution(seed=1).maybe_substitute("It’s a test")
    assert substitution.original == "It’s"
    assert substitution.replacement == "Its"
    assert text == "Its a test"


def test_candidates_finds_every_confusable_word():
    words = [
        match.group(0)
        for match in SemanticSubstitution(seed=1).candidates(
            "their book is over there and its cover is loose"
        )
    ]
    assert words == ["their", "there", "its", "loose"]


def test_substitution_is_deterministic():
    text = "their book is over there and its cover is loose"
    for seed in SEEDS:
        first = SemanticSubstitution(seed=seed).maybe_substitute(text)
        second = SemanticSubstitution(seed=seed).maybe_substitute(text)
        assert first == second


def test_different_seeds_choose_different_substitutions():
    text = "their book is over there and its cover is loose"
    results = {
        SemanticSubstitution(seed=seed).maybe_substitute(text)[0]
        for seed in range(20)
    }
    assert len(results) > 1


def test_a_substitution_serialises():
    substitution = Substitution(3, "its", "it's")
    assert substitution.to_dict() == {
        "kind": "lexical_confusion",
        "index": 3,
        "original": "its",
        "replacement": "it's",
    }


def test_substitution_leaves_the_text_a_valid_target(sample_corpus):
    # The result is a different text, not a mistyping of the same one, so it has
    # to be scriptable in its own right - it is the target the generator records.
    substituter = SemanticSubstitution(seed=2)
    scripter = ms.MacroScripter(seed=2)
    for text in sample_corpus[::4]:
        new_text, _substitution = substituter.maybe_substitute(text)
        assert replay(scripter.generate_script(new_text)) == new_text
