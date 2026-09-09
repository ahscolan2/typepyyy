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

import math
import time
import unicodedata
from typing import Any, Callable, Dict, Iterator, Optional, Tuple

# One merged timeline event: seconds from the start of emission, "down" or
# "up", and the keystroke dict from the record.
TimelineEvent = Tuple[float, str, Dict[str, Any]]

DispatchFn = Callable[[str, Dict[str, Any]], None]
AbortFn = Callable[[], bool]


class ReplayCancelled(Exception):
    """A backend lost its target or was cancelled during a dispatch."""

# Upper bound on one sleep slice while waiting for the next dispatch. The
# remaining time is recomputed after every slice, so the cap costs no
# accuracy; it only bounds how long an abort request can sit unnoticed during
# a long gap.
_ABORT_POLL_S = 0.05

# US-QWERTY physical keys, shared by the timing model and both emitters.
SHIFTED_TO_BASE = {
    "~": "`", "!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6",
    "&": "7", "*": "8", "(": "9", ")": "0", "_": "-", "+": "=", "{": "[",
    "}": "]", "|": "\\", ":": ";", '"': "'", "<": ",", ">": ".", "?": "/",
}


def physical_key(char: str) -> str:
    """Return the unshifted US key, preserving literal non-ASCII characters."""
    if "A" <= char <= "Z":
        return char.lower()
    return SHIFTED_TO_BASE.get(char, char)


def finite_number(value: float, name: str, *, positive: bool = False) -> float:
    """Validate a replay option before opening a browser or touching keys."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    if not math.isfinite(value) or value < 0 or (positive and value == 0):
        bound = "greater than 0" if positive else "0 or greater"
        raise ValueError(f"{name} must be finite and {bound}")
    return float(value)


def validate_live_text(record: dict) -> None:
    """Reject keys that would invoke controls or split editor grapheme deletion."""
    buffer = []
    for key in record.get("keystrokes", []):
        if key["kind"] == "backspace":
            if not buffer:
                raise ValueError("replay would backspace before the start of its text")
            char = buffer[-1]
            code = ord(char)
            if (unicodedata.category(char).startswith("M")
                    or char == "\u200d" or (len(buffer) > 1 and buffer[-2] == "\u200d")
                    or 0x1F1E6 <= code <= 0x1F1FF or 0x1F3FB <= code <= 0x1F3FF
                    or 0x1100 <= code <= 0x11FF or 0xE0020 <= code <= 0xE007F):
                raise ValueError(
                    "replay deletes within a Unicode grapheme (combined accent or emoji); "
                    "use text without these clusters, or disable typos and revisions"
                )
            buffer.pop()
        else:
            char = key["char"]
            if unicodedata.category(char) in ("Cc", "Cs") and char not in "\n\t":
                raise ValueError(f"unsupported live-editor control character {char!r}; use LF newlines")
            buffer.append(char)


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
    to keyup within one keystroke) is unchanged by gap capping. If a record
    overlaps two presses of the same physical key, the earlier release is
    brought forward to the next press: a key must come up before being
    pressed again. The input record is left unchanged.
    """
    speed = finite_number(speed, "speed", positive=True)
    if max_gap_s is not None:
        max_gap_s = finite_number(max_gap_s, "max_gap_s")
    if not isinstance(record, dict) or not isinstance(record.get("keystrokes"), list):
        raise ValueError("record must contain a keystrokes list")

    # Shorten silences first: walk the keystrokes in stream order on an
    # adjusted clock from which oversized gaps have been subtracted.
    validated = []
    previous_by_key = {}
    previous_down_ms = -1.0
    for keystroke in record["keystrokes"]:
        if not isinstance(keystroke, dict) or keystroke.get("kind") not in ("key", "backspace"):
            raise ValueError("each keystroke must have kind 'key' or 'backspace'")
        if keystroke["kind"] == "key" and (
            not isinstance(keystroke.get("char"), str) or len(keystroke["char"]) != 1
        ):
            raise ValueError("each key must contain a single-character char")
        down = finite_number(keystroke.get("keydown_ms"), "keydown_ms")
        up = finite_number(keystroke.get("keyup_ms"), "keyup_ms")
        if down < previous_down_ms or up < down:
            raise ValueError("keystrokes must have ordered keydowns and keyup >= keydown")
        previous_down_ms = down
        identity = physical_key("\b" if keystroke["kind"] == "backspace" else keystroke["char"])
        previous = previous_by_key.get(identity)
        if previous is not None:
            previous[1] = min(previous[1], down)
        current = [down, up, keystroke]
        validated.append(current)
        previous_by_key[identity] = current

    adjusted = []
    removed_ms = 0.0
    previous_keyup_ms: Optional[float] = None
    for down, up, keystroke in validated:
        down_ms = down - removed_ms
        up_ms = up - removed_ms
        if previous_keyup_ms is not None and max_gap_s is not None:
            excess_ms = down_ms - previous_keyup_ms - max_gap_s * 1000.0
            if excess_ms > 0.0:
                removed_ms += excess_ms
                down_ms -= excess_ms
                up_ms -= excess_ms
        previous_keyup_ms = max(previous_keyup_ms or 0.0, up_ms)
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
    initial_delay_s = finite_number(initial_delay_s, "initial_delay_s")
    started_at = time.monotonic()
    origin = started_at + initial_delay_s

    def abort_requested() -> bool:
        return should_abort is not None and should_abort()

    dispatched = 0
    aborted = False
    previous_offset = 0.0
    try:
        aborted = abort_requested()
        for offset_s, event, keystroke in events:
            offset_s = finite_number(offset_s, "event offset")
            if offset_s < previous_offset or event not in ("down", "up"):
                raise ValueError("timeline events must be ordered down/up events")
            previous_offset = offset_s
            if aborted or abort_requested():
                aborted = True
                break
            target = origin + offset_s
            while True:
                remaining = target - time.monotonic()
                if remaining <= 0.0:
                    break
                if abort_requested():
                    aborted = True
                    break
                time.sleep(min(remaining, _ABORT_POLL_S))
            if aborted or abort_requested():
                aborted = True
                break
            dispatch(event, keystroke)
            dispatched += 1
            if abort_requested():
                aborted = True
                break
    except (KeyboardInterrupt, ReplayCancelled):
        aborted = True

    return {
        "dispatched": dispatched,
        "aborted": aborted,
        "duration_s": time.monotonic() - started_at,
    }
