"""Tests for desktop_emitter's dispatch loop, against a fake pynput.

The pure character mapping (describe_character / describe_keystroke) is
covered in test_keymaps; this module exercises emit_to_desktop itself. The
pynput stand-in injected via sys.modules models physical keyswitches: a key
press types its character immediately, shifted iff Shift is held at that
moment. That is exactly the situation the Shift-bleed regression came from -
Shift used to be held for the shifted keystroke's whole dwell including its
rollover extension, so a key going down before that keyup was typed shifted
("THe" for "The"). The fix binds Shift to the press alone.

pynput is an optional dependency and is imported lazily inside
emit_to_desktop, so the fake only has to be in sys.modules by call time.
"""

import sys
import types

import pytest

import pipeline

desktop_emitter = pytest.importorskip("desktop_emitter")


class _FakeKeyCode:
    def __init__(self, char):
        self.char = char

    @classmethod
    def from_char(cls, char):
        return cls(char)


class _FakeKey:
    """The pynput.keyboard.Key members the emitter touches."""

    esc = "esc"
    shift = "shift"
    enter = "enter"
    tab = "tab"
    space = "space"
    backspace = "backspace"


_NAMED_OUTPUT = {"enter": "\n", "tab": "\t", "space": " ", "backspace": "\b"}


class _PhysicalKeyboard:
    """A pynput.keyboard.Controller stand-in modelling physical keyswitches.

    press() types the key's character immediately, shifted iff Shift is
    currently down (letters upper-case, base symbols via SHIFT_PAIRS), and
    backspace deletes. release() only tracks the Shift state. Any Shift held
    across a keystroke boundary therefore corrupts the produced text exactly
    as it would on real hardware.
    """

    instances = []

    def __init__(self):
        type(self).instances.append(self)
        self.events = []
        self.shift_level = 0
        self.text = ""

    def press(self, key):
        self.events.append(("press", key))
        if key == _FakeKey.shift:
            self.shift_level += 1
            return
        if key in _NAMED_OUTPUT:
            char = _NAMED_OUTPUT[key]
        else:
            char = key.char
            if self.shift_level:
                char = desktop_emitter.SHIFT_PAIRS.get(char, char.upper())
        if char == "\b":
            self.text = self.text[:-1]
        else:
            self.text += char

    def release(self, key):
        self.events.append(("release", key))
        if key == _FakeKey.shift:
            self.shift_level -= 1


class _FakeListener:
    def __init__(self, on_press=None):
        self.on_press = on_press
        self.running = False

    def start(self):
        self.running = True

    def stop(self):
        self.running = False


@pytest.fixture
def fake_pynput(monkeypatch):
    """Install the fake pynput modules; return the keyboard class."""
    keyboard = types.ModuleType("pynput.keyboard")
    keyboard.Key = _FakeKey
    keyboard.KeyCode = _FakeKeyCode
    keyboard.Controller = _PhysicalKeyboard
    keyboard.Listener = _FakeListener
    pynput = types.ModuleType("pynput")
    pynput.keyboard = keyboard
    monkeypatch.setitem(sys.modules, "pynput", pynput)
    monkeypatch.setitem(sys.modules, "pynput.keyboard", keyboard)
    _PhysicalKeyboard.instances.clear()
    return _PhysicalKeyboard


def _keystroke(index, char, keydown_ms, keyup_ms, kind="key"):
    return {
        "index": index, "kind": kind, "char": char,
        "keydown_ms": keydown_ms, "keyup_ms": keyup_ms,
        "dwell_ms": keyup_ms - keydown_ms,
        "iki_ms": 0, "motor_iki_ms": 0, "flight_ms": 0, "role": "text",
    }


def _typed_name(key):
    return getattr(key, "char", key)


def test_shift_wraps_the_press_only_and_does_not_bleed_into_rollover(fake_pynput):
    # The micro-repro the regression was found with: "h" goes down while "T"
    # is still held, and must be typed as "h", not "H".
    record = {"keystrokes": [_keystroke(0, "T", 0, 130), _keystroke(1, "h", 50, 150)]}
    desktop_emitter.emit_to_desktop(record, speed=1000.0, initial_delay_s=0.0)
    keyboard = fake_pynput.instances[0]
    assert keyboard.text == "Th"
    assert [(action, _typed_name(key)) for action, key in keyboard.events] == [
        ("press", "shift"), ("press", "t"), ("release", "shift"),
        ("press", "h"), ("release", "t"), ("release", "h"),
    ]


@pytest.mark.parametrize(
    "text,seed",
    [
        ("The Quick Brown Fox jumps over the lazy dog.", 2),
        ("He laughed. She cried. Then They left.", 4),
        ("A Big Cat sat There. Wow!", 3),
    ],
)
def test_real_records_reproduce_their_target_text(fake_pynput, text, seed):
    record = pipeline.generate(text=text, seed=seed)
    keystrokes = record["keystrokes"]
    # The fixture cases only guard the bleed if a shifted keystroke's keyup
    # genuinely slides past the next keystroke's keydown; assert that holds
    # so a timing-model change cannot silently turn these into no-ops.
    assert any(
        following["keydown_ms"] < current["keyup_ms"]
        for current, following in zip(keystrokes, keystrokes[1:])
        if desktop_emitter.describe_keystroke(current).shift
    )
    desktop_emitter.emit_to_desktop(record, speed=1000.0, initial_delay_s=0.0)
    keyboard = fake_pynput.instances[0]
    assert keyboard.text == record["target_text"]


def test_shift_presses_and_releases_stay_balanced(fake_pynput):
    record = pipeline.generate(text="She Typed CAPS! And (Maybe) More? Yes.", seed=8)
    desktop_emitter.emit_to_desktop(record, speed=1000.0, initial_delay_s=0.0)
    keyboard = fake_pynput.instances[0]
    presses = sum(
        1 for action, key in keyboard.events
        if action == "press" and key == _FakeKey.shift
    )
    releases = sum(
        1 for action, key in keyboard.events
        if action == "release" and key == _FakeKey.shift
    )
    assert presses == releases > 0
    assert keyboard.shift_level == 0
