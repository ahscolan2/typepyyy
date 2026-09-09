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
from typing import Callable, Dict, Optional

from emit_common import SHIFTED_TO_BASE, finite_number, iter_timeline, run_timeline, validate_live_text

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
SHIFT_PAIRS: Dict[str, str] = {base: shifted for shifted, base in SHIFTED_TO_BASE.items()}

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


def _foreground_window():
    """Return a stable window handle on Windows; other platforms use Esc/Stop."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    get_foreground = ctypes.windll.user32.GetForegroundWindow
    get_foreground.restype = wintypes.HWND
    return get_foreground()


def emit_to_desktop(record: dict, *, speed: float = 1.0,
                    max_gap_s: Optional[float] = None,
                    initial_delay_s: float = 5.0,
                    should_abort: Optional[Callable[[], bool]] = None,
                    on_status: Optional[Callable[[str], None]] = None) -> dict:
    """Replay the record's keystroke clock as real key events on the desktop.

    Counts down on stderr for initial_delay_s seconds so the user can focus
    the target window, then replays keydown/keyup in time order. Pressing Esc
    at any point - during the countdown or mid-emission - stops the replay.

    Returns run_timeline's summary: {"dispatched", "aborted", "duration_s"}.
    """
    initial_delay_s = finite_number(initial_delay_s, "initial_delay_s")
    events = list(iter_timeline(record, speed=speed, max_gap_s=max_gap_s))
    validate_live_text(record)
    if should_abort is not None and should_abort():
        return {"dispatched": 0, "aborted": True, "duration_s": 0.0}
    try:
        from pynput import keyboard
    except ImportError as exc:
        raise ImportError(
            "pynput is required for desktop emission. Run: pip install pynput"
        ) from exc

    import threading

    abort_requested = threading.Event()
    target_window = None
    focus_lost = False

    def cancelled():
        nonlocal focus_lost
        if target_window is not None and _foreground_window() != target_window:
            focus_lost = True
        return focus_lost or abort_requested.is_set() or (should_abort is not None and should_abort())

    def status(message):
        print(message, file=sys.stderr, flush=True)
        if on_status:
            on_status(message)

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
    held = {}
    shift_held = False

    def dispatch(event: str, keystroke: dict) -> None:
        nonlocal shift_held
        spec = describe_keystroke(keystroke)
        key = resolve(spec)
        if event == "down":
            try:
                if spec.shift:
                    shift_held = True
                    controller.press(keyboard.Key.shift)
                held[id(keystroke)] = key
                controller.press(key)
            finally:
                if spec.shift:
                    controller.release(keyboard.Key.shift)
                    shift_held = False
        else:
            controller.release(key)
            held.pop(id(keystroke), None)

    listener = keyboard.Listener(on_press=_on_press)
    try:
        listener.start()

        if initial_delay_s > 0:
            status("Focus the target editor. Press Esc or Stop to cancel.")
        remaining = initial_delay_s
        last_second = None
        while remaining > 0 and not cancelled():
            second = max(1, int(remaining + 0.999))
            if second != last_second:
                status(f"Starting in {second}s…")
                last_second = second
            step = min(0.1, remaining)
            time.sleep(step)
            remaining -= step
        if cancelled():
            result = {"dispatched": 0, "aborted": True, "duration_s": 0.0}
        else:
            target_window = _foreground_window()
            status(f"Typing {len(record['keystrokes'])} keystrokes. Press Esc or Stop to cancel.")
            result = run_timeline(
                events, dispatch, should_abort=cancelled
            )
    except KeyboardInterrupt:
        result = {"dispatched": 0, "aborted": True, "duration_s": 0.0}
    finally:
        for key in held.values():
            try:
                controller.release(key)
            except Exception:
                pass  # Attempt every release even if one device call fails.
        if shift_held:
            try:
                controller.release(keyboard.Key.shift)
            except Exception:
                pass
        listener.stop()

    state = "stopped because focus changed" if focus_lost else ("stopped" if result["aborted"] else "finished")
    if focus_lost:
        result["reason"] = "focus_changed"
    print(
        f"TypeTrace desktop emission {state}: {result['dispatched']} key events "
        f"in {result['duration_s']:.1f}s.",
        file=sys.stderr,
    )
    return result
