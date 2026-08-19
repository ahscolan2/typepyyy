"""
Project TypeTrace - Emission timeline

Both emitters (docs_emitter for Google Docs, desktop_emitter for the focused
window) replay the same artifact: the "keystrokes" list of a generated record.
That list already carries everything there is to know about when keys go down
and come up, so the scheduling logic lives here exactly once. Each emitter
supplies only a dispatch callback that translates ("down"|"up", keystroke)
into whatever its backend expects.

- iter_timeline() turns the record's absolute millisecond clock into a merged,
  time-ordered stream of (offset_s, event, keystroke) triples.
- run_timeline() sleeps those offsets out against time.monotonic and calls the
  dispatch callback.

This module needs nothing beyond the standard library; the browser and
desktop automation dependencies live in the emitters, imported lazily there.
"""

import time
from typing import Any, Callable, Dict, Iterator, Optional, Tuple

# One merged timeline event: seconds from the start of emission, "down" or
# "up", and the keystroke dict from the record.
TimelineEvent = Tuple[float, str, Dict[str, Any]]

DispatchFn = Callable[[str, Dict[str, Any]], None]
AbortFn = Callable[[], bool]

# Upper bound on one sleep slice while waiting for the next dispatch. The
# remaining time is recomputed after every slice, so the cap costs no
# accuracy; it only bounds how long an abort request can sit unnoticed during
# a long gap.
_ABORT_POLL_S = 0.05


def iter_timeline(
    record: Dict[str, Any],
    *,
    speed: float = 1.0,
    max_gap_s: Optional[float] = None,
) -> Iterator[TimelineEvent]:
    """Yield (offset_s, "down"|"up", keystroke) for the whole record.

    Every keystroke contributes its keydown and its keyup, merged into one
    time order, with the first event at offset 0.0. `speed` > 1 compresses
    time (2.0 replays twice as fast); 0 < speed < 1 stretches it.

    With `max_gap_s`, any silence between one keystroke's keyup and the next
    keystroke's keydown longer than that many seconds is shortened to exactly
    `max_gap_s` - the practical consequence being that multi-hour session
    gaps in the record can be replayed as short pauses. Dwell time (keydown
    to keyup within one keystroke) is never altered. None replays the
    record's timing faithfully.
    """
    if speed <= 0.0:
        raise ValueError(f"speed must be > 0, got {speed!r}")
    if max_gap_s is not None and max_gap_s < 0.0:
        raise ValueError(f"max_gap_s must be >= 0 or None, got {max_gap_s!r}")

    # Shorten silences first: walk the keystrokes in stream order on an
    # adjusted clock from which oversized gaps have been subtracted.
    adjusted = []
    removed_ms = 0.0
    previous_keyup_ms: Optional[float] = None
    for keystroke in record.get("keystrokes", []):
        down_ms = keystroke["keydown_ms"] - removed_ms
        up_ms = keystroke["keyup_ms"] - removed_ms
        if previous_keyup_ms is not None and max_gap_s is not None:
            excess_ms = down_ms - previous_keyup_ms - max_gap_s * 1000.0
            if excess_ms > 0.0:
                removed_ms += excess_ms
                down_ms -= excess_ms
                up_ms -= excess_ms
        previous_keyup_ms = up_ms
        adjusted.append((down_ms, up_ms, keystroke))

    # Merge keydowns and keyups into one stream. The sort key orders a keyup
    # before the next keystroke's keydown on a tie (earlier position wins),
    # and a keystroke's keydown before its own keyup (event order wins).
    merged = []
    for position, (down_ms, up_ms, keystroke) in enumerate(adjusted):
        merged.append((down_ms, position, 0, "down", keystroke))
        merged.append((up_ms, position, 1, "up", keystroke))
    merged.sort(key=lambda item: (item[0], item[1], item[2]))

    origin_ms = merged[0][0] if merged else 0.0
    scale = 1000.0 * speed
    for at_ms, _position, _order, event, keystroke in merged:
        yield ((at_ms - origin_ms) / scale, event, keystroke)


def run_timeline(
    events: Iterator[TimelineEvent],
    dispatch: DispatchFn,
    *,
    initial_delay_s: float = 0.0,
    should_abort: Optional[AbortFn] = None,
) -> Dict[str, Any]:
    """Replay `events` against time.monotonic, calling dispatch() on cue.

    dispatch(event, keystroke) is called when the clock reaches each event's
    offset; offsets are relative to the end of `initial_delay_s` (useful for
    giving the user time to focus a target window). After every dispatch, if
    `should_abort` is given and returns True, the run stops early; it is also
    polled during sleeps so a long pause does not delay an abort by more than
    a fraction of a second.

    Returns {"dispatched": n, "aborted": bool, "duration_s": wall-clock
    seconds from call to return, including the initial delay}.
    """
    started_at = time.monotonic()
    origin = started_at + max(0.0, initial_delay_s)

    def abort_requested() -> bool:
        return should_abort is not None and should_abort()

    dispatched = 0
    aborted = False
    for offset_s, event, keystroke in events:
        target = origin + offset_s
        while True:
            remaining = target - time.monotonic()
            if remaining <= 0.0:
                break
            if abort_requested():
                aborted = True
                break
            time.sleep(min(remaining, _ABORT_POLL_S))
        if aborted:
            break
        dispatch(event, keystroke)
        dispatched += 1
        if abort_requested():
            aborted = True
            break

    return {
        "dispatched": dispatched,
        "aborted": aborted,
        "duration_s": time.monotonic() - started_at,
    }

