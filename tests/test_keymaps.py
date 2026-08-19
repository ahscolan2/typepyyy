"""Tests for the emitters' pure keystroke -> key mappings.

Both emitters keep their character-to-key mapping as module-level functions
with no playwright/pynput imports, so the mapping is testable on a machine
with neither installed. These tests pin the details the v2 emitter rewrite
was built to get right: Backspace is a bare key event with no text payload
(and vk 8 so Chrome registers it), a shifted character wraps its key in a
Shift down/Shift up pair that restores the prior state, and Enter/space carry
complete code and virtual-key fields. If an emitter module is not on disk the
whole module skips.
"""

import pytest

pytest.importorskip("emit_common")
docs = pytest.importorskip("docs_emitter")
desktop = pytest.importorskip("desktop_emitter")


def _keystroke(kind="key", char="a", index=0):
    """A keystroke dict in the v2 record schema."""
    return {
        "index": index,
        "kind": kind,
        "char": char,
        "keydown_ms": 0,
        "keyup_ms": 80,
        "dwell_ms": 80,
        "iki_ms": 0,
        "motor_iki_ms": 0,
        "flight_ms": 0,
        "role": "text",
    }


# --- docs_emitter: CDP key payloads ------------------------------------------


def test_docs_backspace_is_a_bare_key_event_with_no_text():
    ks = _keystroke(kind="backspace", char=None)
    for event, expected_type in (("down", "rawKeyDown"), ("up", "keyUp")):
        payloads = docs.payloads_for(ks, event)
        # No char event, and no text field anywhere: Backspace is not text.
        assert [p["type"] for p in payloads] == [expected_type]
        assert payloads[0]["code"] == "Backspace"
        assert payloads[0]["windowsVirtualKeyCode"] == 8
        assert payloads[0]["nativeVirtualKeyCode"] == 8
        assert "text" not in payloads[0]


def test_docs_uppercase_wraps_the_key_in_a_shift_press_and_release():
    ks = _keystroke(char="A")
    down = docs.payloads_for(ks, "down")
    up = docs.payloads_for(ks, "up")

    # Shift goes down first and comes up last, so it is never left held.
    assert [(p["type"], p["code"]) for p in down] == [
        ("rawKeyDown", "ShiftLeft"),
        ("rawKeyDown", "KeyA"),
        ("char", "KeyA"),
    ]
    assert [(p["type"], p["code"]) for p in up] == [
        ("keyUp", "KeyA"),
        ("keyUp", "ShiftLeft"),
    ]
    assert down[0]["windowsVirtualKeyCode"] == 16  # Shift

    # The key itself is KeyA / vk 65, and the char event carries the text.
    key_down = down[1]
    assert key_down["windowsVirtualKeyCode"] == 65
    assert key_down["nativeVirtualKeyCode"] == 65
    assert down[2]["text"] == "A"

    # Every payload sent while the character is typed carries the Shift
    # modifier bit (Alt=1, Ctrl=2, Meta=4, Shift=8) except the Shift key's own.
    assert all(p.get("modifiers", 0) & 8 for p in down[1:])


def test_docs_enter_maps_to_code_enter_vk_13():
    down = docs.payloads_for(_keystroke(char="\n"), "down")
    raw, char = down  # rawKeyDown, then a char event carrying "\r"
    assert raw["type"] == "rawKeyDown"
    assert raw["code"] == "Enter"
    assert raw["windowsVirtualKeyCode"] == 13
    assert raw["nativeVirtualKeyCode"] == 13
    assert char["type"] == "char"
    assert char["text"] == "\r"


def test_docs_space_maps_to_code_space_vk_32():
    down = docs.payloads_for(_keystroke(char=" "), "down")
    raw, char = down
    assert raw["type"] == "rawKeyDown"
    assert raw["code"] == "Space"
    assert raw["windowsVirtualKeyCode"] == 32
    assert raw["nativeVirtualKeyCode"] == 32
    assert char["type"] == "char"
    assert char["text"] == " "


# --- desktop_emitter: KeySpec mapping ----------------------------------------


def test_desktop_backspace_maps_to_the_backspace_key():
    spec = desktop.describe_keystroke(_keystroke(kind="backspace", char=None))
    assert spec.name == "backspace"
    assert spec.char is None
    assert spec.shift is False


def test_desktop_uppercase_holds_shift_on_the_base_key():
    # The emitter presses Shift just before the key and releases it right
    # after (desktop_emitter's dispatch), which the KeySpec records.
    assert desktop.describe_keystroke(_keystroke(char="A")) == desktop.KeySpec(
        char="a", shift=True
    )
    # A shifted symbol likewise names the unshifted keycap.
    assert desktop.describe_character("!") == desktop.KeySpec(
        char="1", shift=True
    )


def test_desktop_enter_and_space_are_named_keys():
    assert desktop.describe_character("\n") == desktop.KeySpec(name="enter")
    assert desktop.describe_character(" ") == desktop.KeySpec(name="space")
    # An ordinary letter is a plain KeyCode with no modifiers.
    assert desktop.describe_character("a") == desktop.KeySpec(char="a")
