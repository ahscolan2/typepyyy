# Model and parameters

TypeTrace models a possible sequence of editing actions for supplied text. It
does not infer how that text was actually written. Matching a few timing
statistics does not establish that a record represents a particular writer or
population.

The [macro scripter](../macro_scripter.py) chooses actions, the
[timing engine](../timing_engine.py) assigns motor timings, and the
[pipeline](../pipeline.py) combines them into one clock and checks the final text.

## Generation options

| CLI option | Default | Effect and accepted range |
| --- | --- | --- |
| `--profile` | `average` | Interval multiplier: `slow` 1.5, `average` 1.0, `fast` 0.7 |
| `--typo-rate` | `0.03` | Error probability at eligible positions; 0–1 |
| `--typo-model` | `neighbor` | Neighbor-key slips, or `rich` for a wider repertoire |
| `--r-burst-probability` | `0.20` | Chance of a revision burst; 0–1 |
| `--structural-revision-rate` | `0.08` | Chance of revision at a completed sentence; 0–1 |
| `--session-chars` | automatic | Positive integer character budget per session |
| `--target-autocorrelation` | `0.35` | Target lag-1 correlation of motor intervals; 0 ≤ value < 0.9 |
| `--fatigue-rate` | `0.03` | Interval increase per ten minutes of motor time; nonnegative |
| `--warmup-strength` | `0.10` | Initial interval increase; 0 ≤ value < 1 |
| `--familiarity-boost` | `0.08` | Speedup for previously typed digraphs; 0 ≤ value < 1 |

Set an error, revision, or dynamic parameter to zero to disable that mechanism.
Set both revision probabilities to zero to disable both kinds of revision.
Timing targets are approximate and depend on the text and sample length.

## Actions

Fluent bursts are sampled at 8–13 words; revision bursts at 3–7 words. Revision
bursts delete 40–100% of their completed text and retype it. Structural revisions
delete and retype a sentence, sometimes reaching across a paragraph boundary.
Retyped text is identical to the original: the model does not generate alternate
wording or move the cursor to edit arbitrary earlier passages.

`neighbor` introduces adjacent-key substitutions. `rich` additionally includes
transpositions, repeats, anticipation of upcoming letters, and perseveration of
earlier letters. The scripter chooses among applicable errors at a position and
corrects them immediately after a reaction pause. The resulting typo-keystroke
fraction need not equal `--typo-rate`.

Inside a burst, pauses occur at clause, sentence, and paragraph boundaries with
probabilities 0.2, 0.4, and 1.0. Ordinary word boundaries add no separate pause.
Burst endings also produce pauses. Pause distributions are lognormal with
boundary-dependent parameters; the stored medians for word, clause, sentence,
and paragraph pauses are approximately 90, 181, 493, and 1,097 ms.

Automatic sessions use a character budget derived from 20–90 nominal minutes at
160 characters/minute; this is not a measured wall-clock duration. Session gaps
sample 3, 5, 8, 11, or 13 minutes with weights 0.22, 0.28, 0.24, 0.16, and 0.10,
plus ±15% jitter. Each sampled pause or session gap is capped at 15 minutes.

## Motor timing

The keyboard model uses US QWERTY digraph classes: alternate hand (136 ms), same
hand with different fingers (168 ms), same finger (218 ms), and space (120 ms).
These are baselines, not the final intervals. The engine applies a global scale
of 1.7344, the profile multiplier, a 40% speedup for its common-bigram list,
correlated random variation, and within-document dynamics.

Key holds are sampled from a normal distribution with mean 116 ms and standard
deviation 20 ms, with a 40 ms floor. Eligible opposite-hand pairs can overlap;
the previous key's hold is extended to represent this rollover.

Warmup decays with a 25-second time constant. Fatigue grows with motor time.
Both reset after a session gap. Familiarity persists across sessions within the
same document. Correlated variation uses an AR(1) latent process calibrated to
the actual digraph sequence.

These constants describe this implementation. They are modeling and calibration
choices, not guarantees of human realism or independently validated population
estimates.

## Measure current behavior

```sh
python benchmark.py
```

The benchmark prints speed, dwell, intervals, correlation, deletion, rollover,
and correlation tracking across different text samples. It reports the Python
and NumPy versions. Default summary measurements use 40 seeds; tracking uses
10 seeds per cell. Bracketed ranges are observed minima and maxima, not confidence
intervals.

For a faster check:

```sh
python benchmark.py --seeds 4 --repeat 1 --skip-tracking
```

The keystroke-clock WPM measurement types the sample directly through the timing
engine. Whole-pipeline `wpm_active` also counts thinking and revision time, so
it is normally lower. Compare like measurements when adjusting the model.
