"""
Project TypeTrace - Writing replay

Renders a generated record as a readable account of the writing process:
what the document looked like as it was being written, where the writer
paused, what they mistyped, what they went back and rewrote, and where they
stopped for the day.

The JSON record is the machine-readable artifact. This is the one you read to
see whether the generated process actually looks like someone writing.
"""

import unicodedata
from typing import Any, Dict, List, Optional

# Events quieter than this are not worth a line of their own; the writer is
# simply typing.
DEFAULT_PAUSE_THRESHOLD_MS = 400.0

# How much of the document tail to show alongside each event.
DEFAULT_WIDTH = 56

CURSOR = "|"
ELLIPSIS = "…"

_CONTROL_DISPLAY = {
    "\n": "↵",   # return arrow
    "\r": "↵",
    "\t": "→",   # rightwards arrow
    "\b": "⌫",   # erase to the left
    "\x00": "␀",
    "\u2028": "↵",  # LINE SEPARATOR
    "\u2029": "¶",  # PARAGRAPH SEPARATOR
}

# Everything in these categories either breaks the line, moves the cursor, or
# opens an escape sequence when written to a terminal - U+000B and U+000C split
# a line as surely as U+000A does, and U+001B would swallow the rest of the row.
# The named glyphs above are the ones worth reading; anything else falls back to
# the Unicode Control Pictures block, which has one printable glyph per C0 code.
#
# Cf matters as much as Cc here and is easy to miss, because a format character
# is not a control character but is just as invisible: zero-width space, soft
# hyphen, the bidi marks, word joiner and the byte order mark all occupy no
# column. Left unmapped they make the DOCUMENT column silently narrower than the
# character count says, which is exactly the kind of discrepancy this view exists
# to expose.
_INVISIBLE_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp"})
_CONTROL_PICTURES = 0x2400
_SYMBOL_FOR_DELETE = "␡"
# For the C1 controls, which have no picture of their own.
_UNKNOWN_CONTROL = "␦"


def format_timestamp(ms: float) -> str:
    """Elapsed time as a fixed-width, human-scaled string.

    Session gaps can push a document across days, so the format widens rather
    than rolling over silently at an hour.
    """
    total_seconds = ms / 1000.0
    whole = int(total_seconds)
    milliseconds = int(round((total_seconds - whole) * 1000))
    if milliseconds == 1000:
        whole += 1
        milliseconds = 0

    days, remainder = divmod(whole, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)

    if days:
        return f"{days}d {hours:02d}:{minutes:02d}:{seconds:02d}"
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}"
    return f"{minutes:02d}:{seconds:02d}.{milliseconds:03d}"


def format_duration(ms: float) -> str:
    """A duration in whichever unit reads naturally at that scale."""
    if ms < 1000.0:
        return f"{ms:.0f} ms"
    if ms < 60_000.0:
        return f"{ms / 1000.0:.1f} s"
    if ms < 3_600_000.0:
        return f"{ms / 60_000.0:.1f} min"
    hours = ms / 3_600_000.0
    if hours < 48.0:
        return f"{hours:.1f} hours"
    return f"{hours / 24.0:.1f} days"


def display_char(char: Optional[str]) -> str:
    """A printable stand-in for a character that would break the layout."""
    if char is None:
        return ""
    glyph = _CONTROL_DISPLAY.get(char)
    if glyph is not None:
        return glyph
    if len(char) == 1 and unicodedata.category(char) in _INVISIBLE_CATEGORIES:
        code = ord(char)
        if code < 0x20:
            return chr(_CONTROL_PICTURES + code)
        if code == 0x7F:
            return _SYMBOL_FOR_DELETE
        return _UNKNOWN_CONTROL
    return char


def visible_tail(text: str, width: int) -> str:
    """The last `width` characters of `text`, with the cursor marked.

    Control characters are replaced with visible glyphs so one keystroke stays
    one column and the timeline does not wrap.
    """
    tail = text[-width:] if width > 0 else ""
    rendered = "".join(display_char(ch) for ch in tail)
    prefix = ELLIPSIS if len(text) > len(tail) else ""
    return f"{prefix}{rendered}{CURSOR}"


def _moments(record: Dict[str, Any], pause_threshold_ms: float) -> List[dict]:
    """Time-ordered notable events, each already carrying the document state.

    Keystrokes and intervals are merged onto one ordering so a pause appears
    between the keystrokes it separates rather than in a list of its own.
    """
    keystrokes = record["keystrokes"]
    gaps = [i for i in record.get("intervals", []) if i["kind"] == "session_gap"]

    moments: List[dict] = []
    buffer: List[str] = []
    gap_index = 0

    for position, event in enumerate(keystrokes):
        # Any session gap that started before this keystroke belongs here.
        while gap_index < len(gaps) and gaps[gap_index]["start_ms"] <= event["keydown_ms"]:
            moments.append({
                "kind": "session_gap",
                "at_ms": gaps[gap_index]["start_ms"],
                "duration_ms": gaps[gap_index]["duration_ms"],
                "text": "".join(buffer),
            })
            gap_index += 1

        is_backspace = event["kind"] == "backspace"
        if is_backspace:
            if buffer:
                buffer.pop()
        else:
            buffer.append(event["char"])

        role = event["role"]
        pause_ms = event["iki_ms"] - event["motor_iki_ms"]

        if position == 0:
            kind = "begin"
        elif role == "typo":
            kind = "typo"
        elif role == "correction":
            kind = "correction"
        elif role == "revision_delete":
            kind = "revision_delete"
        elif role == "revision_retype":
            kind = "revision_retype"
        elif pause_ms >= pause_threshold_ms:
            kind = "pause"
        else:
            kind = "typing"

        moments.append({
            "kind": kind,
            "at_ms": event["keydown_ms"],
            "pause_ms": pause_ms,
            "char": event["char"],
            "is_backspace": is_backspace,
            "role": role,
            "text": "".join(buffer),
            "index": position,
        })

    while gap_index < len(gaps):
        moments.append({
            "kind": "session_gap",
            "at_ms": gaps[gap_index]["start_ms"],
            "duration_ms": gaps[gap_index]["duration_ms"],
            "text": "".join(buffer),
        })
        gap_index += 1

    return moments


# Kinds where a run of identical keystrokes says nothing extra per line, and
# folds into one line reporting how many there were.
FOLDABLE_KINDS = {
    "typing": "run",
    "revision_delete": "revision_delete_run",
    "revision_retype": "revision_retype_run",
}


def _collapse(moments: List[dict]) -> List[dict]:
    """Fold consecutive keystrokes of the same routine kind into one line.

    A keystroke-by-keystroke listing of an essay is thousands of lines and
    unreadable, and a revision that deletes forty characters should not be
    forty lines saying "delete back". Runs become one line reporting the count;
    anything the writer actually decided - hesitating, mistyping, noticing it -
    keeps its own line.
    """
    collapsed: List[dict] = []
    run: List[dict] = []

    def flush() -> None:
        if not run:
            return
        collapsed.append({
            "kind": FOLDABLE_KINDS[run[0]["kind"]],
            "at_ms": run[0]["at_ms"],
            "chars": len(run),
            "text": run[-1]["text"],
        })
        run.clear()

    for moment in moments:
        kind = moment["kind"]
        if kind in FOLDABLE_KINDS:
            if run and run[0]["kind"] != kind:
                flush()
            run.append(moment)
            continue
        flush()
        collapsed.append(moment)

    flush()
    return collapsed


def _describe(moment: dict) -> str:
    kind = moment["kind"]
    if kind == "begin":
        return "start writing"
    if kind == "run":
        return f"type {moment['chars']} characters"
    if kind == "revision_delete_run":
        return f"delete back {moment['chars']} characters"
    if kind == "revision_retype_run":
        return f"rewrite {moment['chars']} characters"
    if kind == "pause":
        return f"pause {format_duration(moment['pause_ms'])}"
    if kind == "typo":
        return f"mistype {display_char(moment['char'])!r}"
    if kind == "correction":
        if moment["is_backspace"]:
            return "notice it, backspace"
        return f"retype {display_char(moment['char'])!r}"
    if kind == "revision_delete":
        return "delete back" if moment["is_backspace"] else "revise"
    if kind == "revision_retype":
        return "rewrite"
    if kind == "session_gap":
        return f"STOP - {format_duration(moment['duration_ms'])} until the next session"
    if kind == "typing":
        return f"type {display_char(moment['char'])!r}"
    return kind


def render(
    record: Dict[str, Any],
    full: bool = False,
    width: int = DEFAULT_WIDTH,
    pause_threshold_ms: float = DEFAULT_PAUSE_THRESHOLD_MS,
) -> str:
    """Render `record` as a plain-text replay of the writing process.

    With `full`, every keystroke gets its own line. Otherwise runs of ordinary
    typing are collapsed and only the interesting moments are listed.
    """
    stats = record["statistics"]
    meta = record["metadata"]
    target = record["target_text"]

    lines = [
        "TypeTrace writing replay",
        "=" * 24,
        "",
        f"  characters : {meta['input_chars']}  ({meta['input_words']} words)",
        f"  profile    : {meta['profile']}"
        + (f", seed {meta['seed']}" if meta.get("seed") is not None else ""),
        f"  elapsed    : {format_duration(stats['active_time_ms'])} writing"
        + (
            f", {format_duration(stats['total_time_ms'])} wall clock"
            if stats["session_gaps"]
            else ""
        ),
        f"  speed      : {stats['wpm_active']:.1f} WPM",
        f"  keystrokes : {stats['keystrokes']}"
        f" ({stats['backspaces']} backspaces,"
        f" {stats['typo_keystrokes']} typo events,"
        f" {stats['session_gaps']} session gaps)",
        "",
    ]

    moments = _moments(record, pause_threshold_ms)
    if not full:
        moments = _collapse(moments)

    if not moments:
        lines.append("  (no keystrokes)")
        return "\n".join(lines)

    lines.append(f"  {'TIME':>12}  {'DOCUMENT':<{width + 2}} EVENT")
    lines.append(f"  {'-' * 12}  {'-' * (width + 2)} {'-' * 30}")

    for moment in moments:
        if moment["kind"] == "session_gap":
            lines.append("")
            lines.append(
                f"  {format_timestamp(moment['at_ms']):>12}  {_describe(moment)}"
            )
            lines.append("")
            continue
        document = visible_tail(moment["text"], width)
        lines.append(
            f"  {format_timestamp(moment['at_ms']):>12}  "
            f"{document:<{width + 2}} {_describe(moment)}"
        )

    lines.extend(["", "Final text", "-" * 10, target, ""])
    return "\n".join(lines)
