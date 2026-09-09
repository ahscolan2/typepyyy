# Record format

The JSON format is intended for analysis and replay. Times are in milliseconds,
relative to the start of the generated record, rounded to three decimal places.

## Top-level fields

| Field | Meaning |
| --- | --- |
| `generated_by` | `"TypeTrace-Research"` |
| `purpose` | `"detection_training"` |
| `synthetic_research_data` | Always `true` in CLI and GUI output |
| `schema_version` | `2` |
| `metadata` | Resolved generation options, seed, input character and word counts |
| `target_text` | Original input text |
| `macro_script` | Ordered typing, deletion, pause, and session-gap operations |
| `keystrokes` | Characters and Backspaces on an absolute clock |
| `intervals` | Explicit pauses and session gaps on the same clock |
| `statistics` | Counts, durations, speed, and timing summaries |

`main.generate_full_output()` adds the synthetic markers to the record returned by
`pipeline.generate()`. Use the former when generating records through Python.

## Operations and keystrokes

Each `macro_script` entry has `op` and `role`, plus one payload:

| `op` | Payload |
| --- | --- |
| `TYPE` | `char`: one Python character |
| `DELETE` | `count`: number of characters to remove from the end |
| `PAUSE` | `duration_ms` |
| `SESSION_GAP` | `duration_ms` |

Each `keystrokes` entry contains:

| Field | Meaning |
| --- | --- |
| `index` | Zero-based position in the keystroke list |
| `kind` | `key` or `backspace` |
| `char` | One character for a key; `null` for Backspace |
| `keydown_ms`, `keyup_ms` | Absolute press and release times |
| `dwell_ms` | Time held down: `keyup_ms - keydown_ms` |
| `iki_ms` | Time since the previous keydown, including deliberate pauses |
| `motor_iki_ms` | Motor component of that interval, excluding deliberate pauses |
| `flight_ms` | Current keydown minus previous keyup; negative means overlap |
| `role` | `text`, `typo`, `correction`, `revision_delete`, or `revision_retype` |

The first keystroke has no predecessor. Its `iki_ms`, `motor_iki_ms`, and
`flight_ms` are zero. The first key after a session gap also has zero
`motor_iki_ms`; its full `iki_ms` still includes the break. Overlapping holds are
expected, so process keydown and keyup events in timestamp order for live replay.

An `intervals` entry has `kind` (`pause` or `session_gap`), `start_ms`,
`duration_ms`, and `role`. Pauses and gaps also affect the keystroke timestamps;
do not add their durations a second time when replaying that clock.

## Statistics

- **Time:** `total_time_ms`, `active_time_ms`, and `session_gap_ms`. Active time
  excludes session gaps but includes thinking pauses, corrections, and revisions.
- **Speed:** `wpm_active` and `wpm_wall_clock` use the final text length divided by
  five characters per word. Only the latter includes session gaps.
- **Counts:** `keystrokes`, `character_keystrokes`, `backspaces`, `pauses`,
  `session_gaps`, `typo_keystrokes`, and `revision_deleted_chars`.
- **Deletion:** `deletion_ratio` is Backspaces divided by character keystrokes,
  including characters typed and later deleted.
- **Timing:** `mean_iki_ms`, `mean_motor_iki_ms`, `mean_dwell_ms`,
  `lag1_autocorrelation`, and `rollover_keystrokes`. Autocorrelation uses positive
  motor intervals. `rollover_keystrokes` counts negative flight times.

`metadata.input_words` uses whitespace-separated words, so it is not the word
count used by the WPM formula. Counts of typo *keystrokes* can differ from counts
of typo *events*, especially with `--typo-model rich`.

## Check a record

Generation checks both the macro script and keystroke stream against the target.
To independently reconstruct saved JSON:

```python
import json

with open("record.json", encoding="utf-8") as source:
    record = json.load(source)

buffer = []
for key in record["keystrokes"]:
    if key["kind"] == "backspace":
        if buffer:
            buffer.pop()
    else:
        buffer.append(key["char"])
assert "".join(buffer) == record["target_text"]
```

This verifies a plain-text buffer. It does not verify what a live editor saved.
Schema version describes record structure; archive the generator revision and
dependency versions alongside datasets when exact regeneration matters.
