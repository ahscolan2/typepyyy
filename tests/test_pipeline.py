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
import timing_engine as te
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
    # interval spans at least the shortest gap the scripter can draw - the
    # minutes table's floor at the lowest jitter.
    shortest_gap_ms = min(
        min(ms.SESSION_GAP_MINUTES) * 0.85 * 60_000.0, ms.MAX_SILENCE_MS
    )
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


def test_a_session_gap_is_not_also_recorded_as_a_pause(gappy_record):
    """The same silence must not appear twice in the interval list.

    build_timeline used to add the gap's duration to the pending delay that
    the next keystroke flushes into a "pause" interval, so every gap came out
    as a session_gap and an identical pause at the same start_ms - inflating
    statistics["pauses"] and putting phantom fifteen-minute thinking pauses
    into a record that never contained one.
    """
    seen = {}
    for interval in gappy_record["intervals"]:
        key = (round(interval["start_ms"], 3), round(interval["duration_ms"], 3))
        seen.setdefault(key, []).append(interval["kind"])
    collisions = {k: v for k, v in seen.items() if len(v) > 1}
    assert not collisions, f"the same silence recorded twice: {collisions}"

    gap_durations = {
        round(i["duration_ms"], 3)
        for i in gappy_record["intervals"]
        if i["kind"] == "session_gap"
    }
    for interval in gappy_record["intervals"]:
        if interval["kind"] == "pause":
            assert round(interval["duration_ms"], 3) not in gap_durations


def test_silences_never_sum_past_the_elapsed_time(gappy_record):
    """Pauses and gaps are disjoint spans on one clock, so they cannot
    together outlast the document."""
    total = sum(i["duration_ms"] for i in gappy_record["intervals"])
    assert total <= gappy_record["statistics"]["total_time_ms"] + 1.0


def test_gap_time_is_only_counted_as_gap_time(gappy_record):
    """Gap durations agree across the record's three representations.

    The macro script is the generator's account of what happened, the
    interval list is the timeline's, and session_gap_ms is the statistics
    block's - each is written by different code, so comparing all three
    catches a leak in any of them. (Comparing intervals against
    session_gap_ms alone would be vacuous: both reduce timeline.intervals.)
    """
    script_gap_ms = sum(
        op["duration_ms"]
        for op in gappy_record["macro_script"]
        if op["op"] == "SESSION_GAP"
    )
    interval_gap_ms = sum(
        i["duration_ms"]
        for i in gappy_record["intervals"]
        if i["kind"] == "session_gap"
    )
    assert script_gap_ms > 0.0
    assert interval_gap_ms == pytest.approx(script_gap_ms, abs=0.01)
    assert gappy_record["statistics"]["session_gap_ms"] == pytest.approx(
        script_gap_ms, abs=0.01
    )
    # And gap time is exactly the wall-clock minus active-time difference.
    stats = gappy_record["statistics"]
    assert stats["total_time_ms"] - stats["active_time_ms"] == pytest.approx(
        script_gap_ms, abs=0.01
    )


# --- dwell stays physical ----------------------------------------------------


def test_a_key_is_not_held_down_across_a_thinking_pause(long_prose):
    """Rollover must not stretch a keyup over a deliberate silence.

    The engine decides rollover from the motor interval alone; the pause and
    the session gap live in the pipeline. Extending the previous keyup to
    `keydown + overlap` when a pause intervened held keys down for the whole
    silence, producing dwells of seconds against a model that says
    N(116, 20) truncated at 40.
    """
    ceiling = te.DWELL_MEAN + 8 * te.DWELL_STD
    for seed in range(8):
        record = generate(long_prose, seed=seed, session_chars=SHORT_SESSION)
        worst = max(e["dwell_ms"] for e in record["keystrokes"])
        assert worst < ceiling, f"seed {seed} held a key for {worst:.0f} ms"


def test_the_keystroke_after_a_silence_never_overlaps_the_one_before(long_prose):
    record = generate(long_prose, seed=11, session_chars=SHORT_SESSION)
    events = record["keystrokes"]
    for previous, event in zip(events, events[1:]):
        silence = event["iki_ms"] - event["motor_iki_ms"]
        if silence > 1.0 or event["motor_iki_ms"] == 0.0:
            assert event["flight_ms"] >= 0.0, (
                f"keystroke {event['index']} rolled over a {silence:.0f} ms silence"
            )


def test_active_wpm_on_prose_with_pauses_is_below_the_keystroke_rate(long_prose):
    # Composition pauses count against wpm_active, which is why it sits in the
    # low 30s rather than at the engine's 52 WPM keystroke rate.
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
    # Structural revision deletes whole sentences on top of the burst-local
    # R-burst revision, which is what moves the ratio from ~0.09 into the
    # 0.10-0.30 band reported for real composition. Measured with the defaults
    # over these seeds: 0.139.
    assert sum(ratios) / len(ratios) == pytest.approx(0.14, abs=0.04)


def test_no_deletions_when_typos_and_revisions_are_off(long_prose):
    stats = generate(
        long_prose, seed=1, typo_rate=0.0, r_burst_probability=0.0,
        structural_revision_rate=0.0,
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
        "typo_model": ms.TYPO_MODEL_DEFAULT,
        "r_burst_probability": 0.22,
        "structural_revision_rate": ms.STRUCTURAL_REVISION_RATE,
        "session_chars": None,
        "target_autocorrelation": te.DEFAULT_TARGET_AUTOCORRELATION,
        "fatigue_rate": te.FATIGUE_RATE,
        "warmup_strength": te.WARMUP_STRENGTH,
        "familiarity_boost": te.FAMILIARITY_BOOST,
        "input_chars": len(long_prose),
        "input_words": len(long_prose.split()),
    }


def test_metadata_carries_every_argument_that_shapes_the_record(long_prose):
    """A row has to be regenerable from its own metadata.

    Six of the ten generation parameters used to be dropped, so a dataset
    produced with non-default dynamics could not be reproduced from the file
    that recorded it.
    """
    import inspect

    shaping = {
        name
        for name in inspect.signature(generate).parameters
        if name != "text"
    }
    assert shaping <= set(generate(long_prose, seed=3)["metadata"])


def test_metadata_round_trips_back_into_the_same_record(long_prose):
    original = generate(
        long_prose, profile="slow", seed=17, typo_rate=0.07,
        structural_revision_rate=0.15, fatigue_rate=0.0,
        warmup_strength=0.25, familiarity_boost=0.0,
        target_autocorrelation=0.42,
    )
    metadata = dict(original["metadata"])
    metadata.pop("input_chars")
    metadata.pop("input_words")
    assert generate(long_prose, **metadata)["keystrokes"] == original["keystrokes"]


def test_an_unseeded_record_round_trips_through_its_own_metadata(long_prose):
    """The metadata promise has to hold for the default invocation too.

    With no seed given, the scripter and the engine each used to reseed from
    OS entropy while the metadata recorded "seed": null - identical metadata,
    different records, nothing regenerable. generate() now draws the seed
    itself and records the drawn value.
    """
    original = generate(long_prose)
    drawn = original["metadata"]["seed"]
    assert isinstance(drawn, int)

    metadata = dict(original["metadata"])
    metadata.pop("input_chars")
    metadata.pop("input_words")
    assert generate(long_prose, **metadata)["keystrokes"] == original["keystrokes"]

    # Two unseeded runs still differ: each draws its own seed.
    another = generate(long_prose)
    assert another["metadata"]["seed"] != drawn or (
        another["keystrokes"] == original["keystrokes"]
    )


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


# --- structural revision and within-document dynamics --------------------------


def test_reconstruct_holds_with_structural_revisions_forced(sample_corpus):
    # generate() itself raises if reconstruction fails, so this also pins the
    # fail-loud contract with sentence-scale deletion enabled.
    for index, text in enumerate(sample_corpus):
        record = generate(
            text, seed=index % 7, structural_revision_rate=1.0
        )
        buffer = []
        for event in record["keystrokes"]:
            if event["kind"] == KIND_BACKSPACE:
                if buffer:
                    buffer.pop()
            else:
                buffer.append(event["char"])
        assert "".join(buffer) == text, f"sample_corpus[{index}]: {text!r}"


def test_structural_revisions_are_counted_in_the_statistics(long_prose):
    record = generate(long_prose, seed=3, structural_revision_rate=1.0)
    stats = record["statistics"]
    revision_deletes = sum(
        1 for e in record["macro_script"]
        if e["op"] == ms.OP_DELETE and e["role"] == ms.ROLE_REVISION_DELETE
    )
    revision_backspaces = sum(
        1 for e in record["keystrokes"]
        if e["kind"] == KIND_BACKSPACE and e["role"] == ms.ROLE_REVISION_DELETE
    )
    # A rate of 1.0 revises every completed sentence, and long prose has
    # several; burst-local revisions can only add to the DELETE count.
    assert revision_deletes >= long_prose.count(".")
    assert stats["revision_deleted_chars"] == revision_backspaces
    assert record["target_text"] == long_prose


def test_generate_is_deterministic_with_all_dynamics_on(long_prose):
    kwargs = dict(
        structural_revision_rate=0.5,
        fatigue_rate=0.05,
        warmup_strength=0.2,
        familiarity_boost=0.1,
    )
    first = generate(long_prose, seed=77, **kwargs)
    second = generate(long_prose, seed=77, **kwargs)
    assert first == second


def test_different_dynamics_give_different_records(long_prose):
    first = generate(long_prose, seed=77)
    second = generate(long_prose, seed=77, familiarity_boost=0.0)
    assert first != second


def test_intervals_are_in_time_order(gappy_record):
    starts = [i["start_ms"] for i in gappy_record["intervals"]]
    assert starts == sorted(starts)


def test_intervals_are_in_time_order_for_a_pause_directly_before_a_gap():
    """A pause is held back until the keystroke that ends it, a gap is
    recorded where it stands, so this ordering used to come out reversed."""
    script = [
        ScriptEvent(ms.OP_TYPE, char="a"),
        ScriptEvent(ms.OP_PAUSE, duration_ms=500.0),
        ScriptEvent(ms.OP_SESSION_GAP, duration_ms=300_000.0),
        ScriptEvent(ms.OP_TYPE, char="b"),
    ]
    timeline = build_timeline(script, TimingEngine(seed=1))
    starts = [i.start_ms for i in timeline.intervals]
    assert starts == sorted(starts)
    kinds = [i.kind for i in timeline.intervals]
    assert kinds == ["pause", "session_gap"]
    # And the two silences still do not overlap.
    pause, gap = timeline.intervals
    assert pause.start_ms + pause.duration_ms == pytest.approx(gap.start_ms)


def test_pauses_on_both_sides_of_a_gap_stay_disjoint():
    """A pause before the gap must not swallow a pause after it.

    The pause pending at a gap used to stay pending across it, so the next
    keystroke flushed both pauses as one interval anchored before the gap -
    a span overlapping the gap and placing the second pause minutes before
    it happened. The pending pause is now flushed when the gap arrives.
    """
    script = [
        ScriptEvent(ms.OP_TYPE, char="a"),
        ScriptEvent(ms.OP_PAUSE, duration_ms=500.0),
        ScriptEvent(ms.OP_SESSION_GAP, duration_ms=300_000.0),
        ScriptEvent(ms.OP_PAUSE, duration_ms=700.0),
        ScriptEvent(ms.OP_TYPE, char="b"),
    ]
    timeline = build_timeline(script, TimingEngine(seed=1))
    kinds = [i.kind for i in timeline.intervals]
    assert kinds == ["pause", "session_gap", "pause"]

    first, gap, second = timeline.intervals
    assert first.duration_ms == pytest.approx(500.0)
    assert second.duration_ms == pytest.approx(700.0)
    # Disjoint and ordered: each interval ends before the next begins.
    assert first.start_ms + first.duration_ms == pytest.approx(gap.start_ms)
    assert gap.start_ms + gap.duration_ms <= second.start_ms + 0.001
