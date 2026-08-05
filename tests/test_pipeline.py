"""Tests for pipeline.

The headline invariant is `reconstruct(build_timeline(...).events) == text`.
The module exists because an earlier version violated it - a 210-character
target produced 215 keystroke characters - so it is checked over the whole
corpus rather than on a sample paragraph.

The rest of the file checks that the single clock is consistent: keydowns
ordered, keyups after keydowns, flight negative exactly when keys overlap, and
every statistic derivable from the script and keystroke lists that the record
also ships.
"""

import json
import math

import pytest

import macro_scripter as ms
import pipeline
from macro_scripter import MacroScripter, ScriptEvent
from pipeline import (
    KIND_BACKSPACE,
    KIND_KEY,
    Timeline,
    build_timeline,
    generate,
    lag1_autocorrelation,
    reconstruct,
    script_key_sequence,
    summarize,
)
from timing_engine import BACKSPACE, TimingEngine

from conftest import EDGE_CASES

# Small enough that any text of a few hundred characters is split across
# several writing sessions.
SHORT_SESSION = 60


def make_timeline(text, seed=1, **kwargs):
    """Script and time `text`, returning (script, timeline)."""
    scripter_kwargs = {
        key: value for key, value in kwargs.items()
        if key in ("typo_rate", "r_burst_probability", "session_chars")
    }
    script = MacroScripter(seed=seed, **scripter_kwargs).generate_script(text)
    engine = TimingEngine(profile=kwargs.get("profile", "average"), seed=seed)
    return script, build_timeline(script, engine)


@pytest.fixture
def record(long_prose):
    return generate(long_prose, seed=20240617)


@pytest.fixture
def gappy_record(long_prose):
    return generate(long_prose, seed=20240617, session_chars=SHORT_SESSION)


# --- the headline invariant --------------------------------------------------


def test_reconstruct_reproduces_the_corpus(corpus):
    for index, text in enumerate(corpus):
        _script, timeline = make_timeline(text, seed=index % 7)
        assert reconstruct(timeline.events) == text, f"corpus[{index}]: {text!r}"


@pytest.mark.parametrize("name", sorted(EDGE_CASES))
def test_reconstruct_reproduces_edge_cases(name):
    text = EDGE_CASES[name]
    _script, timeline = make_timeline(text, seed=3)
    assert reconstruct(timeline.events) == text


@pytest.mark.parametrize(
    "typo_rate,r_burst_probability,session_chars",
    [(0.0, 0.0, None), (1.0, 1.0, None), (0.4, 0.4, SHORT_SESSION)],
)
def test_reconstruct_holds_at_parameter_extremes(
    sample_corpus, typo_rate, r_burst_probability, session_chars
):
    for text in sample_corpus:
        _script, timeline = make_timeline(
            text,
            seed=2,
            typo_rate=typo_rate,
            r_burst_probability=r_burst_probability,
            session_chars=session_chars,
        )
        assert reconstruct(timeline.events) == text


def test_keystroke_count_matches_the_script(sample_corpus):
    for text in sample_corpus:
        script, timeline = make_timeline(text, seed=4, typo_rate=0.2)
        assert len(timeline.events) == len(script_key_sequence(script))


def test_a_literal_backspace_in_the_text_is_typed_rather_than_deleting():
    # U+0008 is the keystroke stream's backspace marker. As input text it is an
    # ordinary character, and the event kind has to come from the script op
    # rather than from the character, or reconstruction eats what precedes it.
    text = "ab\bcd"
    _script, timeline = make_timeline(text, seed=1, typo_rate=0.0)
    assert reconstruct(timeline.events) == text
    assert [e.kind for e in timeline.events] == [KIND_KEY] * len(text)


# --- clock consistency -------------------------------------------------------


def test_keydowns_are_non_decreasing(sample_corpus):
    for text in sample_corpus:
        _script, timeline = make_timeline(text, seed=5, session_chars=SHORT_SESSION)
        keydowns = [e.keydown_ms for e in timeline.events]
        assert keydowns == sorted(keydowns)


def test_every_key_comes_up_after_it_goes_down(sample_corpus):
    for text in sample_corpus:
        _script, timeline = make_timeline(text, seed=5, typo_rate=0.2)
        for event in timeline.events:
            assert event.keyup_ms > event.keydown_ms
            assert event.dwell_ms > 0.0
            assert event.keyup_ms == pytest.approx(
                event.keydown_ms + event.dwell_ms
            )


def test_times_are_finite(record):
    for event in record["keystrokes"]:
        for key in ("keydown_ms", "keyup_ms", "dwell_ms", "iki_ms", "flight_ms"):
            assert math.isfinite(event[key])


def test_indices_are_dense_and_ordered(record):
    assert [e["index"] for e in record["keystrokes"]] == list(
        range(len(record["keystrokes"]))
    )


def test_first_keystroke_has_no_interval(record):
    assert record["keystrokes"][0]["iki_ms"] == 0.0
    assert record["keystrokes"][0]["motor_iki_ms"] == 0.0
    assert record["keystrokes"][0]["flight_ms"] == 0.0


# --- rollover ----------------------------------------------------------------


def test_flight_is_negative_exactly_when_the_keys_overlap(long_prose):
    _script, timeline = make_timeline(long_prose, seed=8)
    for previous, event in zip(timeline.events, timeline.events[1:]):
        overlapped = event.keydown_ms < previous.keyup_ms
        assert (event.flight_ms < 0.0) is overlapped
        assert event.flight_ms == pytest.approx(
            event.keydown_ms - previous.keyup_ms
        )


def test_rollover_keystrokes_counts_the_negative_flights(record):
    negative = sum(1 for e in record["keystrokes"] if e["flight_ms"] < 0.0)
    assert record["statistics"]["rollover_keystrokes"] == negative


def test_rollover_rate_is_in_the_measured_band(long_prose):
    # The lead measured ~13% of keystrokes. The band is wide because the rate
    # depends on the digraph mix of the text, but it catches a model that has
    # stopped rolling over at all or one that rolls over everything.
    record = generate(long_prose * 2, seed=1)
    stats = record["statistics"]
    rate = stats["rollover_keystrokes"] / stats["keystrokes"]
    assert 0.05 <= rate <= 0.25


def test_a_rolled_over_key_is_held_at_least_as_long_as_its_dwell(long_prose):
    _script, timeline = make_timeline(long_prose, seed=8)
    for event in timeline.events:
        assert event.dwell_ms == pytest.approx(event.keyup_ms - event.keydown_ms)


# --- pauses, sessions and elapsed time ---------------------------------------


def test_iki_is_never_below_the_motor_component(sample_corpus):
    # A deliberate pause is additive on top of the motor interval, so the full
    # interval can only be the larger of the two.
    for text in sample_corpus:
        _script, timeline = make_timeline(
            text, seed=6, session_chars=SHORT_SESSION
        )
        for event in timeline.events:
            assert event.iki_ms >= event.motor_iki_ms


def test_motor_interval_is_zero_at_the_start_and_after_every_session_gap(
    gappy_record,
):
    events = gappy_record["keystrokes"]
    gaps = gappy_record["statistics"]["session_gaps"]
    assert gaps >= 3, "the fixture is meant to contain several session gaps"

    zeros = [e for e in events if e["motor_iki_ms"] == 0.0]
    assert len(zeros) == gaps + 1
    assert zeros[0]["index"] == 0

    # Every other zero is a keystroke that resumed after a gap, so its full
    # interval spans at least the shortest gap the scripter can draw.
    shortest_gap_ms = min(ms.SESSION_GAP_HOURS) * 0.85 * 3_600_000.0
    for event in zeros[1:]:
        assert event["iki_ms"] >= shortest_gap_ms


def test_no_session_gaps_without_a_short_session_limit(record):
    assert record["statistics"]["session_gaps"] == 0
    assert record["statistics"]["session_gap_ms"] == 0.0


def test_session_gap_time_is_excluded_from_active_time(gappy_record):
    stats = gappy_record["statistics"]
    assert stats["session_gap_ms"] > 0.0
    assert stats["active_time_ms"] == pytest.approx(
        stats["total_time_ms"] - stats["session_gap_ms"], abs=0.01
    )
    assert stats["active_time_ms"] < stats["total_time_ms"]


def test_wall_clock_wpm_is_lower_than_active_wpm_when_gaps_exist(gappy_record):
    stats = gappy_record["statistics"]
    assert stats["wpm_wall_clock"] < stats["wpm_active"]


def test_wall_clock_wpm_equals_active_wpm_without_gaps(record):
    stats = record["statistics"]
    assert stats["wpm_wall_clock"] == stats["wpm_active"]


def test_gap_intervals_are_recorded_with_their_start_and_duration(gappy_record):
    gaps = [i for i in gappy_record["intervals"] if i["kind"] == "session_gap"]
    assert len(gaps) == gappy_record["statistics"]["session_gaps"]
    for gap in gaps:
        assert gap["duration_ms"] > 0.0
        assert gap["start_ms"] >= 0.0


def test_pause_intervals_are_counted(record):
    pauses = [i for i in record["intervals"] if i["kind"] == "pause"]
    assert len(pauses) == record["statistics"]["pauses"]
    assert pauses, "prose with word boundaries should contain pauses"


def test_active_wpm_on_prose_with_pauses_is_below_the_keystroke_rate(long_prose):
    # Composition pauses count against wpm_active, which is why it sits near 37
    # rather than the engine's 52 WPM keystroke rate.
    rates = [generate(long_prose, seed=s)["statistics"]["wpm_active"] for s in range(8)]
    assert 28.0 <= sum(rates) / len(rates) <= 46.0


# --- statistics agree with the record ----------------------------------------


def test_statistics_agree_with_the_script_and_keystrokes(long_prose):
    for seed in range(6):
        record = generate(
            long_prose, seed=seed, typo_rate=0.15, r_burst_probability=0.5
        )
        stats = record["statistics"]
        script = record["macro_script"]
        keys = record["keystrokes"]

        typed = [e for e in script if e["op"] == ms.OP_TYPE]
        deleted = sum(e["count"] for e in script if e["op"] == ms.OP_DELETE)
        backspaces = [e for e in keys if e["kind"] == KIND_BACKSPACE]
        characters = [e for e in keys if e["kind"] == KIND_KEY]

        assert stats["keystrokes"] == len(keys)
        assert stats["character_keystrokes"] == len(characters) == len(typed)
        assert stats["backspaces"] == len(backspaces) == deleted

        assert stats["typo_keystrokes"] == sum(
            1 for e in typed if e["role"] == ms.ROLE_TYPO
        )
        assert stats["revision_deleted_chars"] == sum(
            e["count"] for e in script
            if e["op"] == ms.OP_DELETE and e["role"] == ms.ROLE_REVISION_DELETE
        )
        assert stats["revision_deleted_chars"] == sum(
            1 for e in backspaces if e["role"] == ms.ROLE_REVISION_DELETE
        )
        assert stats["deletion_ratio"] == pytest.approx(
            len(backspaces) / len(characters), abs=1e-4
        )


KNOWN_ROLES = frozenset({
    ms.ROLE_TEXT, ms.ROLE_TYPO, ms.ROLE_CORRECTION,
    ms.ROLE_REVISION_DELETE, ms.ROLE_REVISION_RETYPE,
})


def test_every_keystroke_carries_a_known_role(long_prose):
    # A keystroke's role is what a downstream consumer labels it by, so an
    # unrecognised one is a silently unlabelled training example.
    record = generate(long_prose, seed=3, typo_rate=0.2, r_burst_probability=0.6)
    for event in record["keystrokes"]:
        assert event["role"] in KNOWN_ROLES
    for interval in record["intervals"]:
        assert interval["role"] in KNOWN_ROLES


def test_roles_survive_from_the_script_to_the_keystrokes(long_prose):
    # The script decides intent; the timeline must not relabel it.
    script, timeline = make_timeline(
        long_prose, seed=3, typo_rate=0.2, r_burst_probability=0.6
    )
    expected = []
    for event in script:
        if event.op == ms.OP_TYPE:
            expected.append(event.role)
        elif event.op == ms.OP_DELETE:
            expected.extend([event.role] * event.count)
    assert [e.role for e in timeline.events] == expected


def test_deletion_ratio_at_defaults_is_near_the_measured_value(long_prose):
    ratios = [
        generate(long_prose, seed=seed)["statistics"]["deletion_ratio"]
        for seed in range(12)
    ]
    assert sum(ratios) / len(ratios) == pytest.approx(0.09, abs=0.04)


def test_no_deletions_when_typos_and_revisions_are_off(long_prose):
    stats = generate(
        long_prose, seed=1, typo_rate=0.0, r_burst_probability=0.0
    )["statistics"]
    assert stats["backspaces"] == 0
    assert stats["deletion_ratio"] == 0.0
    assert stats["typo_keystrokes"] == 0
    assert stats["revision_deleted_chars"] == 0


def test_mean_dwell_is_near_the_documented_value(long_prose):
    means = [
        generate(long_prose, seed=seed)["statistics"]["mean_dwell_ms"]
        for seed in range(8)
    ]
    # 116ms from the dwell distribution, plus a small genuine extension on the
    # keystrokes that another key rolls over.
    assert 112.0 <= sum(means) / len(means) <= 128.0


def test_reported_autocorrelation_matches_the_target(long_prose):
    values = [
        generate(long_prose * 2, seed=seed, target_autocorrelation=0.35)[
            "statistics"
        ]["lag1_autocorrelation"]
        for seed in range(20)
    ]
    assert sum(values) / len(values) == pytest.approx(0.35, abs=0.06)


# --- lag1_autocorrelation ----------------------------------------------------


@pytest.mark.parametrize("values", [[], [1.0], [1.0, 2.0], [0.0, 0.0, 0.0]])
def test_autocorrelation_of_a_degenerate_series_is_zero(values):
    assert lag1_autocorrelation(values) == 0.0


def test_autocorrelation_of_a_constant_series_is_zero():
    assert lag1_autocorrelation([5.0] * 50) == 0.0


def test_autocorrelation_stays_inside_the_unit_interval():
    # A short series with a differently normalised numerator used to produce
    # values outside [-1, 1], which is not a correlation.
    for length in range(3, 40):
        values = [float(i % 3 + 1) for i in range(length)]
        assert -1.0 <= lag1_autocorrelation(values) <= 1.0


def test_autocorrelation_detects_a_perfectly_persistent_series():
    values = [math.exp(i * 0.01) for i in range(200)]
    assert lag1_autocorrelation(values) > 0.95


def test_autocorrelation_detects_alternation():
    values = [100.0 if i % 2 else 10.0 for i in range(200)]
    assert lag1_autocorrelation(values) < -0.9


def test_autocorrelation_ignores_non_positive_values():
    # log() is undefined there; the estimator drops them rather than raising.
    assert lag1_autocorrelation([1.0, -1.0, 2.0, 0.0, 3.0, 4.0]) != 0.0


# --- generate() contract -----------------------------------------------------


@pytest.mark.parametrize("bad", [None, 42, 3.5, b"bytes", ["a"], {"a": 1}])
def test_generate_rejects_non_string_input(bad):
    with pytest.raises(TypeError, match="text must be str"):
        generate(bad)


def test_generate_raises_when_the_script_does_not_replay(monkeypatch):
    class BrokenScripter:
        def __init__(self, **_kwargs):
            pass

        def generate_script(self, _text):
            return [ScriptEvent(ms.OP_TYPE, char="x")]

    monkeypatch.setattr(pipeline, "MacroScripter", BrokenScripter)
    with pytest.raises(ValueError, match="macro script does not reproduce"):
        generate("hello world", seed=1)


def test_generate_raises_when_the_timeline_does_not_reproduce(monkeypatch):
    monkeypatch.setattr(pipeline, "reconstruct", lambda _events: "wrong")
    with pytest.raises(ValueError, match="keystroke timeline does not reproduce"):
        generate("hello world", seed=1)


def test_generate_propagates_scripter_validation():
    with pytest.raises(ValueError, match="typo_rate"):
        generate("hello", typo_rate=2.0)


def test_generate_propagates_engine_validation():
    with pytest.raises(ValueError, match="unknown profile"):
        generate("hello", profile="turbo")
    with pytest.raises(ValueError, match="target_autocorrelation"):
        generate("hello", target_autocorrelation=0.99)


def test_generate_on_empty_text_produces_an_empty_record():
    record = generate("", seed=1)
    assert record["target_text"] == ""
    assert record["keystrokes"] == []
    assert record["statistics"]["keystrokes"] == 0
    assert record["statistics"]["wpm_active"] == 0.0


def test_build_timeline_rejects_an_unknown_op():
    with pytest.raises(ValueError, match="unknown script op"):
        build_timeline([ScriptEvent("JUMP")], TimingEngine(seed=1))


def test_generate_is_deterministic(long_prose):
    first = generate(long_prose, seed=77, session_chars=SHORT_SESSION)
    second = generate(long_prose, seed=77, session_chars=SHORT_SESSION)
    assert first == second


def test_different_seeds_give_different_records(long_prose):
    first = generate(long_prose, seed=1)
    second = generate(long_prose, seed=2)
    assert first["keystrokes"] != second["keystrokes"]
    assert first["target_text"] == second["target_text"]


def test_metadata_describes_the_request(long_prose):
    record = generate(
        long_prose, profile="fast", seed=9, typo_rate=0.11,
        r_burst_probability=0.22,
    )
    assert record["metadata"] == {
        "profile": "fast",
        "seed": 9,
        "typo_rate": 0.11,
        "r_burst_probability": 0.22,
        "input_chars": len(long_prose),
        "input_words": len(long_prose.split()),
    }


# --- serialisation -----------------------------------------------------------


def _leaked_types(value, path="record"):
    """Every leaf whose type is not a JSON-native builtin, with its path."""
    if isinstance(value, dict):
        return [
            leak
            for key, item in value.items()
            for leak in _leaked_types(item, f"{path}.{key}")
        ]
    if isinstance(value, list):
        return [
            leak
            for index, item in enumerate(value)
            for leak in _leaked_types(item, f"{path}[{index}]")
        ]
    if type(value) in (str, bool, int, float, type(None)):
        return []
    return [(path, type(value).__name__)]


def test_the_record_contains_no_numpy_scalars(gappy_record):
    # numpy scalars serialise as numbers under some encoders and raise under
    # json.dumps, so a leak is only found when someone tries to write the file.
    assert _leaked_types(gappy_record) == []


def test_the_record_round_trips_through_json(gappy_record):
    assert json.loads(json.dumps(gappy_record)) == gappy_record


def test_every_numeric_statistic_is_a_builtin(record):
    for key, value in record["statistics"].items():
        assert type(value) in (int, float), f"{key} is {type(value).__name__}"


def test_keyup_minus_keydown_recovers_dwell_after_rounding(record):
    for event in record["keystrokes"]:
        assert event["keyup_ms"] - event["keydown_ms"] == pytest.approx(
            event["dwell_ms"], abs=1e-9
        )


def test_backspace_events_carry_no_character(gappy_record):
    for event in gappy_record["keystrokes"]:
        if event["kind"] == KIND_BACKSPACE:
            assert event["char"] is None
        else:
            assert event["char"] is not None


# --- helpers -----------------------------------------------------------------


def test_script_key_sequence_expands_deletes():
    script = [
        ScriptEvent(ms.OP_TYPE, char="a"),
        ScriptEvent(ms.OP_PAUSE, duration_ms=10.0),
        ScriptEvent(ms.OP_DELETE, count=3),
        ScriptEvent(ms.OP_TYPE, char="b"),
    ]
    assert script_key_sequence(script) == ["a", BACKSPACE, BACKSPACE, BACKSPACE, "b"]


def test_reconstruct_ignores_a_backspace_on_an_empty_buffer():
    # reconstruct is a description of what the keystrokes do, not a validator;
    # replay() is where a malformed script is rejected.
    from pipeline import KeyEvent

    event = KeyEvent(
        index=0, kind=KIND_BACKSPACE, char=None, keydown_ms=0.0, keyup_ms=1.0,
        dwell_ms=1.0, iki_ms=0.0, motor_iki_ms=0.0, flight_ms=0.0,
        role=ms.ROLE_CORRECTION,
    )
    assert reconstruct([event]) == ""


def test_empty_timeline_reports_zero_time():
    timeline = Timeline()
    assert timeline.total_time_ms == 0.0
    assert timeline.session_gap_ms == 0.0
    assert timeline.active_time_ms == 0.0


def test_summarize_on_an_empty_timeline():
    stats = summarize("", Timeline(), [])
    assert stats["keystrokes"] == 0
    assert stats["mean_iki_ms"] == 0.0
    assert stats["mean_dwell_ms"] == 0.0
    assert stats["lag1_autocorrelation"] == 0.0
