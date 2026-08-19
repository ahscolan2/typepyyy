"""Tests for replay - the human-readable rendering of a record.

The JSON record is the artifact a detector trains on; this is the artifact a
person reads to decide whether the generated process looks like someone
writing. That makes two things testable properties rather than cosmetics.

The first is that the rendering is *lossless about the outcome*: whatever the
replay says happened, the text printed at the bottom is the record's
`target_text`, unchanged.

The second is that it stays a table. The document column is fixed-width, so
every character in it has to occupy one column and none of them may end the
line - a raw U+000B in the document would split the row in two and silently
destroy the alignment of everything after it. `conftest` already carries such a
text, because the generator is expected to handle them.

Records are built through `main.generate_full_output` with fixed seeds, so a
failure here reproduces from the seed alone.
"""

import re
from functools import lru_cache
from itertools import groupby

import pytest

import main
import replay
from replay import (
    CURSOR,
    ELLIPSIS,
    display_char,
    format_duration,
    format_timestamp,
    render,
    visible_tail,
)

from conftest import EDGE_CASES

# Small enough that a few hundred characters are split over several sessions.
SHORT_SESSION = 60

# A pause threshold no generated pause can reach, so every ordinary keystroke
# is classified as plain typing and runs fold predictably.
NO_PAUSES_MS = 1e12

WIDTH = 56

# Where each field of an event row starts: two leading spaces, the timestamp
# right-justified in twelve, two more spaces, then the document column.
DOCUMENT_START = 16

PROSE_FOR_REPLAY = (
    "Academic integrity depends on evidence that a piece of writing was "
    "actually composed by the person who submitted it, and the process "
    "leaves traces a finished document does not. "
)


def make_record(text=PROSE_FOR_REPLAY, seed=20240617, **kwargs):
    """One watermarked record, as the CLI would produce it."""
    return main.generate_full_output(text, seed=seed, **kwargs)


@lru_cache(maxsize=None)
def edge_case_record(name, seed=6):
    """A record for one named edge case, generated once for the whole module.

    The column tests run every edge case at four widths and both verbosities;
    regenerating a 500-character record for each combination is the slowest
    thing in this file and buys nothing, because rendering does not mutate the
    record it is given.
    """
    return make_record(EDGE_CASES[name], seed=seed)


@pytest.fixture
def record():
    return make_record(PROSE_FOR_REPLAY * 3)


@pytest.fixture
def typo_record():
    """A record where every few characters are mistyped and corrected."""
    return make_record(PROSE_FOR_REPLAY * 2, seed=11, typo_rate=0.2)


@pytest.fixture
def revision_record():
    """Every burst ends in a revision, so deletes and retypes are guaranteed."""
    return make_record(PROSE_FOR_REPLAY * 2, seed=12, r_burst_probability=1.0)


@pytest.fixture
def gappy_record():
    """A record that spans several writing sessions."""
    return make_record(PROSE_FOR_REPLAY * 2, seed=13, session_chars=SHORT_SESSION)


def event_rows(rendered):
    """The rows of the timeline table, excluding the session-gap notices.

    A session gap prints its own short line with no document column, so the
    column tests would have nothing to measure on it.
    """
    rows = []
    for line in rendered.splitlines():
        match = re.match(r"^ {2}(.{12})  (.*)$", line)
        if match and re.fullmatch(r"\s*[\d:.d ]+", match.group(1)):
            if match.group(2).startswith("STOP - "):
                continue
            rows.append(line)
    return rows


def descriptions(rendered, width=WIDTH):
    """The EVENT column of every event row."""
    return [
        row[DOCUMENT_START + width + 2:].strip() for row in event_rows(rendered)
    ]


def role_runs(record, role):
    """Lengths of each maximal run of consecutive keystrokes with `role`."""
    matches = (event["role"] == role for event in record["keystrokes"])
    return [len(list(group)) for is_match, group in groupby(matches) if is_match]


# --- format_timestamp --------------------------------------------------------


@pytest.mark.parametrize(
    "ms,expected",
    [
        (0, "00:00.000"),
        (1, "00:00.001"),
        (999, "00:00.999"),
        (1000, "00:01.000"),
        (1500, "00:01.500"),
        (59_999, "00:59.999"),
        (60_000, "01:00.000"),
        (61_500, "01:01.500"),
        (599_999, "09:59.999"),
        (3_599_999, "59:59.999"),
    ],
)
def test_below_an_hour_the_timestamp_is_mm_ss_mmm(ms, expected):
    assert format_timestamp(ms) == expected


@pytest.mark.parametrize(
    "ms,expected",
    [
        (3_600_000, "1:00:00.000"),
        (3_661_001, "1:01:01.001"),
        (7_200_000, "2:00:00.000"),
        (86_399_999, "23:59:59.999"),
    ],
)
def test_past_an_hour_the_timestamp_widens_rather_than_rolling_over(ms, expected):
    assert format_timestamp(ms) == expected


@pytest.mark.parametrize(
    "ms,expected",
    [
        (86_400_000, "1d 00:00:00"),
        (86_400_000 + 3_661_000, "1d 01:01:01"),
        (172_800_000, "2d 00:00:00"),
        (864_000_000_000, "10000d 00:00:00"),
    ],
)
def test_past_a_day_the_timestamp_counts_days(ms, expected):
    assert format_timestamp(ms) == expected


def test_the_day_format_drops_the_milliseconds():
    # Four days into a document, a millisecond is noise. The hour format keeps
    # them because a within-session timestamp is read against keystrokes.
    assert "." not in format_timestamp(90_000_000)
    assert "." in format_timestamp(3_600_000)


def test_milliseconds_rounding_up_carries_into_the_seconds():
    # 999.5 ms rounds to 1000 ms, which is not a printable millisecond field.
    # Without the carry this reads "00:00.1000" and the column stops lining up.
    assert format_timestamp(999.5) == "00:01.000"
    assert format_timestamp(999.4) == "00:00.999"
    assert format_timestamp(1_999.5) == "00:02.000"


@pytest.mark.parametrize("half_ms", [x * 1000 + 999.5 for x in range(0, 120)])
def test_the_millisecond_field_is_never_four_digits(half_ms):
    # The carry has to hold at every half-millisecond boundary, not just the
    # first one, or one row in a long timeline is a character wider than the
    # rest.
    milliseconds = format_timestamp(half_ms).rsplit(".", 1)[-1]
    assert len(milliseconds) == 3
    assert int(milliseconds) < 1000


def test_the_sub_hour_timestamp_is_fixed_width():
    widths = {len(format_timestamp(ms)) for ms in (0, 999, 60_000, 3_599_999)}
    assert widths == {len("00:00.000")}


def test_every_timestamp_fits_the_time_column():
    # The column is twelve wide; a document that ran for years would not fit,
    # but anything the generator can produce must.
    for ms in (0, 1000, 3_600_000, 86_400_000, 30 * 86_400_000):
        assert len(format_timestamp(ms)) <= 12


# --- format_duration ---------------------------------------------------------


@pytest.mark.parametrize(
    "ms,expected",
    [
        (0, "0 ms"),
        (1, "1 ms"),
        (95.98, "96 ms"),
        (999, "999 ms"),
        (1000, "1.0 s"),
        (1500, "1.5 s"),
        (59_999, "60.0 s"),
        (60_000, "1.0 min"),
        (90_000, "1.5 min"),
        (3_599_999, "60.0 min"),
        (3_600_000, "1.0 hours"),
        (5_400_000, "1.5 hours"),
        (172_799_999, "48.0 hours"),
        (172_800_000, "2.0 days"),
        (432_000_000, "5.0 days"),
    ],
)
def test_duration_uses_the_unit_that_reads_at_that_scale(ms, expected):
    assert format_duration(ms) == expected


@pytest.mark.parametrize(
    "ms,unit",
    [
        (999.999, "ms"),
        (1000.0, "s"),
        (59_999.999, "s"),
        (60_000.0, "min"),
        (3_599_999.999, "min"),
        (3_600_000.0, "hours"),
        (172_799_999.999, "hours"),
        (172_800_000.0, "days"),
    ],
)
def test_each_unit_boundary_falls_on_the_larger_unit(ms, unit):
    assert format_duration(ms).split()[-1] == unit


# --- display_char ------------------------------------------------------------


@pytest.mark.parametrize(
    "char,expected",
    [
        ("\n", "↵"),
        ("\r", "↵"),
        ("\t", "→"),
        ("\b", "⌫"),
        ("\x00", "␀"),
    ],
)
def test_the_named_control_characters_get_their_own_glyph(char, expected):
    assert display_char(char) == expected


@pytest.mark.parametrize("char", ["a", "Z", " ", "é", "日", "\U0001f642", "@"])
def test_an_ordinary_character_is_returned_unchanged(char):
    assert display_char(char) == char


def test_none_renders_as_nothing():
    # A backspace keystroke carries char: null, and describing it must not
    # print "None" into the middle of a sentence.
    assert display_char(None) == ""


@pytest.mark.parametrize(
    "char",
    ["\x0b", "\x0c", "\x1b", "\x1c", "\x1e", "\x7f", "\x85", "\u2028", "\u2029"],
)
def test_every_control_character_becomes_one_printable_column(char):
    # These are the ones that were missing: U+000B and U+000C end a line as
    # surely as U+000A, and U+001B opens an escape sequence that would eat the
    # rest of the row. conftest's corpus contains U+000B and U+000C, so they
    # reach the renderer from ordinary input.
    glyph = display_char(char)
    assert len(glyph) == 1
    assert glyph.splitlines() == [glyph]
    assert glyph.isprintable()


# --- visible_tail ------------------------------------------------------------


def test_a_text_shorter_than_the_width_is_shown_whole():
    assert visible_tail("abc", 5) == f"abc{CURSOR}"


def test_a_text_exactly_the_width_is_shown_whole_without_an_ellipsis():
    assert visible_tail("abcde", 5) == f"abcde{CURSOR}"
    assert ELLIPSIS not in visible_tail("abcde", 5)


def test_a_longer_text_shows_the_tail_behind_an_ellipsis():
    assert visible_tail("abcdefgh", 5) == f"{ELLIPSIS}defgh{CURSOR}"


def test_the_empty_text_is_just_the_cursor():
    assert visible_tail("", 5) == CURSOR
    assert visible_tail("", 0) == CURSOR


def test_zero_width_leaves_the_ellipsis_and_the_cursor():
    # Nothing of the document fits, but the row still has to say there is a
    # document there and where the cursor sits in it.
    assert visible_tail("abc", 0) == f"{ELLIPSIS}{CURSOR}"


def test_the_cursor_always_ends_the_tail():
    for text in ("", "a", "abcdefghij"):
        assert visible_tail(text, 4).endswith(CURSOR)


def test_control_characters_in_the_tail_are_shown_as_glyphs():
    assert visible_tail("a\nb\tc", 10) == f"a↵b→c{CURSOR}"


@pytest.mark.parametrize("name", sorted(EDGE_CASES))
@pytest.mark.parametrize("width", [0, 1, 8, WIDTH])
def test_the_tail_never_exceeds_its_column_or_breaks_a_line(name, width):
    tail = visible_tail(EDGE_CASES[name], width)
    assert len(tail) <= width + 2
    assert tail.splitlines() == [tail]


# --- render: the header ------------------------------------------------------


def test_the_header_reports_the_records_own_metadata(record):
    rendered = render(record)
    meta, stats = record["metadata"], record["statistics"]
    assert f"characters : {meta['input_chars']}" in rendered
    assert f"({meta['input_words']} words)" in rendered
    assert f"profile    : {meta['profile']}, seed {meta['seed']}" in rendered
    assert f"speed      : {stats['wpm_active']:.1f} WPM" in rendered
    assert f"keystrokes : {stats['keystrokes']}" in rendered


def test_wall_clock_time_is_reported_only_when_there_are_session_gaps(
    record, gappy_record
):
    assert "wall clock" not in render(record)
    assert "wall clock" in render(gappy_record)


def test_an_unseeded_record_still_reports_a_concrete_seed():
    """An unseeded run draws its seed at generation time and records it.

    The header used to omit the seed line, which meant an unseeded record
    could never be regenerated from what it reported about itself. Now the
    drawn seed appears exactly like a requested one would.
    """
    record = make_record("Hello there.", seed=None)
    assert isinstance(record["metadata"]["seed"], int)
    rendered = render(record)
    assert f"seed {record['metadata']['seed']}" in rendered


# --- render: the body --------------------------------------------------------


def test_a_record_with_no_keystrokes_says_so():
    rendered = render(make_record("", seed=1))
    assert "(no keystrokes)" in rendered
    assert event_rows(rendered) == []


def test_a_one_character_record_renders_a_single_event():
    rendered = render(make_record("a", seed=1))
    assert descriptions(rendered) == ["start writing"]
    assert rendered.endswith(f"\nFinal text\n{'-' * 10}\na\n")


def test_the_first_keystroke_is_where_writing_starts(record):
    assert descriptions(render(record))[0] == "start writing"


def test_a_record_with_typos_reports_mistyping_and_noticing(typo_record):
    events = descriptions(render(typo_record))
    assert any(event.startswith("mistype ") for event in events)
    assert "notice it, backspace" in events
    assert any(event.startswith("retype ") for event in events)


def test_a_record_with_revisions_reports_deleting_and_rewriting(revision_record):
    events = descriptions(render(revision_record))
    assert any(re.fullmatch(r"delete back \d+ characters", e) for e in events)
    assert any(re.fullmatch(r"rewrite \d+ characters", e) for e in events)


def test_a_record_with_session_gaps_reports_stopping_for_the_day(gappy_record):
    rendered = render(gappy_record)
    stops = [line for line in rendered.splitlines() if "STOP - " in line]
    assert len(stops) == gappy_record["statistics"]["session_gaps"]
    assert all("until the next session" in stop for stop in stops)


def test_a_record_without_session_gaps_never_stops(record):
    assert record["statistics"]["session_gaps"] == 0
    assert "STOP - " not in render(record)


def test_a_session_gap_is_not_also_reported_as_a_thinking_pause(gappy_record):
    """A break gets one STOP line, not a STOP line and a pause line.

    motor_iki_ms is zeroed on the keystroke that resumes after a gap, so
    `iki_ms - motor_iki_ms` came out as the whole gap and the renderer
    announced it a second time as a thinking pause of the same length,
    immediately under the STOP line that had already reported it.
    """
    rendered = render(gappy_record)
    gap_ms = [
        i["duration_ms"]
        for i in gappy_record["intervals"]
        if i["kind"] == "session_gap"
    ]
    assert gap_ms, "the fixture is meant to contain session gaps"

    # The next EVENT after each STOP line must not be a minutes-long pause -
    # that is the gap being told twice. A genuine sub-second boundary pause
    # can legitimately sit right after a gap, so only the magnitude is banned.
    # render() puts a blank line directly after a STOP, so scan forward to the
    # next line with content rather than trusting position + 1.
    lines = rendered.splitlines()
    for position, line in enumerate(lines):
        if "STOP - " not in line:
            continue
        following = next(
            (later for later in lines[position + 1:] if later.strip()), ""
        )
        assert not re.search(r"pause [\d.]+ min", following), (
            f"gap reported twice:\n  {line.strip()}\n  {following.strip()}"
        )

    # And no reported pause is as long as any gap in the record.
    reported = re.findall(r"pause ([\d.]+) min", rendered)
    shortest_gap_min = min(gap_ms) / 60_000.0
    assert all(float(value) < shortest_gap_min for value in reported)


def test_moments_carry_only_the_document_tail():
    """The DOCUMENT column shows a tail, so the whole document must not be
    rebuilt per keystroke.

    Joining the full buffer on every keystroke is quadratic - a 57k-character
    essay took sixteen seconds to render. Wall-clock ratios cannot pin that
    down reliably (at test-sized inputs the quadratic version is still fast),
    so this asserts the structural property the fix actually consists of:
    every moment stores at most width+1 characters of document, no matter how
    long the document has grown. A reverted full-buffer join fails this on
    the first keystroke past the width.
    """
    width = 40
    record = make_record(PROSE_FOR_REPLAY * 4, seed=3)
    assert len(record["target_text"]) > width * 4
    moments = replay._moments(record, replay.DEFAULT_PAUSE_THRESHOLD_MS, width=width)
    assert moments, "the record is meant to produce moments"
    longest = max(len(m["text"]) for m in moments)
    assert longest <= width + 1, (
        f"a moment carries {longest} chars of document; the column only "
        f"shows {width}"
    )
    # And the tail is still enough for the renderer: the final moment ends
    # with the end of the document.
    assert record["target_text"].endswith(moments[-1]["text"][-width:])


def test_pauses_are_reported_once_they_are_long_enough(record):
    # The same record renders no pauses at all when the threshold is above
    # every pause in it, which is what makes the threshold the thing deciding.
    assert any(e.startswith("pause ") for e in descriptions(render(record)))
    quiet = descriptions(render(record, pause_threshold_ms=NO_PAUSES_MS))
    assert not any(e.startswith("pause ") for e in quiet)


# --- render: folding ---------------------------------------------------------


def test_consecutive_plain_typing_folds_into_one_line():
    # No typos, no revisions and a threshold no pause reaches, so after the
    # first keystroke every one of them is ordinary typing: one line, not
    # several hundred.
    record = make_record(PROSE_FOR_REPLAY, seed=4, typo_rate=0.0,
                         r_burst_probability=0.0)
    events = descriptions(render(record, pause_threshold_ms=NO_PAUSES_MS))
    typed = record["statistics"]["keystrokes"] - 1
    assert events == ["start writing", f"type {typed} characters"]


def test_consecutive_revision_deletes_fold_into_one_line_each(revision_record):
    events = descriptions(render(revision_record))
    counts = [
        int(match.group(1))
        for event in events
        if (match := re.fullmatch(r"delete back (\d+) characters", event))
    ]
    assert counts == role_runs(revision_record, "revision_delete")


def test_consecutive_revision_retypes_fold_into_one_line_each(revision_record):
    events = descriptions(render(revision_record))
    counts = [
        int(match.group(1))
        for event in events
        if (match := re.fullmatch(r"rewrite (\d+) characters", event))
    ]
    assert counts == role_runs(revision_record, "revision_retype")


def test_folding_never_loses_or_invents_a_keystroke(revision_record):
    # Every folded run reports its own length and every other row is a single
    # keystroke, so the two together have to add up to the whole record.
    rendered = render(revision_record, pause_threshold_ms=NO_PAUSES_MS)
    accounted = 0
    for event in descriptions(rendered):
        match = re.fullmatch(r"(?:type|delete back|rewrite) (\d+) characters", event)
        accounted += int(match.group(1)) if match else 1
    assert accounted == revision_record["statistics"]["keystrokes"]


def test_a_run_of_one_still_reports_its_count(typo_record):
    # A single character typed between two typos is a run of length one. It
    # folds like any other run rather than falling through to a per-character
    # line, so the collapsed rendering never describes one keystroke of
    # ordinary typing on its own.
    events = descriptions(render(typo_record, pause_threshold_ms=NO_PAUSES_MS))
    assert "type 1 characters" in events
    assert not any(re.fullmatch(r"type '.+'", event) for event in events)


# --- render: full vs collapsed -----------------------------------------------


@pytest.mark.parametrize(
    "fixture_name", ["record", "typo_record", "revision_record", "gappy_record"]
)
def test_full_is_never_shorter_than_collapsed(fixture_name, request):
    record = request.getfixturevalue(fixture_name)
    full = render(record, full=True).splitlines()
    collapsed = render(record).splitlines()
    assert len(full) >= len(collapsed)


def test_full_gives_every_keystroke_its_own_line(revision_record):
    rendered = render(revision_record, full=True)
    assert len(event_rows(rendered)) == revision_record["statistics"]["keystrokes"]


def test_full_folds_nothing(revision_record):
    events = descriptions(render(revision_record, full=True))
    assert not any(
        re.fullmatch(r"(?:type|delete back|rewrite) \d+ characters", event)
        for event in events
    )


def test_collapsed_is_strictly_shorter_when_there_is_a_run_to_fold(record):
    assert len(render(record, full=True)) > len(render(record))


# --- render: the invariants --------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"typo_rate": 0.3},
        {"r_burst_probability": 1.0},
        {"session_chars": SHORT_SESSION},
        {"typo_rate": 0.0, "r_burst_probability": 0.0},
    ],
)
@pytest.mark.parametrize("full", [False, True])
def test_the_final_text_is_the_target_text(kwargs, full):
    record = make_record(PROSE_FOR_REPLAY, seed=21, **kwargs)
    rendered = render(record, full=full)
    assert rendered.endswith(
        f"\nFinal text\n{'-' * 10}\n{record['target_text']}\n"
    )


@pytest.mark.parametrize("name", sorted(EDGE_CASES))
def test_the_final_text_survives_every_edge_case(name):
    text = EDGE_CASES[name]
    rendered = render(edge_case_record(name), full=True)
    if not text:
        assert "(no keystrokes)" in rendered
        return
    assert rendered.endswith(f"\nFinal text\n{'-' * 10}\n{text}\n")


@pytest.mark.parametrize("name", sorted(EDGE_CASES))
@pytest.mark.parametrize("width", [0, 1, 12, WIDTH])
def test_the_document_column_never_wraps(name, width):
    # A raw control character in the document would end the row early and push
    # the event column of every following line out of alignment. The document
    # field is therefore exactly `width + 2` columns - ellipsis, tail, cursor -
    # and the event column starts one space after it on every row.
    record = edge_case_record(name)
    for full in (False, True):
        rendered = render(record, full=full, width=width)
        for row in event_rows(rendered):
            document = row[DOCUMENT_START:DOCUMENT_START + width + 2]
            assert len(document) == width + 2, row
            assert row[DOCUMENT_START + width + 2] == " ", row
            assert row[DOCUMENT_START + width + 3] != " ", row


@pytest.mark.parametrize("width", [0, 1, 12, WIDTH, 200])
def test_the_document_column_holds_for_a_record_with_everything_in_it(width):
    record = make_record(
        PROSE_FOR_REPLAY * 2,
        seed=7,
        typo_rate=0.2,
        r_burst_probability=1.0,
        session_chars=SHORT_SESSION,
    )
    rendered = render(record, width=width)
    for row in event_rows(rendered):
        assert row[DOCUMENT_START + width + 2] == " ", row
        assert row[DOCUMENT_START + width + 3] != " ", row


def test_the_header_row_matches_the_document_column(record):
    rendered = render(record, width=WIDTH)
    header = next(
        line for line in rendered.splitlines() if line.strip().startswith("TIME")
    )
    assert header[DOCUMENT_START:].startswith("DOCUMENT")
    assert header[DOCUMENT_START + WIDTH + 2] == " "
    assert header[DOCUMENT_START + WIDTH + 3:] == "EVENT"


def test_the_rendered_timeline_is_in_time_order(gappy_record):
    stamps = [row[2:14].strip() for row in event_rows(render(gappy_record))]
    # Session gaps push the document past a day, so the stamps change shape
    # part way down. Comparing the parsed times, not the strings, is the point.
    assert stamps == sorted(stamps, key=_timestamp_sort_key)


def _timestamp_sort_key(stamp):
    days = 0
    if "d " in stamp:
        day_part, stamp = stamp.split("d ")
        days = int(day_part)
    parts = stamp.split(":")
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60.0 + float(part)
    return days * 86_400.0 + seconds


def test_rendering_is_deterministic_for_a_seed():
    first = render(make_record(PROSE_FOR_REPLAY, seed=9))
    second = render(make_record(PROSE_FOR_REPLAY, seed=9))
    assert first == second


def test_rendering_does_not_mutate_the_record():
    record = make_record(PROSE_FOR_REPLAY, seed=10, r_burst_probability=1.0)
    before = repr(record)
    render(record, full=True)
    render(record)
    assert repr(record) == before


def test_the_default_width_and_threshold_are_the_module_constants(record):
    assert render(record) == render(
        record,
        width=replay.DEFAULT_WIDTH,
        pause_threshold_ms=replay.DEFAULT_PAUSE_THRESHOLD_MS,
    )


# --- the CLI's replay formats ------------------------------------------------


@pytest.mark.parametrize("output_format", ["replay", "replay-full"])
def test_the_cli_renders_the_same_text_the_module_does(output_format, capsys):
    assert main.main(
        ["--text", "Hello there, this is a test.", "--seed", "8",
         "--format", output_format]
    ) == 0
    out = capsys.readouterr().out
    record = main.generate_full_output("Hello there, this is a test.", seed=8)
    assert out == render(record, full=output_format == "replay-full") + "\n"


def test_the_default_format_is_still_json(capsys):
    assert main.main(["--text", "Hello.", "--seed", "8"]) == 0
    assert capsys.readouterr().out.lstrip().startswith("{")


def test_an_unknown_format_is_rejected():
    with pytest.raises(ValueError, match="unknown output format"):
        main.render_record(make_record("Hello.", seed=1), "html")
