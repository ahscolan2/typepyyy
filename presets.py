"""Built-in writing setups and per-user storage paths."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PARAGRAPH = (
    "On Saturday morning, our neighborhood library opened its doors for a small "
    "community workshop. A dozen people gathered around a long table, each carrying "
    "a notebook and an idea they wanted to explore. Maya brought sketches for a "
    "garden, while Leo described a simple website that could help neighbors share "
    "tools. We spent the first hour asking questions, comparing plans, and correcting "
    "details that seemed clear at first but needed another look. By lunchtime, the "
    "table was covered with notes, arrows, and a few crossed-out sentences. Nobody "
    "had finished a perfect project, but everyone had made something more useful "
    "than the idea they arrived with. We agreed to meet again next month, bring "
    "our progress, and leave plenty of room for new suggestions."
)
CHECK_TEXT = "Hello Google Docs! Testing TypeTrace [v2.0]: 100% real (keystrokes) + #symbols."


def get_app_data_dir() -> Path:
    base = Path(os.environ["LOCALAPPDATA"]) if os.environ.get("LOCALAPPDATA") else Path.home() / ".local" / "share"
    path = base / "TypeTrace"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_default_browser_profile_dir() -> Path:
    return get_app_data_dir() / "browser_profile"


def get_default_output_dir() -> Path:
    path = get_app_data_dir() / "output"
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass(frozen=True)
class Preset:
    slug: str
    name: str
    description: str
    sample_text: str = PARAGRAPH
    parameters: dict[str, Any] = field(default_factory=dict)
    emit_enabled: bool = False
    emit_speed: float = 1.0
    emit_max_gap_s: float | None = None

    def to_gui_parameters(self) -> dict[str, str]:
        # Import here to share the GUI's defaults without a module import cycle.
        from gui import default_parameter_strings
        values = default_parameter_strings()
        values.update({key: "" if value is None else str(value) for key, value in self.parameters.items()})
        return values

    def to_gui_emit_options(self) -> dict[str, Any]:
        return {
            "enabled": self.emit_enabled, "target": "desktop",
            "emit_speed": str(self.emit_speed),
            "emit_max_gap_s": "" if self.emit_max_gap_s is None else str(self.emit_max_gap_s),
            "doc_id": "", "headless": False,
            "browser_profile": str(get_default_browser_profile_dir()),
            "browser_channel": "chrome", "editor_timeout_s": "300",
        }


BUILTIN_PRESETS = (
    Preset("preview", "Preview only", "Generate and save a record without typing into an editor."),
    Preset(
        "confirmed", "Confirmed Google Docs",
        "Settings from the paragraph you tested in Google Docs: corrections and pauses at 2.5× speed.",
        sample_text="\n\n" + PARAGRAPH,
        parameters={"seed": 23, "typo_rate": 0.06},
        emit_enabled=True, emit_speed=2.5,
    ),
    Preset(
        "slower", "Slower writing",
        "Half the replay speed of the confirmed setup, with its pauses preserved.",
        parameters={"seed": 23, "typo_rate": 0.06},
        emit_enabled=True, emit_speed=1.25,
    ),
    Preset(
        "breaks", "Writing with breaks",
        "Slower writing with a break after roughly 240 characters; pauses last up to 20 seconds.",
        parameters={"seed": 23, "typo_rate": 0.06, "session_chars": 240},
        emit_enabled=True, emit_speed=1.25, emit_max_gap_s=25.0,
    ),
    Preset(
        "quick-verify", "Quick editor check",
        "Short punctuation and correction sample from the successful Google Docs test.",
        sample_text=CHECK_TEXT, parameters={"seed": 42, "typo_rate": 0.06},
        emit_enabled=True, emit_speed=2.5,
    ),
)


def list_presets() -> list[Preset]:
    return list(BUILTIN_PRESETS)


def get_preset(name: str) -> Preset:
    for preset in BUILTIN_PRESETS:
        if name.casefold() in (preset.slug.casefold(), preset.name.casefold()):
            return preset
    raise KeyError(f"Unknown setup: {name}")
