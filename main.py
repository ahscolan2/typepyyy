"""
Project TypeTrace - Command line interface.

Generates synthetic writing-process datasets: keystroke-level records of how a
piece of text could plausibly have been typed, for training and evaluating
detectors of machine-generated writing.

This tool writes data files. Optionally (--emit) it can also replay a finished
record into a real document editor owned by the user - a Google Doc in the
user's own browser profile, or the focused desktop window - so researchers can
study what those editors record. That replay is an optional extra with its own
dependencies (playwright or pynput); the generator itself needs only numpy.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

import macro_scripter as ms
import replay
import pipeline
import timing_engine as te

# Every record carries this so generated data can always be identified as
# synthetic, wherever it ends up.
WATERMARK = {
    "generated_by": "TypeTrace-Research",
    "purpose": "detection_training",
    "synthetic_research_data": True,
    "schema_version": 2,
}


def load_text(text_arg: str) -> str:
    """Return the literal argument, or the contents of @path.

    A leading '@' means "read from this file". To pass literal text that starts
    with '@', prefix it with a backslash.
    """
    if text_arg.startswith("\\@"):
        return text_arg[1:]
    if text_arg.startswith("@"):
        path = Path(text_arg[1:])
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise FileNotFoundError(f"text file not found: {path}") from None
        except UnicodeDecodeError as exc:
            raise ValueError(
                f"text file {path} is not valid UTF-8: {exc}"
            ) from None
    return text_arg


def generate_full_output(
    text: str,
    profile: str = "average",
    seed: Optional[int] = None,
    typo_rate: float = ms.TYPO_RATE,
    r_burst_probability: float = ms.R_BURST_PROBABILITY,
    session_chars: Optional[int] = None,
    target_autocorrelation: Optional[float] = None,
    structural_revision_rate: float = ms.STRUCTURAL_REVISION_RATE,
    fatigue_rate: float = te.FATIGUE_RATE,
    warmup_strength: float = te.WARMUP_STRENGTH,
    familiarity_boost: float = te.FAMILIARITY_BOOST,
) -> dict:
    """Generate one watermarked synthetic record for `text`."""
    record = pipeline.generate(
        text=text,
        profile=profile,
        seed=seed,
        typo_rate=typo_rate,
        r_burst_probability=r_burst_probability,
        session_chars=session_chars,
        target_autocorrelation=target_autocorrelation,
        structural_revision_rate=structural_revision_rate,
        fatigue_rate=fatigue_rate,
        warmup_strength=warmup_strength,
        familiarity_boost=familiarity_boost,
    )
    return {**WATERMARK, **record}


def render_record(record: dict, output_format: str) -> str:
    """Serialise `record` in the requested output format."""
    if output_format == "json":
        return json.dumps(record, indent=2, ensure_ascii=False) + "\n"
    if output_format == "replay":
        return replay.render(record) + "\n"
    if output_format == "replay-full":
        return replay.render(record, full=True) + "\n"
    raise ValueError(f"unknown output format {output_format!r}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="typetrace",
        description=(
            "Generate synthetic writing-process data (keystroke timings, "
            "pauses, typos, revisions) for detector training."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py --text "Hello world."
  python main.py --text @essay.txt --profile fast --seed 42
  python main.py --text @essay.txt --output record.json
  python main.py --text @essay.txt --emit docs --doc-id 1AbCdEfGh
  python main.py --text @essay.txt --emit desktop
        """,
    )
    parser.add_argument(
        "--text", "-t", required=True,
        help="Text to simulate, or @file.txt to read it from a file",
    )
    parser.add_argument(
        "--profile", "-p", choices=["slow", "average", "fast"], default="average",
        help="Typing speed profile (default: average, ~52 WPM)",
    )
    parser.add_argument(
        "--seed", "-s", type=int, default=None,
        help="Random seed; the same seed reproduces the same record exactly",
    )
    parser.add_argument(
        "--format", "-F", choices=["json", "replay", "replay-full"],
        default="json",
        help=(
            "json: the machine-readable record. replay: a readable account of "
            "the writing process. replay-full: the same with one line per "
            "keystroke (default: json)"
        ),
    )
    parser.add_argument(
        "--output", "-o", default=None,
        help="Write here instead of stdout",
    )
    parser.add_argument(
        "--force", "-f", action="store_true",
        help="Overwrite the output file if it already exists",
    )
    parser.add_argument(
        "--typo-rate", type=float, default=ms.TYPO_RATE,
        help=f"Per-character typo probability (default: {ms.TYPO_RATE})",
    )
    parser.add_argument(
        "--r-burst-probability", type=float, default=ms.R_BURST_PROBABILITY,
        help=(
            "Probability a burst ends in a revision "
            f"(default: {ms.R_BURST_PROBABILITY})"
        ),
    )
    parser.add_argument(
        "--structural-revision-rate", type=float,
        default=ms.STRUCTURAL_REVISION_RATE,
        help=(
            "Probability at each completed sentence of deleting the whole "
            "sentence - possibly across burst and paragraph boundaries - and "
            f"retyping it (default: {ms.STRUCTURAL_REVISION_RATE}; 0 disables)"
        ),
    )
    parser.add_argument(
        "--session-chars", type=int, default=None,
        help=(
            "Force a writing session to end after this many characters. "
            "By default a session runs 20-90 minutes of composition, so "
            "short texts contain no session gaps."
        ),
    )
    parser.add_argument(
        "--target-autocorrelation", type=float, default=None,
        help=(
            "Target lag-1 autocorrelation of the motor inter-key intervals "
            "(default: 0.35). Must be below 0.9."
        ),
    )
    parser.add_argument(
        "--fatigue-rate", type=float, default=te.FATIGUE_RATE,
        help=(
            "Fraction per ten minutes of active typing by which motor "
            "intervals inflate with fatigue "
            f"(default: {te.FATIGUE_RATE}; 0 disables)"
        ),
    )
    parser.add_argument(
        "--warmup-strength", type=float, default=te.WARMUP_STRENGTH,
        help=(
            "Initial fractional slowness of motor intervals, decaying over "
            f"roughly the first minute (default: {te.WARMUP_STRENGTH}; "
            "0 disables)"
        ),
    )
    parser.add_argument(
        "--familiarity-boost", type=float, default=te.FAMILIARITY_BOOST,
        help=(
            "Speedup on digraphs already typed in this document "
            f"(default: {te.FAMILIARITY_BOOST}; 0 disables)"
        ),
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Print a summary to stderr",
    )
    parser.add_argument(
        "--emit", choices=["docs", "desktop"], default=None,
        help=(
            "After writing the record, replay it live: 'docs' types it into "
            "the Google Doc named by --doc-id, 'desktop' types it into the "
            "focused window of this machine. Optional feature; needs the "
            "matching extra (pip install '.[docs]' or '.[desktop]'). "
            "Default: off."
        ),
    )
    parser.add_argument(
        "--doc-id", default=None,
        help="Google Docs document ID; required with --emit docs",
    )
    parser.add_argument(
        "--emit-speed", type=float, default=1.0,
        help="Playback speed multiplier for --emit (default: 1.0)",
    )
    parser.add_argument(
        "--emit-max-gap-s", type=float, default=None,
        help=(
            "Shorten silences longer than this to exactly this many seconds "
            "during --emit (default: keep the record's faithful timing)"
        ),
    )
    parser.add_argument(
        "--headless", action="store_true",
        help=(
            "Run the browser without a window for --emit docs. Logging into "
            "Google needs a visible window, so run once without this flag."
        ),
    )
    parser.add_argument(
        "--browser-profile", default=".typetrace-browser-profile",
        help=(
            "Persistent browser profile directory for --emit docs, so the "
            "Google login survives between runs (default: "
            ".typetrace-browser-profile)"
        ),
    )
    return parser


def emit_record(record: dict, args: argparse.Namespace) -> dict:
    """Replay `record` into a live editor, per the --emit CLI options.

    The emitters and their dependencies (playwright for Google Docs, pynput
    for the desktop) are optional, so they are imported here, only when
    emission was actually requested. A missing dependency comes back as an
    ImportError telling the user what to pip install.
    """
    if args.emit == "docs":
        if not args.doc_id:
            raise ValueError("--doc-id is required with --emit docs")
        import docs_emitter

        return docs_emitter.emit_to_google_docs(
            record,
            doc_id=args.doc_id,
            speed=args.emit_speed,
            max_gap_s=args.emit_max_gap_s,
            headless=args.headless,
            profile_dir=args.browser_profile,
        )
    import desktop_emitter

    return desktop_emitter.emit_to_desktop(
        record,
        speed=args.emit_speed,
        max_gap_s=args.emit_max_gap_s,
    )


def main(argv: Optional[list] = None) -> int:
    # Every byte of the record can reach stdout (the JSON is dumped with
    # ensure_ascii=False and replay text passes through), so pin stdout to
    # UTF-8 before anything is written. A cp1252 console would otherwise die
    # mid-write with a charmap error and lose the whole record. Only the
    # encoding changes: newline translation stays as-is, and errors stay
    # strict because UTF-8 encodes everything. stderr keeps reporting to the
    # console as before and the -o path is already explicit UTF-8.
    reconfigure_stdout = getattr(sys.stdout, "reconfigure", None)
    if reconfigure_stdout is not None:
        try:
            reconfigure_stdout(encoding="utf-8")
        except (ValueError, OSError):
            pass  # an exotic stream (closed, wrapped): nothing we can pin
    args = build_parser().parse_args(argv)

    try:
        if args.emit == "docs" and not args.doc_id:
            raise ValueError("--doc-id is required with --emit docs")

        text = load_text(args.text)

        if not text:
            raise ValueError("input text is empty; nothing to generate")

        record = generate_full_output(
            text=text,
            profile=args.profile,
            seed=args.seed,
            typo_rate=args.typo_rate,
            r_burst_probability=args.r_burst_probability,
            session_chars=args.session_chars,
            target_autocorrelation=args.target_autocorrelation,
            structural_revision_rate=args.structural_revision_rate,
            fatigue_rate=args.fatigue_rate,
            warmup_strength=args.warmup_strength,
            familiarity_boost=args.familiarity_boost,
        )

        payload = render_record(record, args.format)

        if args.output:
            out_path = Path(args.output)
            if out_path.exists() and not args.force:
                raise FileExistsError(
                    f"{out_path} already exists; pass --force to overwrite"
                )
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(payload, encoding="utf-8")
            print(f"Wrote {out_path}", file=sys.stderr)
        else:
            sys.stdout.write(payload)

        if args.verbose:
            stats = record["statistics"]
            print(
                f"{record['metadata']['input_chars']} chars, "
                f"{stats['keystrokes']} keystrokes, "
                f"{stats['backspaces']} backspaces, "
                f"{stats['session_gaps']} session gaps, "
                f"{stats['wpm_active']:.1f} WPM active, "
                f"lag-1 autocorrelation {stats['lag1_autocorrelation']:.3f}",
                file=sys.stderr,
            )

        if args.emit:
            summary = emit_record(record, args)
            print(f"emission: {summary}", file=sys.stderr)

    except (
        FileNotFoundError, FileExistsError, ValueError, TypeError, OSError,
        ImportError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130

    return 0


if __name__ == "__main__":
    sys.exit(main())
