# Project TypeTrace

TypeTrace generates synthetic writing-process data: keystroke-level records of how a
piece of text could plausibly have been typed, including inter-key intervals, key
hold times, thinking pauses, typos that get corrected, revisions, and gaps between
writing sessions. The records are intended as training and evaluation data for
academic-integrity research — for example, as inputs to detectors of
machine-generated writing. TypeTrace can also replay a record into a real document
editor, so we can study what those editors actually record of the writing process.
Run replays only against documents and accounts you own or have explicit consent to use.

On its own it writes data files; with the optional extras installed it can also
type a record into a live editor — see **Replaying into a live editor** below.

## Install

```bash
pip install -r requirements.txt
```

numpy is the only runtime dependency. Python 3.10 or newer.

Replaying into an editor is optional and pulls in extra dependencies, installed
separately:

- **Google Docs replay:** `pip install playwright` followed by
  `playwright install chromium`
- **Desktop replay:** `pip install pynput`

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

Opens a desktop window that walks through the whole workflow in order — text (typed
or loaded from a file), every model parameter from the CLI with validation and
defaults, output format with a preview and a save picker, and live emission controls
(docs or desktop target, document ID, speed, silence cap, headless, browser profile).
It calls the same generator, so a given seed produces the same record either way.
tkinter ships with CPython, so there is nothing further to install.

## Replaying into a live editor

With `--emit`, a generated record is typed back into a real document editor after it
has been generated and written, replaying the record's own keystroke clock — keydowns
and keyups in time order, with the modelled inter-key intervals, dwells, pauses and
session gaps.

### Google Docs

```bash
python main.py --text @essay.txt --seed 42 --output record.json --emit docs --doc-id <document-id>
```

Requires the playwright extra (see Install). The first run opens a visible browser
window so you can log into your own Google account; the browser profile persists in
`.typetrace-browser-profile/`, so later runs reuse that login (`--headless` only makes
sense once the profile exists — logging in needs the visible window). Use a blank
test document: the document ID is the long string in its URL. Afterwards, inspect
**Tools → Version history** in Google Docs to see what the editor recorded.

### Desktop (Word and other editors)

```bash
python main.py --text "Hello world. This is a test." --seed 42 --emit desktop -o record.json
```

Requires the pynput extra. Emission types into whatever window has focus — Word,
Notepad, a text field. It counts down five seconds before starting so you can focus
the target window, and pressing **Esc** aborts the emission.

### Timing control

Timing is faithful to the record by default. `--emit-speed 2.0` compresses time by
that factor; `--emit-max-gap-s 5` shortens any silence longer than five seconds —
a multi-hour session gap, say — to exactly five seconds.

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
| `--structural-revision-rate` | Probability at each completed sentence of deleting it back whole and retyping it; 0 disables | `0.08` |
| `--session-chars` | Force a session to end after this many characters | none |
| `--target-autocorrelation` | Target lag-1 autocorrelation of motor intervals | `0.35` |
| `--fatigue-rate` | Fraction per 10 min of active typing by which motor intervals inflate; 0 disables | `0.03` |
| `--warmup-strength` | Initial fractional slowness, decaying over roughly the first minute; 0 disables | `0.10` |
| `--familiarity-boost` | Speedup on digraphs already typed in this document; 0 disables | `0.08` |
| `--verbose`, `-v` | Print a summary to stderr | off |
| `--emit` | Replay the record into a live editor after writing it: `docs` or `desktop` | off |
| `--doc-id` | Google Docs document ID (with `--emit docs`) | none |
| `--emit-speed` | Time compression for emission; `2.0` is twice as fast | `1.0` |
| `--emit-max-gap-s` | Shorten silences longer than this to exactly this many seconds | none |
| `--headless` | Run the browser without a visible window | off |
| `--browser-profile` | Browser profile directory holding the Google login | `.typetrace-browser-profile` |

To pass literal text beginning with `@`, escape it as `\@`.

## Output

```json
{
  "generated_by": "TypeTrace-Research",
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
TypeTrace writing replay
========================

  characters : 177  (29 words)
  profile    : average, seed 23
  elapsed    : 1.2 min writing
  speed      : 29.1 WPM
  keystrokes : 239 (31 backspaces, 3 typo events, 0 session gaps)

          TIME  DOCUMENT                                                   EVENT
  ------------  ---------------------------------------------------------- ------------------------------
     00:00.000  A|                                                         start writing
     00:00.332  …c integrity depends on evidence that a piece of writing | type 62 characters
     00:16.989  … integrity depends on evidence that a piece of writing w| pause 1.1 s
     00:17.185  … evidence that a piece of writing was actually composed | type 21 characters
     00:23.670  …integrity depends on evidence that a piece of writing wa| delete back 20 characters
     00:31.734  … evidence that a piece of writing was actually composed | rewrite 20 characters
     00:39.069  …evidence that a piece of writing was actually composed b| pause 745 ms
     00:39.390  … a piece of writing was actually composed by the person | type 13 characters

     [11 rows elided]

     01:09.926  … The process leaves traces that a finished document doez| mistype 'z'
     01:10.769  …. The process leaves traces that a finished document doe| notice it, backspace
     01:11.034  … The process leaves traces that a finished document does| retype 's'
     01:11.456  …process leaves traces that a finished document does not.| type 5 characters

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

These are inputs to the model, not outputs of it. Rows marked *model choice*
have no literature source; their directions are commonplace and their
magnitudes are tuned small enough that the achieved statistics below stay in
their bands.

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
| Structural revision rate | 0.08 at each completed sentence | model choice |
| — of which reaches back across a paragraph break | 0.30 | model choice |
| Fatigue | +3% motor intervals per 10 min of active typing | model choice |
| Warmup | +10% at the start, decaying with τ = 25 s | model choice |
| Familiarity | repeated digraphs 8% faster, per document | model choice |
| Pause medians | 90 / 181 / 493 / 1097 ms | word / clause / sentence / paragraph |
| Session length | 20–90 min | at 160 chars/min composition |

### Achieved

Measured over 40 seeds on English prose with default settings. All of these are
text-dependent, so treat them as bands rather than constants.

| Quantity | Achieved | Target |
|---|---|---|
| `average` profile, keystroke clock | 51.7 WPM [48.8, 55.1] | 52 WPM (Dhakal) |
| `slow` / `fast` profile | 34.4 / 74.3 WPM | relative |
| `wpm_active`, whole pipeline | 30.3 [22.8, 37.4] | lower than above; composition pauses and revisions count |
| Mean dwell | 119.0 ms [117.2, 120.9] | 116 plus genuine rollover extension |
| Mean motor interval | 248.2 ms [235.6, 259.5] | — |
| Lag-1 autocorrelation | 0.357 [0.314, 0.397] | 0.35 |
| Deletion ratio | 0.165 [0.071, 0.288] | 0.10–0.30 reported for real composition |
| Rollover | ~11% of keystrokes | — |

Burstiness is modelled as an AR(1) latent speed in log space. `--target-autocorrelation`
sets the lag-1 autocorrelation of the emitted motor intervals, and the engine solves for
the latent variance that produces it, calibrating against the digraph mix of the text
actually being typed — including which digraphs repeat, since repeated digraphs earn the
familiarity speedup. It tracks the target closely across very different texts:

| Target | English prose | Same-finger heavy | Alternation heavy | Punctuation/caps heavy |
|---|---|---|---|---|
| 0.35 | 0.345 | 0.356 | 0.346 | 0.357 |
| 0.50 | 0.505 | 0.505 | 0.515 | 0.492 |
| 0.65 | 0.650 | 0.643 | 0.649 | 0.644 |

On top of the stationary model, three within-document dynamics multiply the motor
interval: a warmup decay over roughly the first minute of a session, a slow fatigue
drift upward with elapsed active-typing time, and a familiarity speedup on digraphs
already typed in this document. The familiarity cache is document-level and survives
session gaps; the other two clocks reset with each session. All three are model
choices (see Inputs) and each is disabled by setting its flag to zero.

Revision happens at two scales. An R-burst deletes back a fraction of the burst just
written and retypes it. A structural revision (`--structural-revision-rate`), rolled
when a sentence completes, deletes the whole sentence — sometimes reaching back over a
paragraph break to the previous paragraph's trailing sentence — and retypes the
identical characters, so the replay invariant holds by construction. Structural
revisions are what lift the deletion ratio into the reported 0.10–0.30 band; the
burst-local mechanism alone cannot reach it.

## Testing

```bash
python -m pytest -q
```

## License

MIT. Intended for academic research.
