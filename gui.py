"""Tkinter interface for generating records and replaying them in editors.

Uses the same defaults and generator as the CLI. Workers report through a
queue; only the main thread touches widgets. Launch with ``typetrace-gui``
or ``python gui.py``.
"""

import argparse
import gc
import importlib.util
import math
import queue
import threading
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy.random  # noqa: F401 - see below
import macro_scripter as ms
import timing_engine as te
from main import emit_record, generate_full_output, load_text, render_record, write_output
from presets import PARAGRAPH, get_default_browser_profile_dir, get_default_output_dir, get_preset, list_presets
from timing_engine import AR1_PHI, DEFAULT_TARGET_AUTOCORRELATION

# numpy lazily imports its np.random submodule on first attribute access.
# Here that first access is TimingEngine's np.random.Generator(...) inside the
# background worker thread, and a module import does enough allocation to
# trigger the cyclic GC there - which cannot be allowed to finalise tkinter
# objects on that thread. Importing eagerly keeps the free work on the main
# thread.

WINDOW_TITLE = "TypeTrace"
DEFAULT_TEXT = PARAGRAPH

# Poll interval for results coming back from the worker thread, ms. Short
# enough to feel immediate, long enough not to spin.
POLL_MS = 60

MONO = ("Consolas", 10)

# The CLI's --profile choices and default.
PROFILES = ("slow", "average", "fast")
PROFILE_DEFAULT = "average"

# The CLI's --format choices, with the labels the window shows for them. The
# CLI default (json) is the default here too.
OUTPUT_FORMATS = (
    ("json", "JSON record (machine-readable)"),
    ("replay", "Writing replay (readable)"),
    ("replay-full", "Every keystroke, one per line"),
)
DEFAULT_FORMAT = "json"
FORMAT_EXTENSIONS = {"json": ".json", "replay": ".txt", "replay-full": ".txt"}

# A writable path also works when the EXE is launched from a read-only folder.
DEFAULT_BROWSER_PROFILE = str(get_default_browser_profile_dir())

# Live replay is an optional feature with its own dependencies (the CLI's
# [docs] and [desktop] extras); the generator itself needs neither. The value
# is (module that must be importable, pip command that provides it).
EMIT_EXTRAS = {
    "docs": (
        "playwright",
        "pip install '.[docs]', then 'playwright install chromium'",
    ),
    "desktop": ("pynput", "pip install '.[desktop]'"),
}
EMIT_TARGETS = tuple(EMIT_EXTRAS)


# -- parameter rules ---------------------------------------------------------
#
# The widgets are thin shells around the Field definitions below, and the
# Field definitions are thin shells around the CLI's own rules: the same
# defaults, the same ranges the generator validates (typo_rate and
# r_burst_probability in [0, 1], a positive session length, an autocorrelation
# in [0, AR1_PHI)). Validating here means a bad value is a dialog before any
# work starts rather than a traceback after it.


def _integer(raw: str) -> int:
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{raw!r} is not an integer") from None


def _number(raw: str) -> float:
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"{raw!r} is not a number") from None
    if not math.isfinite(value):
        raise ValueError("must be a finite number")
    return value


def _typo_model_name(raw: str) -> str:
    return raw.strip().lower()


def _typo_model(value: str) -> Optional[str]:
    if value in ms.TYPO_MODELS:
        return None
    options = " or ".join(repr(name) for name in ms.TYPO_MODELS)
    return f"must be {options}, got {value!r}"


def _probability(value: float) -> Optional[str]:
    if 0.0 <= value <= 1.0:
        return None
    return f"must be in [0, 1], got {value}"


def _positive(value: float) -> Optional[str]:
    if value > 0:
        return None
    return f"must be positive, got {value}"


def _non_negative(value: float) -> Optional[str]:
    if value >= 0.0:
        return None
    return f"must be 0 or more, got {value}"


def _below_1(value: float) -> Optional[str]:
    if 0.0 <= value < 1.0:
        return None
    return f"must be in [0, 1), got {value}"


def _autocorrelation(value: float) -> Optional[str]:
    if 0.0 <= value < AR1_PHI:
        return None
    return f"must be in [0, {AR1_PHI}), got {value}"


@dataclass(frozen=True)
class Field:
    """One model parameter: how to label it, parse it, and check it.

    `default` is the string its entry starts with, matching the CLI's own
    default for that flag. A blank field means "leave the generator's default
    alone"; when blank_is_default is set the key is dropped from the kwargs
    entirely so the function signature's default applies, otherwise the value
    None is passed through (which is itself the CLI default for that flag).
    """

    key: str
    label: str
    default: str
    hint: str
    parse: Callable[[str], Any]
    check: Optional[Callable[[Any], Optional[str]]] = None
    blank_is_default: bool = False


GENERATOR_FIELDS: Tuple[Field, ...] = (
    Field(
        key="seed",
        label="Seed",
        default="",
        hint="integer; blank for a random run (e.g. 42)",
        parse=_integer,
        check=_non_negative,
    ),
    Field(
        key="typo_rate",
        label="Typo rate",
        default=str(ms.TYPO_RATE),
        hint="per character, 0-1",
        parse=_number,
        check=_probability,
        blank_is_default=True,
    ),
    Field(
        key="typo_model",
        label="Typo model",
        default=ms.TYPO_MODEL_DEFAULT,
        hint="neighbor or rich; rich adds the cognitive error kinds",
        parse=_typo_model_name,
        check=_typo_model,
        blank_is_default=True,
    ),
    Field(
        key="r_burst_probability",
        label="Revision probability",
        default=str(ms.R_BURST_PROBABILITY),
        hint="0-1; chance a burst ends in a rewrite",
        parse=_number,
        check=_probability,
        blank_is_default=True,
    ),
    Field(
        key="structural_revision_rate",
        label="Structural revision rate",
        default=str(ms.STRUCTURAL_REVISION_RATE),
        hint="0-1; chance a finished sentence is deleted and retyped",
        parse=_number,
        check=_probability,
        blank_is_default=True,
    ),
    Field(
        key="session_chars",
        label="Session length",
        default="",
        hint="characters; blank for 20-90 real minutes",
        parse=_integer,
        check=_positive,
    ),
    Field(
        key="target_autocorrelation",
        label="Rhythm autocorrelation",
        default="",
        hint=(
            f"blank for {DEFAULT_TARGET_AUTOCORRELATION}; "
            f"must be under {AR1_PHI}"
        ),
        parse=_number,
        check=_autocorrelation,
    ),
    Field(
        key="fatigue_rate",
        label="Fatigue rate",
        default=str(te.FATIGUE_RATE),
        hint="0 or more; slowdown added per 10 min of typing",
        parse=_number,
        check=_non_negative,
        blank_is_default=True,
    ),
    Field(
        key="warmup_strength",
        label="Warmup strength",
        default=str(te.WARMUP_STRENGTH),
        hint="0-1; initial slowness, gone in about a minute",
        parse=_number,
        check=_below_1,
        blank_is_default=True,
    ),
    Field(
        key="familiarity_boost",
        label="Familiarity boost",
        default=str(te.FAMILIARITY_BOOST),
        hint="0-1; speedup on digraphs already typed in this document",
        parse=_number,
        check=_below_1,
        blank_is_default=True,
    ),
)


def default_parameter_strings() -> Dict[str, str]:
    """The untouched parameter fields, keyed the way collect_parameters wants."""
    raw = {"profile": PROFILE_DEFAULT}
    for field in GENERATOR_FIELDS:
        raw[field.key] = field.default
    return raw


def collect_parameters(raw: Dict[str, str]) -> Dict[str, Any]:
    """Turn the raw field strings into keyword arguments for generate_full_output.

    Raises ValueError, prefixed with the offending field's label, on anything
    that is unparseable or out of range.
    """
    if raw["profile"] not in PROFILES:
        raise ValueError(f"Profile: choose one of {', '.join(PROFILES)}")
    kwargs: Dict[str, Any] = {"profile": raw["profile"]}
    for field in GENERATOR_FIELDS:
        text = raw[field.key].strip()
        if not text:
            if not field.blank_is_default:
                kwargs[field.key] = None
            continue
        try:
            value = field.parse(text)
        except ValueError as exc:
            raise ValueError(f"{field.label}: {exc}") from None
        if field.check is not None:
            problem = field.check(value)
            if problem is not None:
                raise ValueError(f"{field.label}: {problem}")
        kwargs[field.key] = value
    return kwargs


# -- emission options --------------------------------------------------------


def default_emit_options() -> Dict[str, Any]:
    """The untouched emission controls (off, matching the CLI default)."""
    return {
        "enabled": False,
        "target": "desktop",
        "doc_id": "",
        "emit_speed": "1.0",
        "emit_max_gap_s": "",
        "headless": False,
        "browser_profile": DEFAULT_BROWSER_PROFILE,
        "browser_channel": "chrome",
        "editor_timeout_s": "300",
    }


def collect_emit_options(raw: Dict[str, Any]) -> Optional[argparse.Namespace]:
    """Turn the emission controls into the namespace main.emit_record consumes.

    Returns None when emission is switched off. The namespace carries exactly
    the attributes the CLI parser produces for the --emit flags, so the call
    into main.emit_record is the same call the CLI makes.
    """
    if not raw["enabled"]:
        return None
    target = raw["target"]
    if target not in EMIT_TARGETS:
        raise ValueError(f"Unknown replay target: {target!r}")

    speed_text = raw["emit_speed"].strip() or "1.0"
    try:
        speed = _number(speed_text)
    except ValueError as exc:
        raise ValueError(f"Emission speed: {exc}") from None
    if speed <= 0.0:
        raise ValueError(f"Emission speed: must be above 0, got {speed}")

    gap_text = raw["emit_max_gap_s"].strip()
    max_gap_s: Optional[float] = None
    if gap_text:
        try:
            max_gap_s = _number(gap_text)
        except ValueError as exc:
            raise ValueError(f"Silence cap: {exc}") from None
        if max_gap_s < 0.0:
            raise ValueError(f"Silence cap: must be 0 or more, got {max_gap_s}")

    doc_id = raw["doc_id"].strip()
    if target == "docs" and not doc_id:
        raise ValueError("Doc ID: required for Google Docs emission")

    browser_profile = raw["browser_profile"].strip() or DEFAULT_BROWSER_PROFILE
    browser_channel = raw.get("browser_channel", "chromium")
    if browser_channel not in ("chromium", "chrome", "msedge"):
        raise ValueError("Browser: choose chromium, chrome, or msedge")
    try:
        timeout = _number(str(raw.get("editor_timeout_s", "300")).strip() or "300")
    except ValueError as exc:
        raise ValueError(f"Editor timeout: {exc}") from None
    if timeout <= 0:
        raise ValueError("Editor timeout: must be above 0")

    return argparse.Namespace(
        emit=target,
        doc_id=doc_id or None,
        emit_speed=speed,
        emit_max_gap_s=max_gap_s,
        headless=bool(raw["headless"]),
        browser_profile=browser_profile,
        browser_channel=browser_channel,
        editor_timeout_s=timeout,
    )


def emit_support() -> Dict[str, Tuple[bool, str]]:
    """Whether each emission target's dependency is importable.

    The record generator does not import playwright or pynput, and neither
    does this check - find_spec answers without executing the module. The
    second element of each pair is the pip command that installs the missing
    extra, for the hint shown under the disabled controls.
    """
    support = {}
    for target, (module, install) in EMIT_EXTRAS.items():
        available = importlib.util.find_spec(module) is not None
        support[target] = (available, "" if available else install)
    return support


# -- output ------------------------------------------------------------------


def render_output(record: Dict[str, Any], fmt: str) -> str:
    """Serialise `record` in one of the CLI's output formats."""
    return render_record(record, fmt)


def summary_line(record: Dict[str, Any]) -> str:
    """The same one-line summary the CLI's --verbose prints."""
    stats = record["statistics"]
    return (
        f"{record['metadata']['input_chars']} chars, "
        f"{stats['keystrokes']} keystrokes, "
        f"{stats['backspaces']} backspaces, "
        f"{stats['session_gaps']} session gaps, "
        f"{stats['wpm_active']:.1f} WPM active, "
        f"lag-1 autocorrelation {stats['lag1_autocorrelation']:.3f}"
    )


# -- widgets -----------------------------------------------------------------


class ParameterRow:
    """One labelled entry, built from a Field.

    The row owns only the widget; the parsing and validation rules live on
    the Field so they can be exercised without a window.
    """

    def __init__(self, parent: tk.Widget, row: int, field: Field):
        self.field = field
        self.variable = tk.StringVar(value=field.default)

        ttk.Label(parent, text=field.label).grid(
            row=row, column=0, sticky="w", padx=(0, 8), pady=3
        )
        entry = ttk.Entry(parent, textvariable=self.variable, width=16)
        entry.grid(row=row, column=1, sticky="w", pady=3)
        ttk.Label(parent, text=field.hint, foreground="grey").grid(
            row=row, column=2, sticky="w", padx=(8, 0), pady=3
        )


class Application(ttk.Frame):
    def __init__(self, master: tk.Tk):
        super().__init__(master, padding=12)
        self.grid(row=0, column=0, sticky="nsew")
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)

        # Results arrive from a worker thread; tkinter is not thread-safe, so
        # they are queued and picked up by the main loop rather than touched
        # from the thread itself.
        self._results: "queue.Queue[tuple]" = queue.Queue()
        self._record: Optional[Dict[str, Any]] = None
        self._busy = False
        self._stop_requested = threading.Event()
        self._worker_thread: Optional[threading.Thread] = None
        self._support = emit_support()

        self._build_setups()
        self._settings = ttk.Notebook(self)
        self._settings.grid(row=1, column=0, sticky="nsew")
        self._input_page = ttk.Frame(self._settings, padding=8)
        model_page = ttk.Frame(self._settings, padding=8)
        replay_page = ttk.Frame(self._settings, padding=8)
        for page, title in (
            (self._input_page, "Text"), (model_page, "Model"),
            (replay_page, "Live replay"),
        ):
            page.columnconfigure(0, weight=1)
            page.rowconfigure(0, weight=1)
            self._settings.add(page, text=title)
        self._build_input()
        self._build_parameters(model_page)
        self._build_emission(replay_page)
        self._build_actions()
        self._result_tabs = ttk.Notebook(self)
        self._result_tabs.grid(row=3, column=0, sticky="nsew", pady=(10, 0))
        self._build_preview()
        self._build_status()

        self._drain_job = self.after(POLL_MS, self._drain)
        # Tk does not cancel `after` timers registered on a widget when the
        # widget is destroyed; without this the timer would fire a method on
        # a dead frame the next time the event loop runs.
        self.bind("<Destroy>", self._on_destroy)

    def _on_destroy(self, _event: tk.Event) -> None:
        self._stop_requested.set()
        if self._drain_job is not None:
            try:
                self.after_cancel(self._drain_job)
            except tk.TclError:
                pass
            self._drain_job = None
        # This frame owns command callbacks (buttons, binds, the poll timer)
        # whose wrappers close over bound methods of the frame itself, so
        # destroying the widget tree leaves a reference cycle only the cyclic
        # GC can break. tkinter Variables' __del__ calls back into Tk and
        # deadlocks if that GC happens to run on a background thread, so
        # finalise the cycle here, on the main thread with the interpreter
        # still alive.
        self.rows.clear()
        gc.collect()

    # -- layout --------------------------------------------------------------

    def _build_setups(self) -> None:
        bar = ttk.Frame(self)
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        bar.columnconfigure(1, weight=1)
        ttk.Label(bar, text="Writing setup").grid(row=0, column=0, padx=(0, 8))
        self.setup = tk.StringVar(value=get_preset("preview").name)
        self._setup_combo = ttk.Combobox(
            bar, textvariable=self.setup, values=[p.name for p in list_presets()],
            state="readonly", width=30,
        )
        self._setup_combo.grid(row=0, column=1, sticky="w")
        self._setup_combo.bind("<<ComboboxSelected>>", self._apply_setup)
        self._sample_button = ttk.Button(bar, text="Use example text", command=self._use_example)
        self._sample_button.grid(row=0, column=2, padx=(8, 0))
        self.setup_description = tk.StringVar(value=get_preset("preview").description)
        ttk.Label(bar, textvariable=self.setup_description, wraplength=720).grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(6, 0),
        )

    def _apply_setup(self, _event: Optional[tk.Event] = None) -> None:
        if self._busy:
            return
        preset = get_preset(self.setup.get())
        parameters = preset.to_gui_parameters()
        self.profile.set(parameters["profile"])
        for key, row in self.rows.items():
            row.variable.set(parameters[key])
        self.emit_enabled.set(preset.emit_enabled)
        self.emit_target.set("desktop")
        self.emit_speed.set(str(preset.emit_speed))
        self.emit_max_gap.set("" if preset.emit_max_gap_s is None else str(preset.emit_max_gap_s))
        self.setup_description.set(preset.description)
        self._update_emit_state()
        self.status.set(f"{preset.name} selected. Your text is unchanged.")

    def _use_example(self) -> None:
        if self._busy:
            return
        self.text_input.delete("1.0", "end")
        self.text_input.insert("1.0", get_preset(self.setup.get()).sample_text)
        self._update_char_count()

    def _build_input(self) -> None:
        frame = ttk.Frame(self._input_page)
        frame.grid(row=0, column=0, sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        self.text_input = tk.Text(frame, height=10, wrap="word", font=MONO, undo=True)
        self.text_input.grid(row=0, column=0, sticky="nsew")
        self.text_input.insert("1.0", DEFAULT_TEXT)

        scroll = ttk.Scrollbar(frame, command=self.text_input.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.text_input.configure(yscrollcommand=scroll.set)

        buttons = ttk.Frame(frame)
        buttons.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        buttons.columnconfigure(2, weight=1)
        ttk.Button(
            buttons, text="Load text file…", command=self._load_file
        ).grid(row=0, column=0, sticky="w")
        ttk.Button(buttons, text="Clear", command=self._clear_text).grid(
            row=0, column=1, sticky="w", padx=(6, 0)
        )
        self.char_count = tk.StringVar()
        ttk.Label(buttons, textvariable=self.char_count, foreground="grey").grid(
            row=0, column=2, sticky="e"
        )
        self.text_input.bind("<<Modified>>", self._text_modified)
        self._update_char_count()

    def _build_parameters(self, parent: tk.Widget) -> None:
        frame = ttk.Frame(parent)
        frame.grid(row=0, column=0, sticky="nsew")
        frame.columnconfigure(2, weight=1)

        ttk.Label(frame, text="Typing rhythm").grid(
            row=0, column=0, sticky="w", padx=(0, 8), pady=3
        )
        self.profile = tk.StringVar(value=PROFILE_DEFAULT)
        ttk.Combobox(
            frame,
            textvariable=self.profile,
            values=list(PROFILES),
            state="readonly",
            width=14,
        ).grid(row=0, column=1, sticky="w", pady=3)
        ttk.Label(frame, text="average is ~52 WPM", foreground="grey").grid(
            row=0, column=2, sticky="w", padx=(8, 0), pady=3
        )

        self.rows: Dict[str, ParameterRow] = {}
        for index, field in enumerate(GENERATOR_FIELDS, start=1):
            self.rows[field.key] = ParameterRow(frame, index, field)

    def _build_output_options(self, parent: tk.Widget) -> None:
        frame = ttk.Frame(parent)
        frame.grid(row=0, column=0, sticky="ew")
        frame.columnconfigure(0, weight=1)

        self.format = tk.StringVar(value=DEFAULT_FORMAT)
        for index, (value, label) in enumerate(OUTPUT_FORMATS):
            ttk.Radiobutton(
                frame,
                text=label,
                value=value,
                variable=self.format,
                command=self._refresh_view,
            ).grid(row=0, column=index, sticky="w", padx=(0, 12), pady=2)

    def _build_emission(self, parent: tk.Widget) -> None:
        frame = ttk.Frame(parent)
        frame.grid(row=0, column=0, sticky="nsew")
        frame.columnconfigure(2, weight=1)

        self.emit_enabled = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            frame,
            text="Replay the finished record into an editor",
            variable=self.emit_enabled,
            command=self._update_emit_state,
        ).grid(row=0, column=0, columnspan=3, sticky="w")

        ttk.Label(frame, text="Target").grid(
            row=1, column=0, sticky="w", padx=(0, 8), pady=3
        )
        self.emit_target = tk.StringVar(value="desktop")
        self._emit_target_combo = ttk.Combobox(
            frame,
            textvariable=self.emit_target,
            values=list(EMIT_TARGETS),
            state="readonly",
            width=10,
        )
        self._emit_target_combo.grid(row=1, column=1, sticky="w", pady=3)
        self._emit_target_combo.bind(
            "<<ComboboxSelected>>", self._update_emit_state
        )
        ttk.Label(
            frame, text="docs = a Google Doc; desktop = the focused window",
            foreground="grey",
        ).grid(row=1, column=2, sticky="w", padx=(8, 0), pady=3)

        ttk.Label(frame, text="Doc URL or ID").grid(
            row=2, column=0, sticky="w", padx=(0, 8), pady=3
        )
        self.doc_id = tk.StringVar()
        self._doc_id_entry = ttk.Entry(frame, textvariable=self.doc_id, width=26)
        self._doc_id_entry.grid(row=2, column=1, sticky="w", pady=3)
        ttk.Label(frame, text="paste the full document URL", foreground="grey").grid(
            row=2, column=2, sticky="w", padx=(8, 0), pady=3
        )

        ttk.Label(frame, text="Speed").grid(
            row=3, column=0, sticky="w", padx=(0, 8), pady=3
        )
        self.emit_speed = tk.StringVar(value="1.0")
        self._speed_entry = ttk.Entry(
            frame, textvariable=self.emit_speed, width=10
        )
        self._speed_entry.grid(row=3, column=1, sticky="w", pady=3)
        ttk.Label(
            frame, text="1.0 is the record's own timing; 2.0 is twice as fast",
            foreground="grey",
        ).grid(row=3, column=2, sticky="w", padx=(8, 0), pady=3)

        ttk.Label(frame, text="Silence cap").grid(
            row=4, column=0, sticky="w", padx=(0, 8), pady=3
        )
        self.emit_max_gap = tk.StringVar()
        self._gap_entry = ttk.Entry(
            frame, textvariable=self.emit_max_gap, width=10
        )
        self._gap_entry.grid(row=4, column=1, sticky="w", pady=3)
        ttk.Label(
            frame, text="seconds; blank keeps the faithful timing",
            foreground="grey",
        ).grid(row=4, column=2, sticky="w", padx=(8, 0), pady=3)

        self.headless = tk.BooleanVar(value=False)
        self._headless_check = ttk.Checkbutton(
            frame,
            text="Headless browser (log in once without this first)",
            variable=self.headless,
        )
        self._headless_check.grid(row=5, column=0, columnspan=3, sticky="w")

        ttk.Label(frame, text="Browser profile").grid(
            row=6, column=0, sticky="w", padx=(0, 8), pady=3
        )
        self.browser_profile = tk.StringVar(value=DEFAULT_BROWSER_PROFILE)
        self._profile_entry = ttk.Entry(
            frame, textvariable=self.browser_profile, width=26
        )
        self._profile_entry.grid(row=6, column=1, sticky="w", pady=3)
        self._profile_browse = ttk.Button(
            frame, text="Browse…", command=self._browse_profile
        )
        self._profile_browse.grid(row=6, column=2, sticky="w", padx=(8, 0), pady=3)

        ttk.Label(frame, text="Browser").grid(row=7, column=0, sticky="w", pady=3)
        self.browser_channel = tk.StringVar(value="chrome")
        self._browser_combo = ttk.Combobox(
            frame, textvariable=self.browser_channel,
            values=["chromium", "chrome", "msedge"], state="readonly", width=14,
        )
        self._browser_combo.grid(row=7, column=1, sticky="w", pady=3)
        ttk.Label(frame, text="Chrome and Edge use their installed browser", foreground="grey").grid(
            row=7, column=2, sticky="w", padx=(8, 0), pady=3
        )
        ttk.Label(frame, text="Sign-in timeout").grid(row=8, column=0, sticky="w", pady=3)
        self.editor_timeout = tk.StringVar(value="300")
        self._timeout_entry = ttk.Entry(frame, textvariable=self.editor_timeout, width=10)
        self._timeout_entry.grid(row=8, column=1, sticky="w", pady=3)
        ttk.Label(frame, text="seconds to sign in and open the editor", foreground="grey").grid(
            row=8, column=2, sticky="w", padx=(8, 0), pady=3
        )
        self._emit_hint_var = tk.StringVar()
        ttk.Label(
            frame, textvariable=self._emit_hint_var, foreground="grey",
            wraplength=340, justify="left",
        ).grid(row=9, column=0, columnspan=3, sticky="w", pady=(6, 0))

        self._update_emit_state()

    def _build_actions(self) -> None:
        bar = ttk.Frame(self)
        bar.grid(row=2, column=0, sticky="w", pady=(12, 0))

        self.generate_button = ttk.Button(
            bar, text="Generate preview", command=self._generate
        )
        self.generate_button.pack(side="left")
        self.save_button = ttk.Button(
            bar, text="Save output…", command=self._save, state="disabled"
        )
        self.save_button.pack(side="left", padx=(8, 0))
        self.stop_button = ttk.Button(
            bar, text="Stop replay", command=self._stop, state="disabled"
        )
        self.stop_button.pack(side="left", padx=(8, 0))
        self.overwrite_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            bar, text="Overwrite existing files",
            variable=self.overwrite_var,
        ).pack(side="left", padx=(12, 0))

    def _build_preview(self) -> None:
        frame = ttk.Frame(self._result_tabs, padding=8)
        self._result_tabs.add(frame, text="Preview")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)
        self._build_output_options(frame)

        self.output = tk.Text(
            frame, wrap="none", font=MONO, height=8, state="disabled"
        )
        self.output.grid(row=1, column=0, sticky="nsew")

        y_scroll = ttk.Scrollbar(frame, command=self.output.yview)
        y_scroll.grid(row=1, column=1, sticky="ns")
        x_scroll = ttk.Scrollbar(
            frame, orient="horizontal", command=self.output.xview
        )
        x_scroll.grid(row=2, column=0, sticky="ew")
        self.output.configure(
            yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set
        )

    def _build_status(self) -> None:
        frame = ttk.Frame(self._result_tabs, padding=8)
        self._result_tabs.add(frame, text="Log")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        self.log = tk.Text(
            frame, height=5, wrap="word", font=MONO, state="disabled"
        )
        self.log.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(frame, command=self.log.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=scroll.set)

        self.status = tk.StringVar(value="Ready.")
        ttk.Label(self, textvariable=self.status, foreground="grey").grid(
            row=4, column=0, sticky="w", pady=(8, 0)
        )

    # -- raw state -------------------------------------------------------------

    def _current_text(self) -> str:
        # Text.get appends a trailing newline the user never typed; drop it so
        # the generated record matches what is on screen.
        return self.text_input.get("1.0", "end-1c")

    def _raw_parameters(self) -> Dict[str, str]:
        raw = {"profile": self.profile.get()}
        for key, row in self.rows.items():
            raw[key] = row.variable.get()
        return raw

    def _raw_emit_options(self) -> Dict[str, Any]:
        return {
            "enabled": self.emit_enabled.get(),
            "target": self.emit_target.get(),
            "doc_id": self.doc_id.get(),
            "emit_speed": self.emit_speed.get(),
            "emit_max_gap_s": self.emit_max_gap.get(),
            "headless": self.headless.get(),
            "browser_profile": self.browser_profile.get(),
            "browser_channel": self.browser_channel.get(),
            "editor_timeout_s": self.editor_timeout.get(),
        }

    # -- emission controls -----------------------------------------------------

    def set_emit_support(self, support: Dict[str, Tuple[bool, str]]) -> None:
        """Replace the detected emission availability and refresh the controls.

        Exists for tests, so the enable/disable logic can be exercised against
        both states of an extra regardless of what is installed here.
        """
        self._support = support
        self._update_emit_state()

    def _set_state(self, widgets: List[tk.Widget], state: str) -> None:
        for widget in widgets:
            widget.configure(state=state)

    def _update_emit_state(self, _event: Optional[tk.Event] = None) -> None:
        enabled = self.emit_enabled.get()
        if hasattr(self, "generate_button"):
            self.generate_button.configure(text="Generate and type…" if enabled else "Generate preview")
        target = self.emit_target.get()
        available, install = self._support.get(target, (False, ""))

        self._emit_target_combo.configure(
            state="readonly" if enabled else "disabled"
        )
        # The docs-specific fields are meaningless for the desktop target and
        # vice versa there are none, so desktop simply leaves them off.
        live = enabled and available
        self._set_state(
            [self._speed_entry, self._gap_entry],
            "normal" if live else "disabled",
        )
        self._set_state(
            [
                self._doc_id_entry,
                self._headless_check,
                self._profile_entry,
                self._profile_browse,
                self._timeout_entry,
            ],
            "normal" if (live and target == "docs") else "disabled",
        )
        self._browser_combo.configure(
            state="readonly" if (live and target == "docs") else "disabled"
        )

        if enabled and not available:
            self._emit_hint_var.set(
                f"'{target}' emission is not installed. Enable it with: {install}"
            )
        elif enabled and target == "desktop":
            self._emit_hint_var.set(
                "Focus the destination editor during the five-second countdown. "
                "Press Esc to stop."
            )
        else:
            self._emit_hint_var.set("")

    # -- actions ---------------------------------------------------------------

    def _browse_profile(self) -> None:
        path = filedialog.askdirectory(title="Browser profile directory")
        if path:
            self.browser_profile.set(path)

    def _update_char_count(self) -> None:
        self.char_count.set(f"{len(self._current_text())} characters")

    def _text_modified(self, _event: tk.Event) -> None:
        if self.text_input.edit_modified():
            self._update_char_count()
            self.text_input.edit_modified(False)

    def _clear_text(self) -> None:
        self.text_input.delete("1.0", "end")
        self._update_char_count()

    def _load_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Load text from a file",
            filetypes=[("Text files", "*.txt *.md"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            content = load_text(f"@{path}")
        except (OSError, ValueError) as exc:
            messagebox.showerror("Could not read the file", str(exc))
            return
        self.text_input.delete("1.0", "end")
        self.text_input.insert("1.0", content)
        self._update_char_count()
        self.status.set(f"Loaded {len(content)} characters from {Path(path).name}")
        self._log(f"loaded @{path}")

    def _generate(self) -> None:
        if self._busy:
            return

        text = self._current_text()
        if not text:
            messagebox.showwarning("Nothing to generate", "Enter some text first.")
            return

        try:
            kwargs = collect_parameters(self._raw_parameters())
            emit_ns = collect_emit_options(self._raw_emit_options())
            if emit_ns is not None:
                available, install = self._support.get(
                    emit_ns.emit, (False, "")
                )
                if not available:
                    raise ValueError(
                        f"'{emit_ns.emit}' emission is not installed. "
                        f"Enable it with: {install}"
                    )
        except ValueError as exc:
            messagebox.showerror("Check the parameters", str(exc))
            self._log(f"error: {exc}")
            return

        self._busy = True
        self._stop_requested.clear()
        self._setup_combo.configure(state="disabled")
        self._sample_button.configure(state="disabled")
        self.generate_button.configure(state="disabled")
        self.status.set(f"Generating {len(text)} characters…")
        self._log(
            f"generating {len(text)} characters "
            f"(profile={kwargs['profile']}, seed={kwargs.get('seed')})…"
        )

        # A long document takes a noticeable moment, and a frozen window looks
        # like a crash. The work happens off the UI thread and reports back
        # through the queue.
        self._worker_thread = threading.Thread(
            target=self._worker, args=(text, kwargs, emit_ns), daemon=True
        )
        self._worker_thread.start()

    def _stop(self) -> None:
        self._stop_requested.set()
        self.stop_button.configure(state="disabled")
        self.status.set("Stopping replay…")

    def close(self) -> None:
        """Let replay release held keys and close its browser before exiting."""
        self._stop_requested.set()
        root = self.winfo_toplevel()
        if self._worker_thread is not None and self._worker_thread.is_alive():
            root.withdraw()
            self.after(POLL_MS, self.close)
        else:
            root.destroy()

    def _worker(
        self,
        text: str,
        kwargs: Dict[str, Any],
        emit_ns: Optional[argparse.Namespace],
    ) -> None:
        try:
            record = generate_full_output(text=text, **kwargs)
        except Exception as exc:  # surfaced in the dialog, not swallowed
            self._results.put(("error", exc, None))
            return
        self._results.put(("record", record, emit_ns is not None))
        if emit_ns is None:
            self._results.put(("done", None, None))
            return
        try:
            self._results.put(("status", "Preparing live replay…", None))
            emission = emit_record(
                record, emit_ns, should_abort=self._stop_requested.is_set,
                on_status=lambda message: self._results.put(("status", message, None)),
            )
        except Exception as exc:
            self._results.put(("emission_error", exc, None))
        else:
            self._results.put(("done", None, emission))

    def _drain(self) -> None:
        try:
            while True:
                status, payload, emission = self._results.get_nowait()
                if status == "status":
                    self.status.set(str(payload))
                    self._log(str(payload))
                    continue
                if status == "record":
                    self._record = payload
                    self._refresh_view()
                    self.save_button.configure(state="disabled" if emission else "normal")
                    self.stop_button.configure(state="normal" if emission else "disabled")
                    line = summary_line(payload)
                    self.status.set(line)
                    self._log(line)
                    continue
                self._busy = False
                self._setup_combo.configure(state="readonly")
                self._sample_button.configure(state="normal")
                self.generate_button.configure(state="normal")
                self.stop_button.configure(state="disabled")
                self.save_button.configure(state="normal" if self._record is not None else "disabled")
                if status in ("error", "emission_error"):
                    title = "Replay failed" if status == "emission_error" else "Generation failed"
                    detail = " The generated record is still available to save." if status == "emission_error" else ""
                    self.status.set(title + "." + detail)
                    self._log(f"error: {type(payload).__name__}: {payload}")
                    messagebox.showerror(
                        title, f"{type(payload).__name__}: {payload}{detail}",
                    )
                    continue
                if emission is not None:
                    self.status.set("Replay stopped. Record available to save." if emission.get("aborted") else "Replay complete. Record available to save.")
                    self._log(f"emission: {emission}")
        except queue.Empty:
            pass
        self._drain_job = self.after(POLL_MS, self._drain)

    def _refresh_view(self) -> None:
        if self._record is None:
            return
        rendered = render_output(self._record, self.format.get())
        self.output.configure(state="normal")
        self.output.delete("1.0", "end")
        self.output.insert("1.0", rendered.rstrip("\n"))
        self.output.configure(state="disabled")

    def _save(self) -> None:
        if self._record is None:
            messagebox.showinfo("Nothing to save", "Generate a record first.")
            return

        fmt = self.format.get()
        extension = FORMAT_EXTENSIONS[fmt]
        path = filedialog.asksaveasfilename(
            title="Save output",
            defaultextension=extension,
            initialfile=f"typetrace-record{extension}",
            initialdir=str(get_default_output_dir()),
            filetypes=[(f"{fmt} output", f"*{extension}"), ("All files", "*.*")],
            confirmoverwrite=False,
        )
        if not path:
            return
        out_path = Path(path)
        # The overwrite guard from the CLI: refuse to clobber silently; the
        # checkbox plays the role of --force.
        overwrite = self.overwrite_var.get()
        if out_path.exists() and not overwrite:
            if not messagebox.askyesno(
                "File exists", f"{out_path.name} already exists. Overwrite it?"
            ):
                return
            overwrite = True
        try:
            write_output(out_path, render_output(self._record, fmt), overwrite=overwrite)
        except OSError as exc:
            messagebox.showerror("Could not save", str(exc))
            return
        self.status.set(f"Saved to {out_path.name}")
        self._log(f"wrote {out_path}")

    def _log(self, line: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", line + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")


def main() -> int:
    root = tk.Tk()
    root.title(WINDOW_TITLE)
    root.minsize(760, 620)
    width = min(1000, root.winfo_screenwidth() - 80)
    height = min(780, root.winfo_screenheight() - 100)
    root.geometry(f"{width}x{height}")
    app = Application(root)
    root.protocol("WM_DELETE_WINDOW", app.close)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
