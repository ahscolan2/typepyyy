# Project Aletheia

Aletheia generates synthetic writing-process data: keystroke-level records of how a
piece of text could plausibly have been typed, including inter-key intervals, key
hold times, thinking pauses, typos that get corrected, revisions, and gaps between
writing sessions. The records are intended as training and evaluation data for
detectors of machine-generated writing.

It writes data files. It does not automate a browser, drive a document editor, or
make any network request.

## Install

```bash
pip install -r requirements.txt
```

numpy is the only runtime dependency. Python 3.10 or newer.

## Quickstart

```bash
python main.py --text "Hello world. This is a test." --seed 42
```

Writes one JSON record to stdout. To generate from a file and save the result:

```bash
python main.py --text @essay.txt --profile fast --seed 42 --output record.json
```

To read the writing process rather than the record, ask for the replay format:

```bash
python main.py --text @essay.txt --seed 42 --format replay
```

## Desktop app

```bash
python gui.py
```

Opens a desktop window exposing the same parameters as the CLI — text or input file,
profile, seed, typo rate, r-burst probability, session length, target autocorrelation —
and the same output formats. It calls the same generator, so a given seed produces the
same record either way. tkinter ships with CPython, so there is nothing further to
install.

## CLI

| Option | Description | Default |
|---|---|---|
| `--text`, `-t` | Text to simulate, or `@file.txt` to read from a file | required |
| `--profile`, `-p` | `slow`, `average` or `fast` | `average` |
| `--seed`, `-s` | Random seed; the same seed reproduces the record exactly | none |
| `--format`, `-F` | `json`, `replay` or `replay-full` | `json` |
| `--output`, `-o` | Write here instead of stdout | stdout |
| `--force`, `-f` | Overwrite the output file if it exists | off |
| `--typo-rate` | Per-character typo probability | `0.03` |
| `--r-burst-probability` | Probability a burst ends in a revision | `0.20` |
| `--session-chars` | Force a session to end after this many characters | none |
| `--target-autocorrelation` | Target lag-1 autocorrelation of motor intervals | `0.35` |
| `--verbose`, `-v` | Print a summary to stderr | off |

To pass literal text beginning with `@`, escape it as `\@`.

## Output

```json
{
  "generated_by": "Aletheia-Research",
  "purpose": "detection_training",
  "synthetic_research_data": true,
  "schema_version": 2,
  "metadata": {
    "profile": "average", "seed": 42, "typo_rate": 0.03,
    "r_burst_probability": 0.2, "input_chars": 12, "input_words": 2
  },
  "statistics": { "...": "see below" },
  "macro_script": [
    {"op": "TYPE", "role": "text", "char": "H"},
    {"op": "PAUSE", "role": "text", "duration_ms": 95.98}
  ],
  "keystrokes": [
    {"index": 1, "kind": "key", "char": "e",
     "keydown_ms": 66.807, "keyup_ms": 185.364, "dwell_ms": 118.557,
     "iki_ms": 66.807, "motor_iki_ms": 66.807, "flight_ms": -68.005,
     "role": "text"}
  ],
  "intervals": [
    {"kind": "pause", "start_ms": 960.334, "duration_ms": 95.98, "role": "text"}
  ],
  "target_text": "Hello world."
}
```

`macro_script` is what happened (type, pause, delete, session gap). `keystrokes` is
when it happened, on one absolute clock. `intervals` records pauses and session gaps
as spans. Replaying `keystrokes` reproduces `target_text` exactly — generation fails
loudly rather than emitting a record where it does not.

Field notes:

- `kind` is `key` or `backspace`. A backspace has `char: null`.
- `iki_ms` is the full keydown-to-keydown interval and includes any deliberate pause.
  `motor_iki_ms` is the motor component alone, and is what the timing model controls.
  It is `0` for the first keystroke and for the first keystroke after a session gap.
- `flight_ms` is previous-keyup to this-keydown. It is **negative when the keys
  overlap** (rollover), which is expected.
- `role` is one of `text`, `typo`, `correction`, `revision_delete`, `revision_retype`,
  so a consumer can label keystrokes without re-deriving intent.

`statistics` contains: `total_time_ms`, `active_time_ms`, `session_gap_ms`,
`wpm_active`, `wpm_wall_clock`, `keystrokes`, `character_keystrokes`, `backspaces`,
`pauses`, `session_gaps`, `typo_keystrokes`, `revision_deleted_chars`,
`deletion_ratio`, `mean_iki_ms`, `mean_motor_iki_ms`, `mean_dwell_ms`,
`lag1_autocorrelation`, `rollover_keystrokes`.

WPM uses the standard five-characters-per-word convention. `wpm_active` excludes time
between writing sessions; `wpm_wall_clock` does not, so it collapses once a document
is long enough to contain gaps.

## Replay

`--format replay` renders the same record as an account of the writing process: what
the document looked like as it was being written, where the writer hesitated, what
they mistyped, what they went back and rewrote, and where they stopped for the day.
It is the format to read when checking whether generated output looks like someone
writing. Truncated, from

```bash
python main.py --text "Academic integrity depends on evidence ..." \
  --seed 23 --format replay
```

```
Aletheia writing replay
=======================

  characters : 177  (29 words)
  profile    : average, seed 23
  elapsed    : 1.1 min writing
  speed      : 30.9 WPM
  keystrokes : 237 (30 backspaces, 2 typo events, 0 session gaps)

          TIME  DOCUMENT                                                   EVENT
  ------------  ---------------------------------------------------------- ------------------------------
     00:00.000  A|                                                         start writing
     00:00.284  …c integrity depends on evidence that a piece of writing | type 62 characters
     00:14.673  … integrity depends on evidence that a piece of writing w| pause 1.1 s
     00:15.036  … evidence that a piece of writing was actually composed | type 21 characters
     00:21.250  …integrity depends on evidence that a piece of writing wa| delete back 20 characters
     00:29.073  … evidence that a piece of writing was actually composed | rewrite 20 characters
     00:34.146  …evidence that a piece of writing was actually composed b| pause 745 ms
     00:34.446  … a piece of writing was actually composed by the person | type 13 characters

     [10 rows elided]

     00:59.386  …the person who submitted it. The process leaves traces f| mistype 'f'
     01:00.024  … the person who submitted it. The process leaves traces | notice it, backspace
     01:00.262  …the person who submitted it. The process leaves traces t| retype 't'
     01:00.447  …process leaves traces that a finished document does not.| type 33 characters

Final text
----------
Academic integrity depends on evidence that a piece of writing was actually composed by the person who submitted it. The process leaves traces that a finished document does not.
```

The `DOCUMENT` column is the tail of the document as it stood after that event, with
`|` for the cursor and `…` where the line was cut. Runs of ordinary typing are folded
into one line reporting how many characters they covered; a pause, a typo, a revision
or the end of a session keeps a line of its own. A session gap prints as its own
`STOP` line, and the timestamp then widens to `1d 04:12:37` rather than rolling over.

`--format replay-full` prints one line per keystroke with nothing folded. It is
thousands of lines for an essay, which is why it is not the default.

## Model

### Inputs

These are set from the literature and are not outputs of the model.

| Parameter | Value | Source |
|---|---|---|
| Alternate-hand digraph | 136 ms | Salthouse (1986) |
| Same-hand, different finger | 168 ms | Salthouse (1986) |
| Same finger | 218 ms | Salthouse (1986) |
| Space bar | 120 ms | thumb, overlaps either hand |
| Dwell time | N(116, 20) ms, truncated at 40 | Dhakal et al. CHI 2018 |
| P-burst length | 8–13 words | Chenoweth & Hayes |
| R-burst length | 3–7 words | Chenoweth & Hayes |
| R-burst probability | 0.20 | Leijten & Van Waes |
| Pause medians | 90 / 181 / 493 / 1097 ms | word / clause / sentence / paragraph |
| Session length | 20–90 min | at 160 chars/min composition |

### Achieved

Measured over 40 seeds on English prose with default settings. All of these are
text-dependent, so treat them as bands rather than constants.

| Quantity | Achieved | Target |
|---|---|---|
| `average` profile, keystroke clock | 54.2 WPM [48.9, 59.9] | 52 WPM (Dhakal) |
| `slow` / `fast` profile | 36.0 / 77.3 WPM | relative |
| `wpm_active`, whole pipeline | 36.0 [26.5, 46.0] | lower than above; composition pauses count |
| Mean dwell | 119.1 ms [116.7, 121.6] | 116 plus genuine rollover extension |
| Mean motor interval | 232.0 ms [206.8, 252.5] | — |
| Lag-1 autocorrelation | 0.352 [0.263, 0.431] | 0.35 |
| Deletion ratio | 0.102 [0.019, 0.209] | 0.10–0.30 reported for real composition |
| Rollover | ~13% of keystrokes | — |

Burstiness is modelled as an AR(1) latent speed in log space. `--target-autocorrelation`
sets the lag-1 autocorrelation of the emitted motor intervals, and the engine solves for
the latent variance that produces it, calibrating against the digraph mix of the text
actually being typed. It tracks the target closely across very different texts:

| Target | English prose | Same-finger heavy | Alternation heavy | Punctuation/caps heavy |
|---|---|---|---|---|
| 0.35 | 0.350 | 0.330 | 0.350 | 0.373 |
| 0.50 | 0.487 | 0.506 | 0.496 | 0.503 |
| 0.65 | 0.639 | 0.646 | 0.647 | 0.649 |

## Testing

```bash
python -m pytest -q
```

## Limitations

- **Deletion is local.** Revision deletes back into the burst just written and retypes
  it. Structural rewriting — deleting a sentence and reworking it, moving a paragraph —
  is not modelled. `deletion_ratio` lands around 0.10, at the bottom of the 0.10–0.30
  range reported for real composition. Raising `--r-burst-probability` to about 0.25
  moves it toward the middle of that range if matching the aggregate matters more than
  matching the cited burst rate.
- **Session gaps need a long document.** A session runs 20–90 minutes at a nominal 160
  characters per minute, so a few thousand characters produce no gaps at all. Use
  `--session-chars` to force them.
- **Low autocorrelation targets are approximate.** The digraph order of a text carries
  autocorrelation of its own, and the latent process can add to that but not subtract
  from it. Above about 0.35 the emitted value tracks the target closely; below it the
  result sits above the request by roughly 0.02 to 0.07, varying with the text. Asking
  for 0.15 yields 0.14 to 0.22 depending on the passage.
- **English on QWERTY.** The keyboard geometry, bigram table and neighbour-key typo
  model all assume a US QWERTY layout and English text. Other layouts and languages
  will produce plausible-looking but uncalibrated timings.
- **Not validated against real keystroke logs.** The model reproduces the summary
  statistics it was built from. Whether a detector trained on this data transfers to
  real human writing is an open question and is not evidenced here.

## legacy_browser_path/

The original project also contained a Chrome DevTools module that typed into a live
Google Docs document. It is not part of this package: nothing imports it, the CLI has
no path into it, and it is excluded from the test and lint runs. It is kept in
`legacy_browser_path/`, unmodified, with a README explaining what it does and why it
sits apart. Read that before using it.

That README also carries the full **"What was not done"** record for this rewrite —
every gap left behind, what each one means in practice, and what it costs. The
Limitations section above is the part that affects how you read the output; the
record there is the complete list, including decisions that were made on the
maintainer's judgement rather than the owner's.

## License

MIT. Intended for academic research.
