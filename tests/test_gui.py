"""Tests for gui - the tkinter desktop application.

The widgets are thin shells around collect_parameters / collect_emit_options
and main's own generator functions, so the rules - defaults matching the CLI,
range validation, format mapping, the optional-emitter wiring - are tested
against those functions directly, without a window. The window itself is
built only where display-dependent behaviour matters (dialogs, the
enable/disable state of the emission controls), and every such test skips
cleanly when tkinter or a display is unavailable.
"""

import time

import pytest

tk = pytest.importorskip("tkinter")

import main  # noqa: E402
import gui  # noqa: E402

TEXT = "Academic integrity matters.\nSecond line."

CLI_EMIT_KEYS = (
    "emit", "doc_id", "emit_speed", "emit_max_gap_s", "headless",
    "browser_profile",
)


@pytest.fixture
def dialogs(monkeypatch):
    """Capture messagebox calls instead of popping a (blocking) dialog."""
    calls = []
    monkeypatch.setattr(
        gui.messagebox, "showerror",
        lambda *a, **k: calls.append(("showerror", a, k)),
    )
    monkeypatch.setattr(
        gui.messagebox, "showwarning",
        lambda *a, **k: calls.append(("showwarning", a, k)),
    )
    monkeypatch.setattr(
        gui.messagebox, "showinfo",
        lambda *a, **k: calls.append(("showinfo", a, k)),
    )
    monkeypatch.setattr(
        gui.messagebox, "askyesno",
        lambda *a, **k: calls.append(("askyesno", a, k)) or True,
    )
    return calls


@pytest.fixture(scope="module")
def root():
    """One Tk interpreter for all window tests.

    Tk interpreters are cheap but not free - spinning up one per test on
    Windows intermittently fails with a TclError that looks like a missing
    display - so they all share this one.
    """
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display available")
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture
def app(root, dialogs):
    """A real Application in the shared withdrawn window."""
    application = gui.Application(root)
    yield application
    application.destroy()


def pump(app, timeout=15.0):
    """Run the event loop until the worker's record has been drained."""
    root = app.winfo_toplevel()
    deadline = time.monotonic() + timeout
    while app._record is None and time.monotonic() < deadline:
        root.update()
        time.sleep(0.01)
    return app._record


# --- parameter collection ----------------------------------------------------


def test_defaults_match_the_cli_parser():
    args = main.build_parser().parse_args(["-t", "x"])
    raw = gui.default_parameter_strings()
    assert raw["profile"] == args.profile
    kwargs = gui.collect_parameters(raw)
    # Every default the window ships, interpreted as the CLI would.
    assert kwargs == {
        "profile": args.profile,
        "seed": args.seed,
        "typo_rate": args.typo_rate,
        "r_burst_probability": args.r_burst_probability,
        "structural_revision_rate": args.structural_revision_rate,
        "session_chars": args.session_chars,
        "target_autocorrelation": args.target_autocorrelation,
        "fatigue_rate": args.fatigue_rate,
        "warmup_strength": args.warmup_strength,
        "familiarity_boost": args.familiarity_boost,
    }


def test_collect_parameters_parses_every_field():
    raw = gui.default_parameter_strings()
    raw.update({
        "profile": "fast",
        "seed": "42",
        "typo_rate": "0.05",
        "r_burst_probability": "0.4",
        "structural_revision_rate": "0.0",
        "session_chars": "500",
        "target_autocorrelation": "0.2",
        "fatigue_rate": "0.1",
        "warmup_strength": "0.0",
        "familiarity_boost": "0.15",
    })
    assert gui.collect_parameters(raw) == {
        "profile": "fast",
        "seed": 42,
        "typo_rate": 0.05,
        "r_burst_probability": 0.4,
        "structural_revision_rate": 0.0,
        "session_chars": 500,
        "target_autocorrelation": 0.2,
        "fatigue_rate": 0.1,
        "warmup_strength": 0.0,
        "familiarity_boost": 0.15,
    }


@pytest.mark.parametrize(
    "key, bad, fragment",
    [
        ("seed", "four", "Seed"),
        ("typo_rate", "1.5", "Typo rate"),
        ("typo_rate", "-0.1", "Typo rate"),
        ("typo_rate", "often", "Typo rate"),
        ("r_burst_probability", "2.0", "Revision probability"),
        ("r_burst_probability", "-1", "Revision probability"),
        ("structural_revision_rate", "1.5", "Structural revision rate"),
        ("structural_revision_rate", "-0.1", "Structural revision rate"),
        ("session_chars", "0", "Session length"),
        ("session_chars", "-100", "Session length"),
        ("session_chars", "1.5", "Session length"),
        # The timing engine requires [0, phi) with phi = 0.9, so 0.9 itself
        # is out - the dialog must catch this before generation does.
        ("target_autocorrelation", "0.9", "Rhythm autocorrelation"),
        ("target_autocorrelation", "-0.5", "Rhythm autocorrelation"),
        ("fatigue_rate", "-0.01", "Fatigue rate"),
        ("fatigue_rate", "fast", "Fatigue rate"),
        ("warmup_strength", "1.0", "Warmup strength"),
        ("warmup_strength", "-0.5", "Warmup strength"),
        ("familiarity_boost", "1.0", "Familiarity boost"),
        ("familiarity_boost", "-0.2", "Familiarity boost"),
    ],
)
def test_collect_parameters_rejects_out_of_range_values(key, bad, fragment):
    raw = gui.default_parameter_strings()
    raw[key] = bad
    with pytest.raises(ValueError, match=fragment):
        gui.collect_parameters(raw)


# --- format mapping ------------------------------------------------------------


@pytest.fixture
def record():
    return main.generate_full_output(TEXT, seed=1)


def test_gui_default_format_matches_the_cli_default():
    args = main.build_parser().parse_args(["-t", "x"])
    assert gui.DEFAULT_FORMAT == args.format


@pytest.mark.parametrize("fmt", ["json", "replay", "replay-full"])
def test_render_output_matches_the_cli_rendering(record, fmt):
    assert gui.render_output(record, fmt) == main.render_record(record, fmt)


def test_every_format_choice_matches_a_cli_format():
    parser = main.build_parser()
    format_action = next(a for a in parser._actions if a.dest == "format")
    for value, _label in gui.OUTPUT_FORMATS:
        assert value in format_action.choices
        assert value in gui.FORMAT_EXTENSIONS


def test_summary_line_matches_the_cli_verbose_format(record):
    stats = record["statistics"]
    expected = (
        f"{record['metadata']['input_chars']} chars, "
        f"{stats['keystrokes']} keystrokes, "
        f"{stats['backspaces']} backspaces, "
        f"{stats['session_gaps']} session gaps, "
        f"{stats['wpm_active']:.1f} WPM active, "
        f"lag-1 autocorrelation {stats['lag1_autocorrelation']:.3f}"
    )
    assert gui.summary_line(record) == expected


# --- determinism wiring --------------------------------------------------------


def test_gui_code_path_reproduces_the_cli_record():
    raw = gui.default_parameter_strings()
    raw["seed"] = "42"
    kwargs = gui.collect_parameters(raw)
    via_gui = main.generate_full_output(TEXT, **kwargs)
    via_cli = main.generate_full_output(
        TEXT,
        profile="average",
        seed=42,
        session_chars=None,
        target_autocorrelation=None,
    )
    assert via_gui == via_cli


def test_the_same_seed_gives_the_same_record_twice():
    kwargs = gui.collect_parameters({**gui.default_parameter_strings(), "seed": "7"})
    assert main.generate_full_output(TEXT, **kwargs) == main.generate_full_output(
        TEXT, **kwargs
    )


# --- emission options ----------------------------------------------------------


def test_emission_off_by_default_like_the_cli():
    assert gui.collect_emit_options(gui.default_emit_options()) is None


def test_emit_namespace_matches_the_cli_parser_shape():
    raw = gui.default_emit_options()
    raw.update({
        "enabled": True,
        "target": "docs",
        "doc_id": "abc123",
        "emit_speed": "2.0",
        "emit_max_gap_s": "0.5",
        "headless": True,
        "browser_profile": "some-profile",
    })
    ns = gui.collect_emit_options(raw)
    cli = main.build_parser().parse_args([
        "-t", "x", "--emit", "docs", "--doc-id", "abc123",
        "--emit-speed", "2.0", "--emit-max-gap-s", "0.5", "--headless",
        "--browser-profile", "some-profile",
    ])
    assert vars(ns) == {key: getattr(cli, key) for key in CLI_EMIT_KEYS}


def test_emit_desktop_needs_no_doc_id():
    raw = gui.default_emit_options()
    raw.update({"enabled": True, "target": "desktop"})
    ns = gui.collect_emit_options(raw)
    assert ns.emit == "desktop"
    assert ns.doc_id is None


@pytest.mark.parametrize(
    "patch, fragment",
    [
        ({"target": "docs", "doc_id": "   "}, "Doc ID"),
        ({"emit_speed": "0"}, "Emission speed"),
        ({"emit_speed": "-2"}, "Emission speed"),
        ({"emit_speed": "quick"}, "Emission speed"),
        ({"emit_max_gap_s": "-0.1"}, "Silence cap"),
        ({"emit_max_gap_s": "soon"}, "Silence cap"),
    ],
)
def test_collect_emit_options_rejects_bad_values(patch, fragment):
    raw = gui.default_emit_options()
    raw.update({"enabled": True, "target": "docs", "doc_id": "abc123"})
    raw.update(patch)
    with pytest.raises(ValueError, match=fragment):
        gui.collect_emit_options(raw)


def test_browser_profile_default_matches_the_cli():
    args = main.build_parser().parse_args(["-t", "x"])
    assert gui.DEFAULT_BROWSER_PROFILE == args.browser_profile


# --- emitter availability ------------------------------------------------------


def _fake_find_spec(available):
    return lambda module: object() if module in available else None


def test_emit_support_reports_installed_extras(monkeypatch):
    monkeypatch.setattr(
        gui.importlib.util, "find_spec", _fake_find_spec({"playwright", "pynput"})
    )
    support = gui.emit_support()
    assert support == {"docs": (True, ""), "desktop": (True, "")}


def test_emit_support_points_at_the_pip_command_when_missing(monkeypatch):
    monkeypatch.setattr(gui.importlib.util, "find_spec", _fake_find_spec(set()))
    support = gui.emit_support()
    for target in ("docs", "desktop"):
        available, install = support[target]
        assert not available
        assert "pip install" in install


# --- the window itself ---------------------------------------------------------


def test_window_builds_with_cli_defaults(app):
    assert app.profile.get() == "average"
    assert app.format.get() == gui.DEFAULT_FORMAT
    assert app._record is None
    # Emission is off and its controls start disabled, like the CLI.
    assert app.emit_enabled.get() is False
    assert str(app._doc_id_entry.cget("state")) == "disabled"
    assert str(app._speed_entry.cget("state")) == "disabled"


def test_a_bad_parameter_shows_a_dialog_and_does_not_start_work(app, dialogs):
    app.rows["typo_rate"].variable.set("9")
    app._generate()
    assert [kind for kind, *_ in dialogs] == ["showerror"]
    assert "Typo rate" in dialogs[0][1][1]
    assert app._busy is False
    assert app._record is None


def test_emitting_without_a_doc_id_shows_a_dialog(app, dialogs):
    app.set_emit_support({"docs": (True, ""), "desktop": (True, "")})
    app.emit_enabled.set(True)
    app._generate()
    assert [kind for kind, *_ in dialogs] == ["showerror"]
    assert "Doc ID" in dialogs[0][1][1]
    assert app._busy is False


def test_emit_controls_follow_availability(app):
    app.set_emit_support({
        "docs": (True, ""),
        "desktop": (False, "pip install '.[desktop]'"),
    })
    app.emit_enabled.set(True)

    app.emit_target.set("docs")
    app._update_emit_state()
    assert str(app._doc_id_entry.cget("state")) == "normal"
    assert str(app._speed_entry.cget("state")) == "normal"

    # Picking an uninstalled target disables its controls and says why.
    app.emit_target.set("desktop")
    app._update_emit_state()
    assert str(app._doc_id_entry.cget("state")) == "disabled"
    assert str(app._speed_entry.cget("state")) == "disabled"
    assert "pip install" in app._emit_hint_var.get()


def test_emit_controls_sleep_while_the_checkbox_is_off(app):
    app.set_emit_support({"docs": (True, ""), "desktop": (True, "")})
    app.emit_enabled.set(False)
    app._update_emit_state()
    for widget in (
        app._emit_target_combo,
        app._doc_id_entry,
        app._speed_entry,
        app._gap_entry,
        app._headless_check,
        app._profile_entry,
        app._profile_browse,
    ):
        assert str(widget.cget("state")) == "disabled"


def test_desktop_target_disables_the_docs_only_fields(app):
    app.set_emit_support({"docs": (True, ""), "desktop": (True, "")})
    app.emit_enabled.set(True)
    app.emit_target.set("desktop")
    app._update_emit_state()
    assert str(app._speed_entry.cget("state")) == "normal"
    assert str(app._gap_entry.cget("state")) == "normal"
    for widget in (app._doc_id_entry, app._headless_check, app._profile_entry):
        assert str(widget.cget("state")) == "disabled"


def test_generation_through_the_window_matches_the_cli(app, dialogs):
    app.rows["seed"].variable.set("11")
    app._generate()
    got = pump(app)
    if got is None:  # TEMP DEBUG
        print("\nDEBUG dialogs:", dialogs)
        print("DEBUG busy:", app._busy, "qsize:", app._results.qsize())
        import sys as _s
        _s.stderr.write("DEBUG params=%r\n" % (app._raw_parameters(),))
    assert got is not None
    assert dialogs == []
    assert app._record == main.generate_full_output(gui.DEFAULT_TEXT, seed=11)
    assert "keystrokes" in app.status.get()
    assert gui.summary_line(app._record) in app.log.get("1.0", "end")
    assert app._busy is False
    # The preview shows the selected format and Save becomes available.
    assert app._record["target_text"] == gui.DEFAULT_TEXT
    assert len(app.output.get("1.0", "end").strip()) > 0
    assert str(app.save_button.cget("state")) == "normal"
