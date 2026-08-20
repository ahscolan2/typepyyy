"""Tests for the --typo-model flag: neighbour default, rich opt-in.

The load-bearing property is the first one: with the default model, the
generated process is exactly what it was before the flag existed, so nobody's
seeded dataset changes underneath them. Everything else checks that "rich"
composes with the machinery it is spliced into - same replay invariant, same
determinism contract, same typo budget.
"""

import json

import pytest

import error_models as em
import macro_scripter as ms
from macro_scripter import MacroScripter
from pipeline import generate

from conftest import EDGE_CASES

PROSE = (
    "The professor asked the students to explain the reasoning behind their "
    "conclusions, and most of them did so with real care. The rest promised "
    "to try again after the seminar. "
)

RICH_KINDS = (
    em.KIND_EXCHANGE,
    em.KIND_STUTTER,
    em.KIND_ANTICIPATION,
    em.KIND_PERSEVERATION,
)


# --- the default is unchanged ------------------------------------------------


@pytest.mark.parametrize("seed", [1, 42, 20240617])
def test_default_model_is_neighbor_and_changes_nothing(seed):
    """Omitting the flag and passing 'neighbor' are the same record.

    The default path must not consume a single extra RNG draw, so the whole
    JSON - keystrokes, script, intervals, statistics - is compared, not a
    sample of it.
    """
    default = generate(PROSE * 2, seed=seed)
    explicit = generate(PROSE * 2, seed=seed, typo_model="neighbor")
    assert json.dumps(default, sort_keys=True) == json.dumps(
        explicit, sort_keys=True
    )
    assert default["metadata"]["typo_model"] == "neighbor"


def test_neighbor_mode_produces_only_neighbor_errors():
    scripter = MacroScripter(seed=3, typo_rate=0.2)
    scripter.generate_script(PROSE * 2)
    assert set(scripter.error_kinds) <= {ms.KIND_NEIGHBOR}
    assert scripter.error_kinds.get(ms.KIND_NEIGHBOR, 0) > 0


# --- validation --------------------------------------------------------------


def test_a_bogus_typo_model_is_rejected():
    with pytest.raises(ValueError, match="typo_model"):
        MacroScripter(typo_model="cognitive")


def test_the_cli_rejects_a_bogus_typo_model():
    import main

    with pytest.raises(SystemExit) as excinfo:
        main.build_parser().parse_args(["-t", "x", "--typo-model", "bogus"])
    assert excinfo.value.code == 2


def test_the_cli_accepts_both_models():
    import main

    for name in ms.TYPO_MODELS:
        args = main.build_parser().parse_args(["-t", "x", "--typo-model", name])
        assert args.typo_model == name


# --- the rich model ----------------------------------------------------------


def test_all_four_cognitive_kinds_occur_under_rich():
    """Across a seeded sweep, every kind in the taxonomy actually fires."""
    seen = {}
    for seed in range(20):
        scripter = MacroScripter(seed=seed, typo_model="rich", typo_rate=0.06)
        scripter.generate_script(PROSE * 4)
        for kind, count in scripter.error_kinds.items():
            seen[kind] = seen.get(kind, 0) + count
    for kind in RICH_KINDS:
        assert seen.get(kind, 0) > 0, f"{kind} never occurred in 20 seeds"
    # The plain slip stays in the mix - rich splits the budget, it does not
    # replace the neighbour model.
    assert seen.get(ms.KIND_NEIGHBOR, 0) > 0


def test_rich_errors_vanish_at_rate_zero():
    scripter = MacroScripter(seed=5, typo_model="rich", typo_rate=0.0)
    script = scripter.generate_script(PROSE * 2)
    assert scripter.error_kinds == {}
    assert all(event.role != ms.ROLE_TYPO for event in script)


def test_rich_replay_invariant_over_the_corpus(corpus):
    for index, text in enumerate(corpus):
        scripter = MacroScripter(
            seed=index % 7, typo_model="rich", typo_rate=0.4
        )
        assert ms.replay(scripter.generate_script(text)) == text, (
            f"corpus[{index}]: {text!r}"
        )


@pytest.mark.parametrize("name", sorted(EDGE_CASES))
def test_rich_replay_invariant_on_edge_cases(name):
    text = EDGE_CASES[name]
    record = generate(text, seed=6, typo_model="rich", typo_rate=0.4)
    assert record["target_text"] == text


def test_rich_full_pipeline_reconstructs_unicode():
    text = "Café naïve — résumé… 中文 🙂 done. Vraiment très bien. "
    record = generate(text * 3, seed=9, typo_model="rich", typo_rate=0.3)
    assert record["target_text"] == text * 3


@pytest.mark.parametrize("seed", [7, 11])
def test_rich_is_deterministic(seed):
    first = generate(PROSE * 2, seed=seed, typo_model="rich")
    second = generate(PROSE * 2, seed=seed, typo_model="rich")
    assert json.dumps(first, sort_keys=True) == json.dumps(
        second, sort_keys=True
    )


def test_rich_metadata_round_trips():
    original = generate(PROSE * 2, seed=13, typo_model="rich", typo_rate=0.1)
    assert original["metadata"]["typo_model"] == "rich"
    metadata = dict(original["metadata"])
    metadata.pop("input_chars")
    metadata.pop("input_words")
    assert generate(PROSE * 2, **metadata)["keystrokes"] == original["keystrokes"]


# --- the budget --------------------------------------------------------------


@pytest.mark.slow
def test_rich_spends_the_same_budget_as_neighbor():
    """Achieved error rate stays within ~20% of the configured one.

    The rate is per eligible position - letters, since neither model mistypes
    whitespace or punctuation - and slightly under the nominal rate because a
    fired exchange consumes two positions. Both models are measured the same
    way so the comparison is between them, not against an idealised count.
    """
    text = PROSE * 6
    eligible = sum(1 for ch in text if ch.isalpha())
    rate = 0.05
    per_model = {}
    for model in ms.TYPO_MODELS:
        fired = 0
        for seed in range(20):
            scripter = MacroScripter(
                seed=seed, typo_model=model, typo_rate=rate
            )
            scripter.generate_script(text)
            fired += sum(scripter.error_kinds.values())
        per_model[model] = fired / (20 * eligible)
    for model, achieved in per_model.items():
        assert achieved == pytest.approx(rate, rel=0.20), (
            f"{model}: achieved {achieved:.4f} against configured {rate}"
        )


# --- script shape under rich -------------------------------------------------


def test_a_rich_error_is_typed_noticed_deleted_and_retyped():
    """Every typo run in the script has the shape the roles promise:
    typo TYPEs, one typo PAUSE, one correction DELETE covering exactly the
    typed characters, correction TYPEs restoring the intended ones."""
    scripter = MacroScripter(seed=2, typo_model="rich", typo_rate=0.3)
    script = scripter.generate_script(PROSE * 2)

    index = 0
    runs = 0
    while index < len(script):
        if script[index].role != ms.ROLE_TYPO or script[index].op != ms.OP_TYPE:
            index += 1
            continue
        typed = 0
        while (
            index < len(script)
            and script[index].role == ms.ROLE_TYPO
            and script[index].op == ms.OP_TYPE
        ):
            typed += 1
            index += 1
        assert script[index].op == ms.OP_PAUSE, "no reaction pause after a typo"
        assert script[index].role == ms.ROLE_TYPO
        index += 1
        assert script[index].op == ms.OP_DELETE
        assert script[index].role == ms.ROLE_CORRECTION
        assert script[index].count == typed, (
            "the correction must delete exactly what the error typed"
        )
        index += 1
        retyped = 0
        while (
            index < len(script)
            and script[index].role == ms.ROLE_CORRECTION
            and script[index].op == ms.OP_TYPE
        ):
            retyped += 1
            index += 1
        assert retyped >= 1
        runs += 1
    assert runs > 0, "the fixture is meant to contain errors"


# --- the default stream is pinned, not just self-consistent ------------------

# SHA-256 of the canonical macro script for a fixed text and seed under the
# DEFAULT model. These digests were taken from the release before
# --typo-model existed and verified identical against it, so they pin the
# generated writing process to what earlier datasets were built from - not
# merely to whatever this tree happens to produce today.
#
# The macro script rather than the keystroke record, deliberately: the script
# is decided entirely by MacroScripter's stdlib random.Random, whose stream
# Python guarantees across versions, while the keystroke timings come from
# numpy's Generator, which carries no such guarantee and would make this test
# fail on a numpy upgrade for no real reason.
#
# A failure here means the default writing process changed. That is sometimes
# intended - but it invalidates every previously generated seeded dataset, so
# it has to be a decision, not a side effect. Regenerate the digests only
# alongside that decision.
GOLDEN_TEXT = (
    "Academic integrity depends on evidence that a piece of writing was "
    "actually composed by the person who submitted it. The process leaves "
    "traces that a finished document does not.\n\n"
    "A second paragraph follows, so paragraph breaks are covered too. "
)

GOLDEN_SCRIPTS = (
    ("plain", 1, {}, "dfe20a5de2b380513ad99d24c3a88e00537f1568847b50be60d905990947e93a"),
    ("plain", 42, {}, "62749f368fd6b0768144cad88d66211f4edb5a8652956dad49cd2cb8f29a3bb5"),
    ("typo_heavy", 7, {"typo_rate": 0.25},
     "2d06583a0235989435b9cd2bd67c220534be4d6d26606d6cf81b824edb124aaa"),
    ("revisions", 11,
     {"r_burst_probability": 1.0, "structural_revision_rate": 1.0},
     "2c36ba0edff877a8a4eb82df5dad6e4a2debe2ecd3a2656d35de9a544906ccb9"),
    ("sessions", 3, {"session_chars": 60},
     "fa0b9d78277639315f593b3baf8c2ade65f43ea8a4af94614df5720c6b7a12bd"),
)


def _script_digest(seed, **kwargs):
    import hashlib

    script = MacroScripter(seed=seed, **kwargs).generate_script(GOLDEN_TEXT)
    blob = json.dumps([event.to_dict() for event in script], sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


@pytest.mark.parametrize(
    "name,seed,kwargs,digest",
    GOLDEN_SCRIPTS,
    ids=[f"{name}-{seed}" for name, seed, _kwargs, _digest in GOLDEN_SCRIPTS],
)
def test_the_default_writing_process_is_unchanged(name, seed, kwargs, digest):
    assert _script_digest(seed, **kwargs) == digest, (
        f"the default writing process changed for {name}/seed {seed}; every "
        "seeded dataset generated by an earlier release is now irreproducible"
    )


def test_the_golden_digests_are_sensitive_to_the_random_stream():
    """The pins above must actually be able to fail.

    A digest test is worthless if it does not notice a perturbed stream, so
    this asserts that consuming one extra draw - the cheapest possible
    accidental regression, and one the rest of the suite does not catch -
    changes the result.
    """
    _name, seed, kwargs, digest = GOLDEN_SCRIPTS[0]
    scripter = MacroScripter(seed=seed, **kwargs)
    scripter._rng.random()
    import hashlib

    script = scripter.generate_script(GOLDEN_TEXT)
    blob = json.dumps([event.to_dict() for event in script], sort_keys=True)
    assert hashlib.sha256(blob.encode()).hexdigest() != digest
