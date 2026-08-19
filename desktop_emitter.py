"""
Project TypeTrace - Desktop Emitter

Replays a generated record's keystroke clock into whatever desktop window has
focus - Word, Notepad, any editor - using pynput keyboard events. Unlike the
Google Docs path, nothing here ties the emission to a specific application;
the user is responsible for giving the right window focus during the
countdown, and pressing Esc aborts the emission at any point.

pynput is an optional dependency: it is imported lazily inside functions, and
a missing install raises guidance rather than an opaque ImportError. The
character-to-key mapping is pynput-free and exposed as pure functions
(describe_character / describe_keystroke) so tests can exercise it without
pynput installed.
"""

import sys
import time
from dataclasses import dataclass
from typing import Dict, Optional

# Named pynput Key attributes for characters that are not text. Key.space is
# preferred over a " " KeyCode because it hangs up on some Linux layouts less.
_NAMED_KEYS: Dict[str, str] = {
    "\n": "enter",
    "\r": "enter",
    "\t": "tab",
    " ": "space",
}

# US-QWERTY shifted pairs: the base key on the left, the symbol it produces
# under Shift on the right. Records carry the produced character, so emission
# presses Shift plus the base key.
SHIFT_PAIRS: Dict[str, str] = {
    "1": "!", "2": "@", "3": "#", "4": "$", "5": "%",
    "6": "^", "7": "&", "8": "*", "9": "(", "0": ")",
    "-": "_", "=": "+", "[": "{", "]": "}", "\\": "|",
    ";": ":", "'": '"', ",": "<", ".": ">", "/": "?", "`": "~",
}

_UNSHIFTED_SYMBOLS = frozenset(SHIFT_PAIRS)
_SHIFTED_TO_BASE: Dict[str, str] = {shifted: base for base, shifted in SHIFT_PAIRS.items()}


@dataclass(frozen=True)
class KeySpec:
    """What must be pressed to produce one keystroke.

    Exactly one of name / char is set:
      name  -> a pynput keyboard.Key attribute ("enter", "tab", "backspace",
               "space") for keys that carry no text.
      char  -> the base character for keyboard.KeyCode.from_char() - the
               unshifted keycap, even when shift is True.
    shift says whether Shift must be held while the key is down. The emitter
    presses Shift just before the key and releases it right after, so the
    caller's own Shift state is left alone.
    """

    name: Optional[str] = None
    char: Optional[str] = None
    shift: bool = False


def describe_character(char: str) -> KeySpec:
    """Map one character to the key and Shift state that produces it.

    Covers letters, digits, space, newline, tab and the standard US-QWERTY
    shifted symbols. Anything else falls through as a literal KeyCode with no
    Shift; pynput will raise at emission time if the layout cannot type it,
    which is the honest failure for a character the mapping does not know.
    """
    if len(char) != 1:
        raise ValueError(f"describe_character expects one character, got {char!r}")
    if char in _NAMED_KEYS:
        return KeySpec(name=_NAMED_KEYS[char])
    if "a" <= char <= "z" or char in _UNSHIFTED_SYMBOLS:
        return KeySpec(char=char)
    if "A" <= char <= "Z":
        return KeySpec(char=char.lower(), shift=True)
    base = _SHIFTED_TO_BASE.get(char)
    if base is not None:
        return KeySpec(char=base, shift=True)
    return KeySpec(char=char)


def describe_keystroke(keystroke: dict) -> KeySpec:
    """Map one record keystroke to the key and Shift state that produces it.

    kind="backspace" maps to the Backspace key with no character payload.
    """
    if keystroke.get("kind") == "backspace":
        return KeySpec(name="backspace")
    char = keystroke.get("char")
    if not isinstance(char, str) or len(char) != 1:
        raise ValueError(
            f"keystroke {keystroke.get('index')}: expected a single-character 'char', got {char!r}"
        )
    return describe_character(char)


def emit_to_desktop(record: dict, *, speed: float = 1.0,
                    max_gap_s: Optional[float] = None,
                    initial_delay_s: float = 5.0) -> dict:
    """Replay the record's keystroke clock as real key events on the desktop.

    Counts down on stderr for initial_delay_s seconds so the user can focus
    the target window, then replays keydown/keyup in time order. Pressing Esc
    at any point - during the countdown or mid-emission - stops the replay.

    Returns run_timeline's summary: {"dispatched", "aborted", "duration_s"}.
    """
    try:
        from pynput import keyboard
    except ImportError as exc:
        raise ImportError(
            "pynput is required for desktop emission. Run: pip install pynput"
        ) from exc

    # Imported here so the module loads (and its pure mapping is testable)
    # even before emit_common or pynput are installed.
    from emit_common import iter_timeline, run_timeline

    import threading

    abort_requested = threading.Event()

    def _on_press(key):
        if key == keyboard.Key.esc:
            abort_requested.set()
            return False  # stop the listener itself
        return True

    def resolve(spec: KeySpec):
        if spec.name is not None:
            return getattr(keyboard.Key, spec.name)
        return keyboard.KeyCode.from_char(spec.char)

    controller = keyboard.Controller()

    def dispatch(event: str, keystroke: dict) -> None:
        spec = describe_keystroke(keystroke)
        key = resolve(spec)
        if event == "down":
            if spec.shift:
                controller.press(keyboard.Key.shift)
            controller.press(key)
            if spec.shift:
                # Shift wraps the key press alone, not the keystroke's whole
                # dwell: a rollover key that goes down before this key comes
                # up must not see Shift held. Releasing it right after the
                # press keeps the produced character (decided at press time)
                # and restores the caller's own Shift state.
                controller.release(keyboard.Key.shift)
        else:
            controller.release(key)

    listener = keyboard.Listener(on_press=_on_press)
    try:
        listener.start()

        if initial_delay_s > 0:
            print(
                "TypeTrace desktop emission: focus the target editor window.",
                file=sys.stderr,
            )
        remaining = initial_delay_s
        while remaining > 0 and not abort_requested.is_set():
            print(
                f"\rStarting in {remaining:4.1f}s (press Esc to abort)...",
                end="", file=sys.stderr, flush=True,
            )
            step = min(0.1, remaining)
            time.sleep(step)
            remaining -= step
        if initial_delay_s > 0:
            print("", file=sys.stderr)  # clear the countdown line

        if abort_requested.is_set():
            result = {"dispatched": 0, "aborted": True, "duration_s": 0.0}
        else:
            events = iter_timeline(record, speed=speed, max_gap_s=max_gap_s)
            result = run_timeline(
                events, dispatch, should_abort=abort_requested.is_set
            )
    finally:
        listener.stop()

    state = "aborted by Esc" if result["aborted"] else "finished"
    print(
        f"TypeTrace desktop emission {state}: {result['dispatched']} key events "
        f"in {result['duration_s']:.1f}s.",
        file=sys.stderr,
    )
    return result
