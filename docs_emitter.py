"""
Project TypeTrace - Google Docs emitter

Replays a generated record into a real Google Docs document over the Chrome
DevTools Protocol, dispatching the same rawKeyDown/char/keyUp sequence a
physical keyboard would produce. This is honest research tooling: it types
the record's own keystrokes, on the record's own clock, into a document the
user points it at, so the team can study what the editor records. The
browser runs visibly by default with a persistent profile, and what is typed
is exactly what the record says - nothing more is synthesised at emission
time.

Playwright is an optional dependency of the project as a whole and is
imported lazily inside emit_to_google_docs(); without it, this module still
imports cleanly and its key mapping stays unit-testable:

    pip install playwright && playwright install chromium

The keystroke -> CDP mapping below is pure and dependency-free by design.
No timestamps are attached to the dispatched events: scheduling is done on
the wall clock by emit_common.run_timeline, so Chrome receives each event
"now" and stamps it itself.
"""

import sys
from typing import Any, Dict, List, Optional

from emit_common import iter_timeline, run_timeline

DOCS_EDIT_URL = "https://docs.google.com/document/d/{doc_id}/edit"

# The main text surface of the Google Docs editor.
EDITOR_SELECTOR = ".kix-appview-editor"

# How long to wait for the editor after navigation. Generous because a
# first-run profile may be sitting on a Google sign-in page.
_EDITOR_TIMEOUT_MS = 120_000

# CDP modifier bit for Shift (Alt=1, Ctrl=2, Meta=4, Shift=8).
_SHIFT_MODIFIER = 8

# Symbol keys by their unshifted character: (CDP code, Windows virtual-key
# code). US QWERTY, matching the keyboard the timing model is built on.
_SYMBOL_KEYS = {
    "`": ("Backquote", 192),
    "-": ("Minus", 189),
    "=": ("Equal", 187),
    "[": ("BracketLeft", 219),
    "]": ("BracketRight", 221),
    "\\": ("Backslash", 220),
    ";": ("Semicolon", 186),
    "'": ("Quote", 222),
    ",": ("Comma", 188),
    ".": ("Period", 190),
    "/": ("Slash", 191),
}

# Shifted characters mapped to the unshifted character on the same key.
_SHIFTED_CHARS = {
    "~": "`", "!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6",
    "&": "7", "*": "8", "(": "9", ")": "0", "_": "-", "+": "=", "{": "[",
    "}": "]", "|": "\\", ":": ";", '"': "'", "<": ",", ">": ".", "?": "/",
}

_SHIFT_DESCRIPTOR = {
    "code": "ShiftLeft",
    "key": "Shift",
    "windowsVirtualKeyCode": 16,
    "nativeVirtualKeyCode": 16,
    "shift": False,
    "text": None,
}

_BACKSPACE_DESCRIPTOR = {
    "code": "Backspace",
    "key": "Backspace",
    "windowsVirtualKeyCode": 8,
    "nativeVirtualKeyCode": 8,
    "shift": False,
    "text": None,
}

# Control characters the record can contain, typed as named keys. Enter
# carries a char event whose text is a carriage return, which is what a real
# keyboard produces; Tab produces no text (a real Tab keypress generates no
# character event).
_NAMED_KEYS = {
    "\n": {"code": "Enter", "key": "Enter", "windowsVirtualKeyCode": 13,
           "nativeVirtualKeyCode": 13, "shift": False, "text": "\r"},
    "\t": {"code": "Tab", "key": "Tab", "windowsVirtualKeyCode": 9,
           "nativeVirtualKeyCode": 9, "shift": False, "text": None},
    " ": {"code": "Space", "key": " ", "windowsVirtualKeyCode": 32,
          "nativeVirtualKeyCode": 32, "shift": False, "text": " "},
}


def descriptor_for(char: str) -> Dict[str, Any]:
    """The physical key that produces `char`, as a CDP field set.

    Returns {"code", "key", "windowsVirtualKeyCode", "nativeVirtualKeyCode",
    "shift", "text"}: which key it is, whether Shift must be held for it, and
    what text its char event carries (None for keys that produce no
    character, e.g. Tab). `key` is the character actually produced, so it is
    "A" / "!" when shifted. Characters outside US QWERTY fall back to vk 0
    with the text carried by the char event alone.
    """
    if char in _NAMED_KEYS:
        return dict(_NAMED_KEYS[char])

    shift = False
    base = char
    if char in _SHIFTED_CHARS:
        base = _SHIFTED_CHARS[char]
        shift = True

    if len(base) == 1 and "a" <= base.lower() <= "z":
        code = f"Key{base.upper()}"
        vk = ord(base.upper())
        if base.isupper():
            shift = True
    elif len(base) == 1 and base.isdigit():
        code = f"Digit{base}"
        vk = ord(base)
    elif base in _SYMBOL_KEYS:
        code, vk = _SYMBOL_KEYS[base]
    else:
        # Outside the modelled layout; the char event still carries the text.
        code, vk, shift = "", 0, False

    return {
        "code": code,
        "key": char,
        "windowsVirtualKeyCode": vk,
        "nativeVirtualKeyCode": vk,
        "shift": shift,
        "text": char,
    }


def _key_event(event_type: str, desc: Dict[str, Any], *, modifiers: int = 0) -> Dict[str, Any]:
    payload = {
        "type": event_type,
        "key": desc["key"],
        "code": desc["code"],
        "windowsVirtualKeyCode": desc["windowsVirtualKeyCode"],
        "nativeVirtualKeyCode": desc["nativeVirtualKeyCode"],
    }
    if modifiers:
        payload["modifiers"] = modifiers
    return payload


def payloads_for(keystroke: Dict[str, Any], event: str) -> List[Dict[str, Any]]:
    """CDP payloads for one keydown/keyup of `keystroke`, in order.

    `event` is "down" or "up", as delivered by emit_common.iter_timeline:

    - printable character, keydown: rawKeyDown, then a char event carrying
      the text - wrapped in a Shift rawKeyDown/keyUp pair when the character
      needs Shift, so the modifier is held only for this keystroke and the
      prior state is always restored;
    - printable character, keyup: the matching keyUp, then the Shift keyUp;
    - Backspace: rawKeyDown/keyUp only. Backspace is not a character, so no
      char event and no text field are ever sent for it.

    No timestamp field is set on any payload; run_timeline decides when each
    payload is sent.

    desktop_emitter releases Shift immediately after the press rather than
    holding it across the dwell, and the two are meant to differ. pynput
    drives the real OS keyboard, where a held Shift is global state that the
    next rolled-over key would genuinely see. CDP carries `modifiers` on each
    individual payload and the character comes from the char event's explicit
    `text`, so a rolled-over key dispatched between the Shift rawKeyDown and
    its keyUp still declares modifiers=0 and still inserts its own text. The
    hold is per-event bookkeeping here, not a machine-wide modifier latch.
    """
    if event not in ("down", "up"):
        raise ValueError(f"event must be 'down' or 'up', got {event!r}")

    if keystroke["kind"] == "backspace":
        return [_key_event("rawKeyDown" if event == "down" else "keyUp",
                           _BACKSPACE_DESCRIPTOR)]

    desc = descriptor_for(keystroke["char"])
    modifiers = _SHIFT_MODIFIER if desc["shift"] else 0

    if event == "down":
        payloads = []
        if desc["shift"]:
            payloads.append(_key_event("rawKeyDown", _SHIFT_DESCRIPTOR))
        payloads.append(_key_event("rawKeyDown", desc, modifiers=modifiers))
        if desc["text"] is not None:
            char_event = _key_event("char", desc, modifiers=modifiers)
            char_event["text"] = desc["text"]
            payloads.append(char_event)
        return payloads

    payloads = [_key_event("keyUp", desc, modifiers=modifiers)]
    if desc["shift"]:
        payloads.append(_key_event("keyUp", _SHIFT_DESCRIPTOR))
    return payloads


def emit_to_google_docs(
    record: Dict[str, Any],
    *,
    doc_id: str,
    speed: float = 1.0,
    max_gap_s: Optional[float] = None,
    headless: bool = False,
    profile_dir: str = ".typetrace-browser-profile",
) -> Dict[str, Any]:
    """Replay `record` into the Google Docs document `doc_id`.

    Launches Chromium with a persistent profile (`profile_dir`) so a Google
    sign-in survives between runs: run once with headless=False, sign in when
    the window shows Google's login page, and every later run reuses that
    session. The record's keystroke clock is replayed at `speed`, with
    silences longer than `max_gap_s` seconds shortened if given.

    Returns a summary dict: {"emitter", "doc_id", "url", "keystrokes",
    "dispatched", "aborted", "duration_s"}. Ctrl+C, or closing the tab or
    browser, aborts the run; the browser is shut down cleanly either way.
    """
    if not doc_id:
        raise ValueError("doc_id is required to emit to Google Docs")
    try:
        from playwright.sync_api import (
            Error as PlaywrightError,
            TimeoutError as PlaywrightTimeoutError,
            sync_playwright,
        )
    except ImportError as exc:
        raise ImportError(
            "Emitting to Google Docs requires Playwright: "
            "pip install playwright && playwright install chromium"
        ) from exc

    keystrokes = record.get("keystrokes", [])
    url = DOCS_EDIT_URL.format(doc_id=doc_id)
    print(
        f"typetrace: emitting {len(keystrokes)} keystrokes to Google Docs document {doc_id}",
        file=sys.stderr,
    )
    print(
        f"  {url}\n"
        f"  speed x{speed}, gap cap {max_gap_s if max_gap_s is not None else 'off'},"
        f" profile {profile_dir!r} (sign in to Google in the window if asked)",
        file=sys.stderr,
    )

    events = iter_timeline(record, speed=speed, max_gap_s=max_gap_s)
    clock: Dict[str, Any] = {"dispatched": 0, "aborted": False, "duration_s": 0.0}
    interrupted = False
    closed = False

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            profile_dir, headless=headless
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(url, wait_until="domcontentloaded")
            try:
                page.wait_for_selector(EDITOR_SELECTOR, timeout=_EDITOR_TIMEOUT_MS)
            except PlaywrightTimeoutError as exc:
                raise RuntimeError(
                    "The Google Docs editor never appeared. If the browser is "
                    "showing a Google sign-in page, sign in under this profile "
                    f"({profile_dir!r}) and re-run; the session is kept."
                ) from exc
            page.click(EDITOR_SELECTOR)

            cdp = context.new_cdp_session(page)

            def dispatch(event: str, keystroke: Dict[str, Any]) -> None:
                nonlocal closed
                if closed:
                    return
                try:
                    for payload in payloads_for(keystroke, event):
                        cdp.send("Input.dispatchKeyEvent", payload)
                except PlaywrightError:
                    # The tab or browser went away mid-run.
                    closed = True

            def should_abort() -> bool:
                return closed or page.is_closed()

            try:
                clock = run_timeline(events, dispatch, should_abort=should_abort)
            except KeyboardInterrupt:
                interrupted = True
        finally:
            # Closing the persistent context closes the browser, even when
            # navigation or the run above failed partway.
            context.close()

    aborted = interrupted or closed or clock["aborted"]
    state = "aborted" if aborted else "finished"
    if interrupted:
        detail = " (Ctrl+C)"
    elif closed:
        detail = " (browser closed)"
    else:
        detail = ""
    print(
        f"typetrace: {state}{detail} - {clock['dispatched']} dispatches"
        f" in {clock['duration_s']:.1f} s",
        file=sys.stderr,
    )

    return {
        "emitter": "docs",
        "doc_id": doc_id,
        "url": url,
        "keystrokes": len(keystrokes),
        "dispatched": clock["dispatched"],
        "aborted": aborted,
        "duration_s": clock["duration_s"],
    }
