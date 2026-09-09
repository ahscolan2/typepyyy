"""Tests for emit_common - the shared timeline that both emitters replay.

iter_timeline turns a record's keystroke clock (absolute milliseconds) into a
sequence of (offset_s, "down"|"up", keystroke) events in time order, optionally
speed-scaled and with long silences capped. run_timeline walks those events
against the wall clock, dispatching each one, and supports early abort.

emit_common is an optional companion module; the whole module skips when it is
not on disk.
"""

import pytest

emit_common = pytest.importorskip("emit_common")


def _keystroke(index, keydown_ms, keyup_ms, kind="key", char="a"):
    """A keystroke dict in the v2 record schema."""
    return {
        "index": index,
        "kind": kind,
        "char": char,
        "keydown_ms": keydown_ms,
        "keyup_ms": keyup_ms,
        "dwell_ms": keyup_ms - keydown_ms,
        "iki_ms": 0,
        "motor_iki_ms": 0,
        "flight_ms": 0,
        "role": "text",
    }


def _record(*keystrokes):
    return {"keystrokes": list(keystrokes), "target_text": ""}


def _offsets_events(events):
    return [(offset, event) for offset, event, _ in events]


def _assert_timeline(events, offsets, names):
    """Same event sequence, and offsets numerically equal (float dust aside)."""
    assert [event for _, event, _ in events] == names
    assert [offset for offset, _, _ in events] == pytest.approx(offsets)


# --- iter_timeline -----------------------------------------------------------


def test_events_merge_downs_and_ups_in_time_order():
    record = _record(
        _keystroke(0, 1000, 1050, char="a"),
        _keystroke(1, 1200, 1260, char="b"),
    )
    timeline = list(emit_common.iter_timeline(record))
    _assert_timeline(timeline, [0.0, 0.05, 0.2, 0.26], ["down", "up", "down", "up"])
    assert [ks["char"] for _, _, ks in timeline] == ["a", "a", "b", "b"]


def test_the_first_event_is_at_zero():
    record = _record(_keystroke(0, 5000, 5100, char="a"))
    first_offset, first_event, _ = next(iter(emit_common.iter_timeline(record)))
    assert first_offset == 0.0
    assert first_event == "down"


def test_speed_two_halves_the_offsets():
    record = _record(
        _keystroke(0, 0, 50, char="a"),
        _keystroke(1, 200, 260, char="b"),
    )
    plain = list(emit_common.iter_timeline(record))
    doubled = list(emit_common.iter_timeline(record, speed=2.0))
    assert [event for _, event, _ in plain] == [event for _, event, _ in doubled]
    assert [offset for offset, _, _ in doubled] == pytest.approx(
        [offset / 2.0 for offset, _, _ in plain]
    )


def test_max_gap_s_caps_long_silences():
    record = _record(
        _keystroke(0, 0, 50, char="a"),
        # A ten-second silence between this keyup and the next keydown.
        _keystroke(1, 10_000, 10_050, char="b"),
    )
    _assert_timeline(
        list(emit_common.iter_timeline(record, max_gap_s=1.0)),
        # keyup at 0.05 s, then the silence is shortened to exactly 1.0 s.
        [0.0, 0.05, 1.05, 1.10],
        ["down", "up", "down", "up"],
    )


def test_max_gap_s_leaves_short_gaps_alone():
    record = _record(
        _keystroke(0, 0, 50, char="a"),
        _keystroke(1, 150, 200, char="b"),
    )
    _assert_timeline(
        list(emit_common.iter_timeline(record, max_gap_s=1.0)),
        [0.0, 0.05, 0.15, 0.2],
        ["down", "up", "down", "up"],
    )


def test_backspace_keystrokes_pass_through():
    record = _record(
        _keystroke(0, 0, 40, char="a"),
        _keystroke(1, 100, 140, kind="backspace", char=None),
    )
    timeline = list(emit_common.iter_timeline(record))
    backspace_events = [
        (event, ks) for _, event, ks in timeline if ks["kind"] == "backspace"
    ]
    assert [event for event, _ in backspace_events] == ["down", "up"]
    assert all(ks["char"] is None for _, ks in backspace_events)


@pytest.mark.parametrize("first,second,kind", [
    ("a", "a", "key"), ("a", "A", "key"), ("!", "1", "key"),
    (None, None, "backspace"),
])
def test_overlapping_physical_repeats_release_before_the_next_press(first, second, kind):
    record = _record(
        _keystroke(0, 0, 200, kind=kind, char=first),
        _keystroke(1, 50, 100, char="b"),
        _keystroke(2, 80, 150, kind=kind, char=second),
    )
    events = list(emit_common.iter_timeline(record))
    assert [(event, ks["index"]) for _, event, ks in events] == [
        ("down", 0), ("down", 1), ("up", 0), ("down", 2), ("up", 1), ("up", 2),
    ]
    assert [offset for offset, _, _ in events] == pytest.approx([0, .05, .08, .08, .1, .15])
    assert record["keystrokes"][0]["keyup_ms"] == 200


def test_repeat_normalization_happens_before_silence_capping():
    record = _record(
        _keystroke(0, 0, 2000, char="a"),
        _keystroke(1, 100, 200, char="A"),
        _keystroke(2, 1000, 1100, char="b"),
    )
    events = list(emit_common.iter_timeline(record, max_gap_s=.1))
    assert [offset for offset, _, _ in events] == pytest.approx([0, .1, .1, .2, .3, .4])


def test_zero_dwell_repeated_keys_each_press_and_release_in_record_order():
    record = _record(*[_keystroke(i, 0, 0) for i in range(3)])
    assert [(event, ks["index"]) for _, event, ks in emit_common.iter_timeline(record)] == [
        ("down", 0), ("up", 0), ("down", 1), ("up", 1), ("down", 2), ("up", 2),
    ]


# --- run_timeline ------------------------------------------------------------


def test_run_timeline_dispatches_every_event_in_order():
    events = list(emit_common.iter_timeline(_record(
        _keystroke(0, 0, 10, char="a"),
        _keystroke(1, 20, 30, char="b"),
    )))
    dispatched = []
    result = emit_common.run_timeline(
        events, lambda event, ks: dispatched.append((event, ks["char"]))
    )
    assert dispatched == [("down", "a"), ("up", "a"), ("down", "b"), ("up", "b")]
    assert result["dispatched"] == 4
    assert result["aborted"] is False
    assert result["duration_s"] >= 0.0


def test_run_timeline_should_abort_stops_early():
    events = list(emit_common.iter_timeline(_record(
        _keystroke(0, 0, 10, char="a"),
        _keystroke(1, 20, 30, char="b"),
        _keystroke(2, 40, 50, char="c"),
    )))
    dispatched = []

    def dispatch(event, ks):
        dispatched.append(event)

    result = emit_common.run_timeline(
        events, dispatch, should_abort=lambda: len(dispatched) >= 2
    )
    assert result["dispatched"] == 2
    assert result["aborted"] is True


def test_backend_cancellation_preserves_completed_dispatch_count():
    def dispatch(event, key):
        if event == "up":
            raise emit_common.ReplayCancelled()

    events = emit_common.iter_timeline(_record(_keystroke(0, 0, 0)))
    assert emit_common.run_timeline(events, dispatch)["dispatched"] == 1


def test_ctrl_c_during_the_initial_abort_check_returns_an_aborted_clock():
    def interrupt():
        raise KeyboardInterrupt()

    clock = emit_common.run_timeline([], lambda *_: None, should_abort=interrupt)
    assert clock["aborted"] is True
    assert clock["dispatched"] == 0
