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

import re
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

from emit_common import (
    ReplayCancelled, SHIFTED_TO_BASE, finite_number, iter_timeline,
    run_timeline, validate_live_text,
)

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
_SHIFTED_CHARS = SHIFTED_TO_BASE

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
    if not isinstance(char, str) or len(char) != 1:
        raise ValueError("a browser key must contain exactly one character")
    if char in _NAMED_KEYS:
        return dict(_NAMED_KEYS[char])

    shift = False
    base = char
    if char in _SHIFTED_CHARS:
        base = _SHIFTED_CHARS[char]
        shift = True

    if "a" <= base <= "z" or "A" <= base <= "Z":
        code = f"Key{base.upper()}"
        vk = ord(base.upper())
        if base.isupper():
            shift = True
    elif len(base) == 1 and "0" <= base <= "9":
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


def normalize_doc_id(value: str) -> str:
    """Accept a document ID or a Google Docs editing URL."""
    if not isinstance(value, str):
        raise ValueError("doc_id must be a Google Docs URL or document ID")
    value = value.strip()
    if value.lower().startswith(("https://", "http://")):
        parsed = urlparse(value)
        match = re.fullmatch(r"/document/(?:u/\d+/)?d/([A-Za-z0-9_-]+)(?:/.*)?", parsed.path)
        if parsed.scheme.lower() != "https" or parsed.netloc.lower() != "docs.google.com" or not match:
            raise ValueError("expected an https://docs.google.com/document/d/... URL")
        value = match[1]
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("doc_id must be a Google Docs URL or document ID")
    return value


def replay_in_page(page, record, *, speed=1.0, max_gap_s=None, should_abort=None):
    """Replay into an already focused Chromium editor, releasing keys on exit."""
    events = list(iter_timeline(record, speed=speed, max_gap_s=max_gap_s))
    validate_live_text(record)

    def cancelled():
        return page.is_closed() or (should_abort is not None and should_abort())

    if cancelled():
        return {"dispatched": 0, "aborted": True, "duration_s": 0.0}
    cdp = page.context.new_cdp_session(page)
    held = {}

    def dispatch(event, key):
        if event == "down":
            held[id(key)] = key
        try:
            for payload in payloads_for(key, event):
                if cancelled():
                    raise ReplayCancelled()
                cdp.send("Input.dispatchKeyEvent", payload)
        except Exception as exc:
            if cancelled():
                raise ReplayCancelled() from exc
            raise
        if event == "up":
            held.pop(id(key), None)

    try:
        return run_timeline(events, dispatch, should_abort=cancelled)
    finally:
        for key in held.values():
            for payload in payloads_for(key, "up"):
                try:
                    cdp.send("Input.dispatchKeyEvent", payload)
                except Exception:
                    pass  # A failed character release must not skip Shift.
        try:
            cdp.detach()
        except Exception:
            pass


def emit_to_google_docs(
    record: Dict[str, Any],
    *,
    doc_id: str,
    speed: float = 1.0,
    max_gap_s: Optional[float] = None,
    headless: bool = False,
    profile_dir: str = ".typetrace-browser-profile",
    browser_channel: str = "chromium",
    editor_timeout_s: float = 300.0,
    should_abort: Optional[Callable[[], bool]] = None,
    on_status: Optional[Callable[[str], None]] = None,
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
    doc_id = normalize_doc_id(doc_id)
    editor_timeout_s = finite_number(editor_timeout_s, "editor_timeout_s", positive=True)
    if browser_channel not in ("chromium", "chrome", "msedge"):
        raise ValueError("browser_channel must be chromium, chrome or msedge")
    list(iter_timeline(record, speed=speed, max_gap_s=max_gap_s))
    validate_live_text(record)
    keystrokes = record.get("keystrokes", [])
    url = DOCS_EDIT_URL.format(doc_id=doc_id)
    def status(message):
        print(f"TypeTrace: {message}", file=sys.stderr, flush=True)
        if on_status:
            on_status(message)

    page = None

    def cancelled():
        return ((should_abort is not None and should_abort())
                or (page is not None and page.is_closed()))

    clock: Dict[str, Any] = {"dispatched": 0, "aborted": False, "duration_s": 0.0}
    saved = False
    context = None
    closed_before_cleanup = False
    if cancelled():
        return {
            "emitter": "docs", "doc_id": doc_id, "url": url,
            "keystrokes": len(keystrokes), "dispatched": 0,
            "aborted": True, "duration_s": 0.0, "saved": False,
        }
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
    try:
        if cancelled():
            clock["aborted"] = True
        else:
            with sync_playwright() as playwright:
                status("Opening Google Docs. Sign in in the browser if needed.")
                context = playwright.chromium.launch_persistent_context(
                    str(Path(profile_dir).expanduser().resolve()), headless=headless,
                    channel=browser_channel, chromium_sandbox=True,
                )
                try:
                    page = context.pages[0] if context.pages else context.new_page()
                    if cancelled():
                        raise ReplayCancelled()
                    page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                    deadline = time.monotonic() + editor_timeout_s
                    while not cancelled():
                        if time.monotonic() >= deadline:
                            raise RuntimeError(
                                "Google Docs was not ready before the timeout. Check sign-in and edit "
                                "access. If Google rejects automated sign-in, use desktop replay in "
                                "your normal browser. Increase --editor-timeout-s for more login time."
                            )
                        try:
                            page.wait_for_selector(EDITOR_SELECTOR, timeout=500)
                            if cancelled():
                                break
                            editor = page.frame_locator('.docs-texteventtarget-iframe').locator('[contenteditable="true"]')
                            editor.wait_for(state="attached", timeout=500)
                            if not editor.is_editable(timeout=500):
                                page.wait_for_timeout(100)
                                continue
                            if cancelled():
                                break
                            page.locator(EDITOR_SELECTOR).click(timeout=500)
                            if cancelled():
                                break
                            editor.focus(timeout=500)
                            break
                        except PlaywrightTimeoutError:
                            continue
                    if cancelled():
                        clock["aborted"] = True
                    else:
                        page.keyboard.press("Meta+ArrowDown" if sys.platform == "darwin" else "Control+End")
                        status(f"Appending {len(keystrokes)} keystrokes. Press Stop or Ctrl+C to cancel.")
                        clock = replay_in_page(page, record, speed=speed, max_gap_s=max_gap_s, should_abort=cancelled)
                        if not clock["aborted"]:
                            status("Typing finished. Waiting for Google Docs to save…")
                            page.wait_for_timeout(750)
                            save_deadline = time.monotonic() + 30
                            indicator = page.locator(
                                '[aria-label*="Saved to Drive"], [aria-label*="All changes saved"], '
                                '[data-tooltip*="Saved to Drive"], [data-tooltip*="All changes saved"]'
                            )
                            while not cancelled() and time.monotonic() < save_deadline:
                                if indicator.count():
                                    saved = True
                                    break
                                page.wait_for_timeout(250)
                            if cancelled():
                                clock["aborted"] = True
                            elif not saved:
                                raise RuntimeError(
                                    "Typing finished, but Google Docs did not confirm saving within 30 seconds. "
                                    "Check the document before replaying again to avoid duplicate text."
                                )
                finally:
                    closed_before_cleanup = page is not None and page.is_closed()
                    try:
                        context.close()
                    except PlaywrightError:
                        pass
    except (KeyboardInterrupt, ReplayCancelled):
        clock["aborted"] = True
    except PlaywrightError as exc:
        if closed_before_cleanup or (should_abort is not None and should_abort()):
            clock["aborted"] = True
        else:
            raise RuntimeError(
                "Google Docs replay failed. Check the document before retrying; some text may "
                "already have been typed. Close other browsers using the TypeTrace profile. "
                f"Browser detail: {exc}"
            ) from exc

    aborted = clock["aborted"]
    state = "aborted" if aborted else "finished"
    status(f"{state}: {clock['dispatched']} key events in {clock['duration_s']:.1f}s")

    return {
        "emitter": "docs",
        "doc_id": doc_id,
        "url": url,
        "keystrokes": len(keystrokes),
        "dispatched": clock["dispatched"],
        "aborted": aborted,
        "duration_s": clock["duration_s"],
        "saved": saved,
    }
