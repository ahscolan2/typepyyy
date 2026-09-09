"""Regressions for clocks, script validation, and readable replay."""

import pytest

import macro_scripter as ms
import replay
from error_models import SemanticSubstitution
from macro_scripter import MacroScripter, ScriptEvent
from pipeline import build_timeline
from timing_engine import KeyTiming, TimingEngine


class FixedEngine:
    def __init__(self, *timings):
        self.timings = iter(timings)

    def calibrate(self, _keys):
        pass

    def next_keystroke(self, _char):
        return next(self.timings)

    def reset_context(self):
        pass

    def reset_speed(self):
        pass


@pytest.mark.parametrize("op", [ms.OP_PAUSE, ms.OP_SESSION_GAP])
def test_silence_waits_for_all_held_keys_and_finishes_before_resume(op):
    script = [
        ScriptEvent(ms.OP_TYPE, char="a"),
        ScriptEvent(ms.OP_TYPE, char="b"),
        ScriptEvent(op, duration_ms=500),
        ScriptEvent(ms.OP_TYPE, char="c"),
    ]
    timeline = build_timeline(
        script,
        FixedEngine(KeyTiming(20, 200), KeyTiming(20, 40), KeyTiming(20, 40)),
    )
    interval = timeline.intervals[0]
    assert interval.start_ms == 200
    assert interval.start_ms + interval.duration_ms <= timeline.events[-1].keydown_ms
    assert timeline.events[-1].iki_ms == (
        timeline.events[-1].keydown_ms - timeline.events[-2].keydown_ms
    )


def test_total_time_includes_an_earlier_key_released_after_the_final_key():
    timeline = build_timeline(
        [ScriptEvent(ms.OP_TYPE, char="a"), ScriptEvent(ms.OP_TYPE, char="b")],
        FixedEngine(KeyTiming(20, 200), KeyTiming(20, 40)),
    )
    assert timeline.total_time_ms == 200


@pytest.mark.parametrize("first,second", [("a", "a"), ("a", "A"), ("!", "1")])
def test_physical_repeat_clips_earlier_dwell_and_keeps_flights_and_pauses_consistent(first, second):
    timeline = build_timeline(
        [
            ScriptEvent(ms.OP_TYPE, char=first),
            ScriptEvent(ms.OP_TYPE, char="b"),
            ScriptEvent(ms.OP_TYPE, char=second),
            ScriptEvent(ms.OP_PAUSE, duration_ms=100),
        ],
        FixedEngine(KeyTiming(20, 200), KeyTiming(20, 40), KeyTiming(20, 40)),
    )
    assert [event.keydown_ms for event in timeline.events] == [0, 20, 40]
    assert timeline.events[0].keyup_ms == 40
    assert timeline.events[0].dwell_ms == 40
    assert timeline.events[1].flight_ms == -20
    assert timeline.intervals[0].start_ms == 80
    assert timeline.total_time_ms == 180


def test_leading_and_trailing_pauses_keep_their_time_and_roles():
    script = [
        ScriptEvent(ms.OP_PAUSE, duration_ms=100, role=ms.ROLE_TEXT),
        ScriptEvent(ms.OP_TYPE, char="a"),
        ScriptEvent(ms.OP_PAUSE, duration_ms=200, role=ms.ROLE_TYPO),
        ScriptEvent(ms.OP_PAUSE, duration_ms=300, role=ms.ROLE_CORRECTION),
    ]
    timeline = build_timeline(script, FixedEngine(KeyTiming(20, 40)))
    assert timeline.events[0].keydown_ms == 100
    assert [(i.start_ms, i.duration_ms, i.role) for i in timeline.intervals] == [
        (0, 100, ms.ROLE_TEXT),
        (140, 200, ms.ROLE_TYPO),
        (340, 300, ms.ROLE_CORRECTION),
    ]
    assert timeline.total_time_ms == 640


def test_silence_only_script_has_nonnegative_active_time():
    timeline = build_timeline(
        [
            ScriptEvent(ms.OP_PAUSE, duration_ms=100),
            ScriptEvent(ms.OP_SESSION_GAP, duration_ms=500),
            ScriptEvent(ms.OP_PAUSE, duration_ms=200),
        ],
        FixedEngine(),
    )
    assert timeline.total_time_ms == 800
    assert timeline.active_time_ms == 300


@pytest.mark.parametrize(
    "event",
    [
        ScriptEvent("TYPO"),
        ScriptEvent(ms.OP_TYPE, char=""),
        ScriptEvent(ms.OP_TYPE, char="ab"),
        ScriptEvent(ms.OP_TYPE, char=7),
        ScriptEvent(ms.OP_DELETE, count=1.5),
        ScriptEvent(ms.OP_DELETE, count=True),
        ScriptEvent(ms.OP_PAUSE, duration_ms=-1),
        ScriptEvent(ms.OP_PAUSE, duration_ms=float("nan")),
        ScriptEvent(ms.OP_SESSION_GAP, duration_ms=float("inf")),
    ],
)
def test_malformed_operations_are_rejected_by_replay_and_timing(event):
    with pytest.raises(ValueError):
        ms.replay([event])
    with pytest.raises(ValueError):
        build_timeline([event], FixedEngine())


@pytest.mark.parametrize("value", [True, 0.5, float("nan"), float("inf"), "10"])
def test_session_length_requires_a_positive_integer(value):
    with pytest.raises(ValueError, match="session_chars"):
        MacroScripter(session_chars=value)


def test_the_final_word_can_start_a_new_session():
    script = MacroScripter(
        seed=1, session_chars=2, typo_rate=0, structural_revision_rate=0
    ).generate_script("a b")
    gap = next(index for index, event in enumerate(script) if event.op == ms.OP_SESSION_GAP)
    assert script[gap + 1].char == "b"
    assert ms.replay(script) == "a b"


def test_error_counts_describe_the_current_document():
    scripter = MacroScripter(seed=1, typo_rate=1)
    scripter.generate_script("abc")
    assert scripter.error_kinds
    scripter.generate_script("")
    assert scripter.error_kinds == {}


@pytest.mark.parametrize("value", [-1, 0, 1, 2, float("nan"), float("inf")])
def test_latent_persistence_must_be_stationary(value):
    with pytest.raises(ValueError, match="phi"):
        TimingEngine(phi=value)


def test_infinite_fatigue_is_rejected_before_generating_nan_timestamps():
    with pytest.raises(ValueError, match="fatigue_rate"):
        TimingEngine(fatigue_rate=float("inf"))


@pytest.mark.parametrize(
    "text", ["éits", "itsé", "2its", "its2", "_its", "its_", "its\u0301", "a\u0301its", "who'd've"]
)
def test_semantic_replacements_do_not_match_inside_unicode_words_or_identifiers(text):
    assert SemanticSubstitution(seed=1).maybe_substitute(text) == (text, None)


def test_semantic_replacements_still_match_quoted_whole_words():
    assert [m.group() for m in SemanticSubstitution().candidates("'its' and “their”")] == [
        "its", "their"
    ]


def test_replay_shows_pauses_before_the_action_with_the_pre_action_text():
    timeline = build_timeline(
        [
            ScriptEvent(ms.OP_PAUSE, duration_ms=500),
            ScriptEvent(ms.OP_TYPE, char="x", role=ms.ROLE_TYPO),
            ScriptEvent(ms.OP_PAUSE, duration_ms=600, role=ms.ROLE_TYPO),
            ScriptEvent(ms.OP_DELETE, count=1, role=ms.ROLE_CORRECTION),
            ScriptEvent(ms.OP_PAUSE, duration_ms=700),
        ],
        FixedEngine(KeyTiming(20, 40), KeyTiming(20, 40)),
    )
    moments = replay._moments(
        {
            "keystrokes": [e.to_dict() for e in timeline.events],
            "intervals": [i.to_dict() for i in timeline.intervals],
        },
        400,
    )
    assert [(m["kind"], m["text"]) for m in moments] == [
        ("pause", ""), ("typo", "x"), ("pause", "x"),
        ("correction", ""), ("pause", ""),
    ]
    assert [m["at_ms"] for m in moments] == sorted(m["at_ms"] for m in moments)


def test_collapsed_document_state_uses_the_time_of_the_final_key_in_the_run():
    moments = [
        {"kind": "typing", "at_ms": 100, "text": "a"},
        {"kind": "typing", "at_ms": 200, "text": "ab"},
    ]
    assert replay._collapse(moments) == [
        {"kind": "run", "at_ms": 200, "text": "ab", "chars": 2}
    ]


@pytest.mark.parametrize("width", [-1, 1.5, True])
def test_replay_rejects_invalid_column_width_before_rendering(width):
    with pytest.raises(ValueError, match="width"):
        replay.render({}, width=width)


@pytest.mark.parametrize("threshold", [-1, float("nan"), float("inf")])
def test_replay_rejects_invalid_pause_threshold_before_rendering(threshold):
    with pytest.raises(ValueError, match="pause_threshold_ms"):
        replay.render({}, pause_threshold_ms=threshold)
