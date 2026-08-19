"""
Project TypeTrace - reproduce the Achieved tables in the README.

The README quotes measured statistics - WPM, dwell, motor interval, lag-1
autocorrelation, deletion ratio, rollover rate - and a table showing how
closely the emitted autocorrelation tracks --target-autocorrelation across
texts with very different digraph mixes. Nothing in the repository produced
those numbers, so a reader had no way to check them and a change that moved
them had no way to announce itself.

    python benchmark.py            # both tables
    python benchmark.py --seeds 80 # steadier means, slower; the bracketed
                                   # bands are min-max, so more seeds can only
                                   # widen them

Everything here is measured through pipeline.generate at default settings, so
what it reports is what the CLI produces. It writes nothing.
"""

import argparse
import statistics
import sys
from typing import List, Sequence

import pipeline
import timing_engine as te

DEFAULT_SEEDS = 40

# The prose the README's figures are quoted on: ordinary English, long enough
# for the within-document dynamics to matter.
PROSE = (
    "Academic integrity depends on evidence that a piece of writing was "
    "actually composed by the person who submitted it. The process leaves "
    "traces that a finished document does not: the pauses where the writer "
    "was thinking, the sentences that were written and then taken back, the "
    "gaps where they stopped for a while and came back to it. A finished "
    "essay records none of that. Process data records all of it, and that "
    "asymmetry is what makes it worth collecting. "
)

# Texts chosen to stress the digraph classifier in different directions, for
# the autocorrelation tracking table.
TEXT_KINDS = {
    "English prose": PROSE,
    "Same-finger heavy": (
        "deed feed greed breed freed treed decreed agreed exceed proceed "
        "kimono minimum unmimicked lollipop pupil bubble juju numb humdrum "
    ) * 4,
    "Alternation heavy": (
        "the them then they their theme there thence rhythm eighth height "
        "problem penalty auditor turkey dormant island signal profit "
    ) * 4,
    "Punctuation/caps heavy": (
        'He said, "Why NOW?" - and (again) asked: "WHO, exactly?" '
        "A.B.C.; D.E.F.! G/H\\I? J-K_L. M+N=O. P*Q&R^S. "
    ) * 4,
}

TARGETS = (0.35, 0.50, 0.65)


def interval(values: Sequence[float]) -> str:
    """Mean with a min-max band, formatted the way the README quotes them."""
    return f"{statistics.mean(values):.1f} [{min(values):.1f}, {max(values):.1f}]"


def keystroke_wpm(text: str, profile: str, seed: int) -> float:
    """WPM on the keystroke clock: transcription through the engine alone.

    This is the quantity the Dhakal 52 WPM population mean measures - words of
    produced text over typing time - so it is computed the way the engine's own
    test does: the text typed straight through, no typos, no revisions, chars/5
    over the summed motor intervals. Dividing the full pipeline's keystroke
    count by its motor clock instead would count backspaces and retyped
    characters as words, which is not any WPM convention.
    """
    engine = te.TimingEngine(profile=profile, seed=seed)
    engine.calibrate(text)
    timings = [engine.next_keystroke(char) for char in text]
    elapsed_ms = sum(t.iki_ms for t in timings[1:]) + timings[-1].dwell_ms
    return (len(text) / 5.0) / (elapsed_ms / 60_000.0)


def achieved(seeds: int, text: str) -> None:
    rows = {name: [] for name in (
        "keystroke_wpm", "wpm_active", "mean_dwell_ms", "mean_motor_iki_ms",
        "lag1_autocorrelation", "deletion_ratio", "rollover_rate",
    )}
    for seed in range(seeds):
        record = pipeline.generate(text, seed=seed)
        stats = record["statistics"]
        rows["keystroke_wpm"].append(keystroke_wpm(text, "average", seed))
        rows["wpm_active"].append(stats["wpm_active"])
        rows["mean_dwell_ms"].append(stats["mean_dwell_ms"])
        rows["mean_motor_iki_ms"].append(stats["mean_motor_iki_ms"])
        rows["lag1_autocorrelation"].append(stats["lag1_autocorrelation"])
        rows["deletion_ratio"].append(stats["deletion_ratio"])
        rows["rollover_rate"].append(
            stats["rollover_keystrokes"] / stats["keystrokes"]
        )

    profiles = {}
    for profile in ("slow", "fast"):
        profiles[profile] = statistics.mean(
            keystroke_wpm(text, profile, seed) for seed in range(seeds)
        )

    print(f"Achieved, {seeds} seeds, {len(text)} chars of English prose, defaults")
    print(f"{'-' * 68}")
    print(f"  {'average profile, keystroke clock':<34} "
          f"{interval(rows['keystroke_wpm'])} WPM")
    print(f"  {'slow / fast profile':<34} "
          f"{profiles['slow']:.1f} / {profiles['fast']:.1f} WPM")
    print(f"  {'wpm_active, whole pipeline':<34} {interval(rows['wpm_active'])}")
    print(f"  {'mean dwell':<34} {interval(rows['mean_dwell_ms'])} ms")
    print(f"  {'mean motor interval':<34} {interval(rows['mean_motor_iki_ms'])} ms")
    print(f"  {'lag-1 autocorrelation':<34} "
          f"{statistics.mean(rows['lag1_autocorrelation']):.3f} "
          f"[{min(rows['lag1_autocorrelation']):.3f}, "
          f"{max(rows['lag1_autocorrelation']):.3f}]")
    print(f"  {'deletion ratio':<34} "
          f"{statistics.mean(rows['deletion_ratio']):.3f} "
          f"[{min(rows['deletion_ratio']):.3f}, "
          f"{max(rows['deletion_ratio']):.3f}]")
    print(f"  {'rollover':<34} "
          f"{100 * statistics.mean(rows['rollover_rate']):.1f}% of keystrokes")
    print()


def tracking(seeds: int) -> None:
    print(f"Autocorrelation tracking, {seeds} seeds per cell")
    print(f"{'-' * 68}")
    header = f"  {'target':<8}" + "".join(f"{name:>24}" for name in TEXT_KINDS)
    print(header)
    for target in TARGETS:
        cells: List[str] = []
        for text in TEXT_KINDS.values():
            values = [
                pipeline.generate(
                    text, seed=seed, target_autocorrelation=target
                )["statistics"]["lag1_autocorrelation"]
                for seed in range(seeds)
            ]
            cells.append(f"{statistics.mean(values):>24.3f}")
        print(f"  {target:<8.2f}" + "".join(cells))
    print()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[1])
    parser.add_argument("--seeds", type=int, default=DEFAULT_SEEDS)
    parser.add_argument(
        "--repeat", type=int, default=3,
        help="how many times to repeat the prose sample (default 3)",
    )
    parser.add_argument(
        "--skip-tracking", action="store_true",
        help="only print the Achieved table",
    )
    args = parser.parse_args(argv)

    if args.seeds < 1:
        parser.error("--seeds must be at least 1")

    print(f"TypeTrace benchmark - numpy {getattr(te.np, '__version__', '?')}, "
          f"python {sys.version.split()[0]}")
    print()
    achieved(args.seeds, PROSE * args.repeat)
    if not args.skip_tracking:
        tracking(max(args.seeds // 4, 4))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
