"""Tests for main - the command line interface.

Two things matter here beyond the happy path. The first is that failures come
back as an exit code rather than a traceback, because the tool is meant to be
scriptable. The second is that the CLI has no browser-driving options: --mode
and --doc-id belonged to a Chrome DevTools path that is no longer wired to
anything, and a test that asserts they are rejected is what stops them
creeping back.
"""

import json

import pytest

import main
from main import WATERMARK, build_parser, generate_full_output, load_text

UNICODE_TEXT = (
    "Résumé: 日本語のテキスト, привет, \U0001f642 — and a tab\there.\n"
    "Second line with «typographic» punctuation."
)


@pytest.fixture
def essay(tmp_path):
    path = tmp_path / "essay.txt"
    path.write_text(UNICODE_TEXT, encoding="utf-8")
    return path


# --- load_text ---------------------------------------------------------------


@pytest.mark.parametrize(
    "literal",
    ["hello world", "", "  spaced  ", "日本語", "a@b.com", "line\nbreak"],
)
def test_load_text_returns_a_literal_unchanged(literal):
    assert load_text(literal) == literal


def test_load_text_reads_an_at_path_as_utf8(essay):
    assert load_text(f"@{essay}") == UNICODE_TEXT


def test_load_text_raises_for_a_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError, match="text file not found"):
        load_text(f"@{tmp_path / 'nope.txt'}")


def test_load_text_raises_for_a_file_that_is_not_utf8(tmp_path):
    path = tmp_path / "latin1.txt"
    path.write_bytes("café".encode("latin-1"))
    with pytest.raises(ValueError, match="not valid UTF-8"):
        load_text(f"@{path}")


def test_backslash_at_escapes_a_literal_leading_at():
    assert load_text("\\@handle mentioned this") == "@handle mentioned this"
    assert load_text("\\@") == "@"


def test_the_escape_only_applies_to_a_leading_at():
    assert load_text("mail \\@ home") == "mail \\@ home"


# --- parser ------------------------------------------------------------------


def test_text_is_required():
    with pytest.raises(SystemExit):
        build_parser().parse_args([])


@pytest.mark.parametrize("profile", ["slow", "average", "fast"])
def test_profiles_are_accepted(profile):
    assert build_parser().parse_args(["-t", "x", "-p", profile]).profile == profile


def test_an_unknown_profile_is_rejected():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["-t", "x", "-p", "turbo"])


@pytest.mark.parametrize(
    "argv",
    [
        ["--text", "x", "--mode", "dry-run"],
        ["--text", "x", "--mode", "live"],
        ["--text", "x", "--doc-id", "abc123"],
        ["--text", "x", "--mode", "live", "--doc-id", "abc123"],
    ],
)
def test_the_browser_driving_options_are_gone(argv):
    # cdp_emitter.py is still in the repository but is not wired to anything:
    # nothing imports it, and the CLI has no path into it. This generator
    # writes data files and drives nothing.
    with pytest.raises(SystemExit):
        build_parser().parse_args(argv)


def test_no_option_mentions_a_browser_or_a_document_id():
    help_text = build_parser().format_help().lower()
    for word in ("--mode", "--doc-id", "playwright", "chrome", "devtools"):
        assert word not in help_text


def test_defaults_match_the_library_defaults():
    import macro_scripter as ms

    args = build_parser().parse_args(["-t", "x"])
    assert args.profile == "average"
    assert args.seed is None
    assert args.typo_rate == ms.TYPO_RATE
    assert args.r_burst_probability == ms.R_BURST_PROBABILITY
    assert args.session_chars is None
    assert args.target_autocorrelation is None
    assert args.force is False
    assert args.verbose is False


# --- generate_full_output ----------------------------------------------------


def test_generate_full_output_carries_the_watermark():
    record = generate_full_output("Hello there.", seed=1)
    for key, value in WATERMARK.items():
        assert record[key] == value


def test_generate_full_output_keeps_the_pipeline_record():
    record = generate_full_output("Hello there.", seed=1)
    for key in ("metadata", "statistics", "macro_script", "keystrokes",
                "intervals", "target_text"):
        assert key in record


def test_the_watermark_never_overwrites_record_data():
    # WATERMARK is merged in front of the record, so a future watermark key
    # that collided with a record key would silently win. It must not.
    record = generate_full_output("Hello there.", seed=1)
    assert set(WATERMARK) & set(record["metadata"]) == set()


# --- main() ------------------------------------------------------------------


def test_main_returns_zero_and_writes_json_to_stdout(capsys):
    assert main.main(["--text", "Hello there.", "--seed", "1"]) == 0
    record = json.loads(capsys.readouterr().out)
    assert record["target_text"] == "Hello there."
    assert record["generated_by"] == WATERMARK["generated_by"]


def test_main_returns_one_for_a_missing_input_file(tmp_path, capsys):
    assert main.main(["--text", f"@{tmp_path / 'nope.txt'}"]) == 1
    assert "text file not found" in capsys.readouterr().err


def test_main_returns_one_for_empty_input(capsys):
    assert main.main(["--text", ""]) == 1
    assert "empty" in capsys.readouterr().err


def test_main_returns_one_when_the_output_exists_without_force(tmp_path, capsys):
    out = tmp_path / "record.json"
    out.write_text("keep me", encoding="utf-8")

    assert main.main(["--text", "Hello.", "--output", str(out)]) == 1
    assert "--force" in capsys.readouterr().err
    # The existing file is untouched, which is the point of refusing.
    assert out.read_text(encoding="utf-8") == "keep me"


def test_force_overwrites_an_existing_output(tmp_path):
    out = tmp_path / "record.json"
    out.write_text("stale", encoding="utf-8")

    assert main.main(
        ["--text", "Hello.", "--output", str(out), "--force", "--seed", "1"]
    ) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["target_text"] == "Hello."


def test_main_creates_missing_output_directories(tmp_path):
    out = tmp_path / "nested" / "deeper" / "record.json"
    assert main.main(["--text", "Hello.", "--output", str(out), "--seed", "1"]) == 0
    assert out.exists()


def test_main_returns_one_for_an_invalid_parameter(capsys):
    assert main.main(["--text", "Hello.", "--typo-rate", "5"]) == 1
    assert "typo_rate" in capsys.readouterr().err


def test_main_returns_one_for_an_unreachable_autocorrelation(capsys):
    assert main.main(["--text", "Hello.", "--target-autocorrelation", "0.95"]) == 1
    assert "target_autocorrelation" in capsys.readouterr().err


def test_main_returns_130_on_interrupt(monkeypatch):
    def interrupt(**_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(main, "generate_full_output", interrupt)
    assert main.main(["--text", "Hello."]) == 130


def test_verbose_writes_a_summary_to_stderr_and_json_to_stdout(capsys):
    assert main.main(["--text", "Hello there.", "--seed", "1", "-v"]) == 0
    captured = capsys.readouterr()
    json.loads(captured.out)
    assert "keystrokes" in captured.err
    assert "WPM active" in captured.err


# --- round trip --------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Plain ASCII text.",
        UNICODE_TEXT,
        "日本語だけのテキストです。",
        "Emoji \U0001f642\U0001f389 and a family \U0001f469‍\U0001f467.",
        "Tabs\tand\r\nCRLF line endings.",
        "   leading and trailing whitespace   ",
    ],
)
def test_output_file_round_trips_the_target_text(tmp_path, text):
    out = tmp_path / "record.json"
    assert main.main(
        ["--text", text, "--output", str(out), "--seed", "3"]
    ) == 0

    record = json.loads(out.read_text(encoding="utf-8"))
    assert record["target_text"] == text
    for key, value in WATERMARK.items():
        assert record[key] == value


def test_output_file_round_trips_a_file_input(tmp_path, essay):
    out = tmp_path / "record.json"
    assert main.main(
        ["--text", f"@{essay}", "--output", str(out), "--seed", "3"]
    ) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["target_text"] == UNICODE_TEXT


def test_the_same_seed_produces_the_same_file(tmp_path):
    first, second = tmp_path / "a.json", tmp_path / "b.json"
    for path in (first, second):
        assert main.main(
            ["--text", UNICODE_TEXT, "--output", str(path), "--seed", "11"]
        ) == 0
    assert first.read_bytes() == second.read_bytes()


def test_a_different_seed_produces_a_different_file(tmp_path):
    first, second = tmp_path / "a.json", tmp_path / "b.json"
    for path, seed in ((first, "11"), (second, "12")):
        assert main.main(
            ["--text", UNICODE_TEXT, "--output", str(path), "--seed", seed]
        ) == 0
    assert first.read_bytes() != second.read_bytes()


def test_output_is_written_as_utf8_not_escapes(tmp_path):
    out = tmp_path / "record.json"
    main.main(["--text", "日本語", "--output", str(out), "--seed", "1"])
    assert "日本語" in out.read_text(encoding="utf-8")
