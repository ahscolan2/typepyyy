"""Behavior checks for the GUI writing setups."""

import pytest

import gui
import main
import presets
from emit_common import iter_timeline


@pytest.mark.parametrize("preset", presets.list_presets(), ids=lambda p: p.slug)
def test_setup_generates_its_sample_and_valid_emission_options(preset):
    record = main.generate_full_output(preset.sample_text, **gui.collect_parameters(preset.to_gui_parameters()))
    text = []
    for key in record["keystrokes"]:
        if key["kind"] == "backspace":
            text.pop()
        else:
            text.append(key["char"])
    assert "".join(text) == preset.sample_text
    options = gui.collect_emit_options(preset.to_gui_emit_options())
    assert (options is not None) == preset.emit_enabled
    if options:
        assert options.emit == "desktop"
        assert len(list(iter_timeline(record, speed=options.emit_speed, max_gap_s=options.emit_max_gap_s))) == 2 * len(record["keystrokes"])


def test_confirmed_paragraph_uses_the_successful_live_record_settings():
    preset = presets.get_preset("confirmed")
    record = main.generate_full_output(preset.sample_text, **gui.collect_parameters(preset.to_gui_parameters()))
    assert len(record["keystrokes"]) == 1021
    assert sum(k["kind"] == "backspace" for k in record["keystrokes"]) == 133
    assert record["target_text"] == "\n\n" + presets.PARAGRAPH
    assert preset.emit_speed == 2.5
    assert preset.emit_max_gap_s is None


def test_slow_profile_preserves_timing_but_takes_twice_as_long():
    quick = presets.get_preset("confirmed")
    slow = presets.get_preset("slower")
    record = main.generate_full_output(presets.PARAGRAPH, **gui.collect_parameters(quick.to_gui_parameters()))
    fast_events = list(iter_timeline(record, speed=quick.emit_speed))
    slow_events = list(iter_timeline(record, speed=slow.emit_speed))
    assert slow_events[-1][0] == pytest.approx(2 * fast_events[-1][0])


def test_breaks_setup_adds_real_gaps_without_changing_text():
    preset = presets.get_preset("breaks")
    record = main.generate_full_output(preset.sample_text, **gui.collect_parameters(preset.to_gui_parameters()))
    assert record["statistics"]["session_gaps"] >= 2
    events = list(iter_timeline(record, speed=preset.emit_speed, max_gap_s=preset.emit_max_gap_s))
    assert any(b[0] - a[0] >= 19.9 for a, b in zip(events, events[1:]))


def test_storage_paths_follow_user_data_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert presets.get_default_browser_profile_dir() == tmp_path / "TypeTrace" / "browser_profile"
    assert presets.get_default_output_dir() == tmp_path / "TypeTrace" / "output"
    assert presets.get_default_output_dir().is_dir()
