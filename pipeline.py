"""
Project Aletheia - Pipeline

Joins the macro script (what happens) to the timing engine (when it happens)
on a single monotonic clock, and derives the output record.

This module exists because the two halves used to be joined incorrectly. The
old code concatenated every TYPE character into one string - including typo
characters that the script later deleted - and fed that string to the timing
engine. The resulting keystroke stream did not reproduce the target text
(210 characters of target against 215 of keystrokes on a sample paragraph),
carried none of the pause or session-gap time, and reported typo flags at
indices computed against a different string than the one being indexed.

Here there is one event list, one clock, and one invariant:

    reconstruct(build_timeline(...).events) == target_text
"""

import math
from dataclasses import dataclass, field
from typing import List, Optional

import macro_scripter as ms
from macro_scripter import MacroScripter, ScriptEvent
from timing_engine import BACKSPACE, TimingEngine

KIND_KEY = "key"
KIND_BACKSPACE = "backspace"


@dataclass
class KeyEvent:
    """One physical keystroke on the absolute timeline. Times in ms."""

    index: int
    kind: str
    char: Optional[str]
    keydown_ms: float
    keyup_ms: float
    dwell_ms: float
    iki_ms: float
    motor_iki_ms: float
    flight_ms: float
    role: str

    def to_dict(self) -> dict:
        # keyup is derived from the rounded keydown and dwell rather than
        # rounded independently, so a consumer that computes keyup - keydown
        # gets exactly dwell_ms back instead of being off by a rounding step.
        keydown = round(self.keydown_ms, 3)
        dwell = round(self.dwell_ms, 3)
        return {
            "index": self.index,
            "kind": self.kind,
            "char": self.char,
            "keydown_ms": keydown,
            "keyup_ms": round(keydown + dwell, 3),
            "dwell_ms": dwell,
            # iki_ms is the full keydown-to-keydown interval, including any
            # deliberate pause. motor_iki_ms is the motor component alone -
            # what the timing engine models. Statistics about typing rhythm
            # use the motor component; statistics about elapsed time use the
            # full interval.
            "iki_ms": round(self.iki_ms, 3),
            "motor_iki_ms": round(self.motor_iki_ms, 3),
            "flight_ms": round(self.flight_ms, 3),
            "role": self.role,
        }


@dataclass
class Interval:
    """A pause or session gap on the absolute timeline."""

    kind: str  # "pause" | "session_gap"
    start_ms: float
    duration_ms: float
    role: str

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "start_ms": round(self.start_ms, 3),
            "duration_ms": round(self.duration_ms, 3),
            "role": self.role,
        }


@dataclass
class Timeline:
    events: List[KeyEvent] = field(default_factory=list)
    intervals: List[Interval] = field(default_factory=list)

    @property
    def total_time_ms(self) -> float:
        return self.events[-1].keyup_ms if self.events else 0.0

    @property
    def session_gap_ms(self) -> float:
        return sum(i.duration_ms for i in self.intervals if i.kind == "session_gap")

    @property
    def active_time_ms(self) -> float:
        """Wall-clock time minus the gaps between writing sessions."""
        return self.total_time_ms - self.session_gap_ms


def reconstruct(events: List[KeyEvent]) -> str:
    """Apply the keystroke stream to an empty buffer and return the text."""
    buffer: List[str] = []
    for event in events:
        if event.kind == KIND_BACKSPACE:
            if buffer:
                buffer.pop()
        else:
            buffer.append(event.char)
    return "".join(buffer)


def script_key_sequence(script: List[ScriptEvent]) -> List[str]:
    """The characters that will actually be struck, in order.

    Includes a backspace character per deleted character, so the sequence
    matches the keystrokes the timing engine will be asked to produce.
    """
    keys: List[str] = []
    for event in script:
        if event.op == ms.OP_TYPE:
            keys.append(event.char)
        elif event.op == ms.OP_DELETE:
            keys.extend(BACKSPACE * event.count)
    return keys


def build_timeline(
    script: List[ScriptEvent], engine: TimingEngine
) -> Timeline:
    """Walk the script, assigning every operation a place on one clock."""
    # The engine's burstiness solve depends on the digraph mix of the text, so
    # let it measure the real sequence before it starts emitting.
    engine.calibrate(script_key_sequence(script))

    timeline = Timeline()

    # Time of the most recent keydown. The first keystroke is placed relative
    # to zero rather than to a previous key.
    last_keydown: Optional[float] = None
    pending_delay_ms = 0.0
    # Set when a session gap has just been emitted, so the keystroke that
    # resumes writing is marked as having no motor predecessor. Its interval is
    # measured across days and carries none of the rhythm the engine models.
    resuming_after_gap = False

    for event in script:
        if event.op == ms.OP_PAUSE:
            pending_delay_ms += event.duration_ms
            continue

        if event.op == ms.OP_SESSION_GAP:
            start = last_keydown if last_keydown is not None else 0.0
            start = timeline.events[-1].keyup_ms if timeline.events else start
            timeline.intervals.append(
                Interval(
                    kind="session_gap",
                    start_ms=start + pending_delay_ms,
                    duration_ms=event.duration_ms,
                    role=event.role,
                )
            )
            pending_delay_ms += event.duration_ms
            # Coming back to the document days later, neither the previous
            # keystroke nor the previous typing speed carries over.
            engine.reset_context()
            engine.reset_speed()
            resuming_after_gap = True
            continue

        # The kind of keystroke comes from the operation, never from the
        # character. Inferring it from the character would misread a literal
        # backspace (U+0008) in the input text as a delete, and reconstruction
        # would then eat the character before it.
        if event.op == ms.OP_TYPE:
            actions = [(event.char, event.role, KIND_KEY)]
        elif event.op == ms.OP_DELETE:
            actions = [(BACKSPACE, event.role, KIND_BACKSPACE)] * event.count
        else:
            raise ValueError(f"unknown script op {event.op!r}")

        for char, role, kind in actions:
            timing = engine.next_keystroke(char)

            if pending_delay_ms > 0.0 and timeline.events:
                timeline.intervals.append(
                    Interval(
                        kind="pause",
                        start_ms=timeline.events[-1].keyup_ms,
                        duration_ms=pending_delay_ms,
                        role=role,
                    )
                )

            if last_keydown is None:
                keydown = pending_delay_ms
                iki = 0.0
            else:
                keydown = last_keydown + pending_delay_ms + timing.iki_ms
                iki = pending_delay_ms + timing.iki_ms
            pending_delay_ms = 0.0

            # Rollover: the previous key is released after this one goes down.
            if timing.prev_overlap_ms > 0.0 and timeline.events:
                previous = timeline.events[-1]
                extended = keydown + timing.prev_overlap_ms
                if extended > previous.keyup_ms:
                    previous.keyup_ms = extended
                    previous.dwell_ms = previous.keyup_ms - previous.keydown_ms

            flight = (
                keydown - timeline.events[-1].keyup_ms if timeline.events else 0.0
            )

            timeline.events.append(
                KeyEvent(
                    index=len(timeline.events),
                    kind=kind,
                    char=None if kind == KIND_BACKSPACE else char,
                    keydown_ms=keydown,
                    keyup_ms=keydown + timing.dwell_ms,
                    dwell_ms=timing.dwell_ms,
                    iki_ms=iki,
                    motor_iki_ms=(
                        0.0
                        if last_keydown is None or resuming_after_gap
                        else timing.iki_ms
                    ),
                    flight_ms=flight,
                    role=role,
                )
            )
            last_keydown = keydown
            resuming_after_gap = False

    return timeline


def lag1_autocorrelation(values: List[float]) -> float:
    """Lag-1 autocorrelation of log(values). Returns 0.0 if undefined.

    Uses the standard estimator, where the lag-1 cross-product sum and the
    variance sum share the same denominator. Normalising them differently (by
    n-1 and n respectively) lets the result fall outside [-1, 1] on short
    series, which is not a correlation.
    """
    series = [math.log(v) for v in values if v > 0.0]
    if len(series) < 3:
        return 0.0
    mean = sum(series) / len(series)
    denominator = sum((x - mean) ** 2 for x in series)
    if denominator <= 0.0:
        return 0.0
    numerator = sum(
        (series[i] - mean) * (series[i + 1] - mean) for i in range(len(series) - 1)
    )
    return numerator / denominator


def summarize(text: str, timeline: Timeline, script: List[ScriptEvent]) -> dict:
    """Derive the statistics block from the finished timeline."""
    key_events = [e for e in timeline.events if e.kind == KIND_KEY]
    backspaces = [e for e in timeline.events if e.kind == KIND_BACKSPACE]

    # Motor intervals, excluding the first keystroke (no predecessor) and any
    # keystroke that resumed after a session gap. These carry the typing
    # rhythm; the full iki_ms would mix in deliberate pauses and flatten it.
    motor = [e.motor_iki_ms for e in timeline.events[1:] if e.motor_iki_ms > 0.0]
    ikis = [e.iki_ms for e in timeline.events[1:] if e.iki_ms > 0.0]

    total_ms = timeline.total_time_ms
    active_ms = timeline.active_time_ms
    chars = len(text)

    def wpm(elapsed_ms: float) -> float:
        if elapsed_ms <= 0.0:
            return 0.0
        return (chars / 5.0) / (elapsed_ms / 60_000.0)

    typo_events = sum(1 for e in timeline.events if e.role == ms.ROLE_TYPO)
    revision_deletes = sum(
        e.count for e in script
        if e.op == ms.OP_DELETE and e.role == ms.ROLE_REVISION_DELETE
    )

    return {
        "total_time_ms": round(total_ms, 3),
        "active_time_ms": round(active_ms, 3),
        "session_gap_ms": round(timeline.session_gap_ms, 3),
        # WPM on the standard 5-characters-per-word convention. `active`
        # excludes time between writing sessions; `wall_clock` does not.
        "wpm_active": round(wpm(active_ms), 3),
        "wpm_wall_clock": round(wpm(total_ms), 3),
        "keystrokes": len(timeline.events),
        "character_keystrokes": len(key_events),
        "backspaces": len(backspaces),
        "pauses": sum(1 for i in timeline.intervals if i.kind == "pause"),
        "session_gaps": sum(1 for i in timeline.intervals if i.kind == "session_gap"),
        "typo_keystrokes": typo_events,
        "revision_deleted_chars": revision_deletes,
        # Fraction of typed characters that were later deleted. The literature
        # reports 10-30% for real composition.
        "deletion_ratio": round(
            len(backspaces) / len(key_events) if key_events else 0.0, 4
        ),
        "mean_iki_ms": round(sum(ikis) / len(ikis), 3) if ikis else 0.0,
        "mean_motor_iki_ms": round(sum(motor) / len(motor), 3) if motor else 0.0,
        "mean_dwell_ms": round(
            sum(e.dwell_ms for e in timeline.events) / len(timeline.events), 3
        ) if timeline.events else 0.0,
        # Rhythm autocorrelation, measured on the motor intervals. This is the
        # quantity TimingEngine(target_autocorrelation=...) controls.
        "lag1_autocorrelation": round(lag1_autocorrelation(motor), 4),
        "rollover_keystrokes": sum(1 for e in timeline.events if e.flight_ms < 0.0),
    }


def generate(
    text: str,
    profile: str = "average",
    seed: Optional[int] = None,
    typo_rate: float = ms.TYPO_RATE,
    r_burst_probability: float = ms.R_BURST_PROBABILITY,
    session_chars: Optional[int] = None,
    target_autocorrelation: Optional[float] = None,
) -> dict:
    """Generate the full synthetic record for `text`.

    Raises ValueError if the generated keystroke stream does not reproduce the
    input text exactly.
    """
    if not isinstance(text, str):
        raise TypeError(f"text must be str, got {type(text).__name__}")

    scripter = MacroScripter(
        seed=seed,
        typo_rate=typo_rate,
        r_burst_probability=r_burst_probability,
        session_chars=session_chars,
    )
    engine_kwargs = {"profile": profile, "seed": seed}
    if target_autocorrelation is not None:
        engine_kwargs["target_autocorrelation"] = target_autocorrelation
    engine = TimingEngine(**engine_kwargs)

    script = scripter.generate_script(text)

    replayed = ms.replay(script)
    if replayed != text:
        raise ValueError(
            "macro script does not reproduce the target text "
            f"({len(replayed)} chars produced vs {len(text)} expected)"
        )

    timeline = build_timeline(script, engine)

    produced = reconstruct(timeline.events)
    if produced != text:
        raise ValueError(
            "keystroke timeline does not reproduce the target text "
            f"({len(produced)} chars produced vs {len(text)} expected)"
        )

    return {
        "metadata": {
            "profile": profile,
            "seed": seed,
            "typo_rate": typo_rate,
            "r_burst_probability": r_burst_probability,
            "input_chars": len(text),
            "input_words": len(text.split()),
        },
        "statistics": summarize(text, timeline, script),
        "macro_script": [e.to_dict() for e in script],
        "keystrokes": [e.to_dict() for e in timeline.events],
        "intervals": [i.to_dict() for i in timeline.intervals],
        "target_text": text,
    }
