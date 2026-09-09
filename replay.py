"""
Project TypeTrace - Writing replay

Renders a generated record as a readable account of the writing process:
what the document looked like as it was being written, where the writer
paused, what they mistyped, what they went back and rewrote, and where they
stopped for the day.

The JSON record is the machine-readable artifact. This is the one you read to
see whether the generated process actually looks like someone writing.
"""

import math
import unicodedata
from typing import Any, Dict, List, Optional

from wcwidth import iter_graphemes, wcswidth

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
    """The last `width` terminal cells of `text`, with the cursor marked.

    Keep complete graphemes: a combining accent stays with its base and an
    emoji sequence stays together. Controls retain their visible stand-ins.
    """
    clusters = list(iter_graphemes(text))
    tail = []
    cells = 0
    for cluster in reversed(clusters):
        rendered = _display_cluster(cluster)
        cells += wcswidth(rendered)
        if cells > max(width, 0):
            break
        tail.append(rendered)
    prefix = ELLIPSIS if len(tail) < len(clusters) else ""
    return f"{prefix}{''.join(reversed(tail))}{CURSOR}"


def _display_cluster(cluster: str) -> str:
    # Joiners inside a grapheme belong to its glyph, such as a family emoji.
    # Standalone format characters still receive the visible control marker.
    rendered = "".join(
        ch if ch == "\u200d" and 0 < index < len(cluster) - 1 else display_char(ch)
        for index, ch in enumerate(cluster)
    )
    # A leading, unattached accent must not decorate the table's padding or
    # ellipsis. A dotted circle gives it its own visible cell.
    return "◌" + rendered if wcswidth(rendered) == 0 else rendered


def _moments(
    record: Dict[str, Any],
    pause_threshold_ms: float,
    width: int = DEFAULT_WIDTH,
) -> List[dict]:
    """Merge intervals and keys, retaining only the visible document tail."""
    intervals = sorted(record.get("intervals", []), key=lambda item: item["start_ms"])
    interval_index = 0
    moments: List[dict] = []
    # Keep the editable buffer as graphemes. Updating only its final cluster
    # avoids rebuilding the full document for every keystroke.
    buffer: List[str] = []

    def snapshot() -> str:
        tail = []
        cells = 0
        for cluster in reversed(buffer):
            tail.append(cluster)
            cells += wcswidth(_display_cluster(cluster))
            if cells > width:
                break  # One overflow cluster lets visible_tail mark clipping.
        return "".join(reversed(tail))

    def add_interval(interval: dict) -> None:
        if interval["kind"] == "pause":
            if interval["duration_ms"] < pause_threshold_ms:
                return
            moments.append({
                "kind": "pause",
                "at_ms": interval["start_ms"],
                "pause_ms": interval["duration_ms"],
                "text": snapshot(),
            })
        elif interval["kind"] == "session_gap":
            moments.append({
                "kind": "session_gap",
                "at_ms": interval["start_ms"],
                "duration_ms": interval["duration_ms"],
                "text": snapshot(),
            })

    previous = None
    for position, event in enumerate(record["keystrokes"]):
        while (
            interval_index < len(intervals)
            and intervals[interval_index]["start_ms"] <= event["keydown_ms"]
        ):
            add_interval(intervals[interval_index])
            interval_index += 1

        # Older records lack explicit intervals. Keep their pause estimate,
        # but attach it to the pre-keystroke document like modern records.
        if "intervals" not in record and previous is not None:
            pause_ms = max(0.0, event["iki_ms"] - event["motor_iki_ms"])
            if pause_ms > 0.0:
                add_interval({
                    "kind": "pause",
                    "start_ms": previous["keyup_ms"],
                    "duration_ms": pause_ms,
                })

        is_backspace = event["kind"] == "backspace"
        if is_backspace:
            if buffer:
                buffer.extend(iter_graphemes(buffer.pop()[:-1]))
        else:
            previous_cluster = buffer.pop() if buffer else ""
            buffer.extend(iter_graphemes(previous_cluster + event["char"]))

        role = event["role"]
        if role in {"typo", "correction", "revision_delete", "revision_retype"}:
            kind = role
        else:
            kind = "begin" if position == 0 else "typing"
        moments.append({
            "kind": kind,
            "at_ms": event["keydown_ms"],
            "char": event["char"],
            "is_backspace": is_backspace,
            "role": role,
            "text": snapshot(),
            "index": position,
        })
        previous = event

    for interval in intervals[interval_index:]:
        add_interval(interval)
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
            "at_ms": run[-1]["at_ms"],
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
    if isinstance(width, bool) or not isinstance(width, int) or width < 0:
        raise ValueError("width must be a non-negative integer")
    if not math.isfinite(pause_threshold_ms) or pause_threshold_ms < 0:
        raise ValueError("pause_threshold_ms must be finite and >= 0")

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

    moments = _moments(record, pause_threshold_ms, width)
    if not full:
        moments = _collapse(moments)

    if not moments:
        lines.append("  (no keystrokes)")
        return "\n".join(lines)

    lines.append(f"  {'TIME':>12}  {'DOCUMENT'[:width + 2]:<{width + 2}} EVENT")
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
        padding = " " * (width + 2 - wcswidth(document))
        lines.append(
            f"  {format_timestamp(moment['at_ms']):>12}  "
            f"{document}{padding} {_describe(moment)}"
        )

    lines.extend(["", "Final text", "-" * 10, target, ""])
    return "\n".join(lines)
