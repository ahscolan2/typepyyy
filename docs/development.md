# Development and testing

From the repository, with a virtual environment active:

```sh
python -m pip install -e ".[dev]"
python -m pytest -q
python -m ruff check .
python benchmark.py --seeds 4 --repeat 1 --skip-tracking
```

Statistical tests run by default. For a shorter iteration, use
`python -m pytest -q -m "not slow"`, then run the full suite before submitting a
model change. GUI tests need Tk and a display; skipped GUI tests do not establish
that the app works on that environment.

CI runs tests on Windows with Python 3.10–3.13 and macOS with Python 3.12. It also
installs the package, checks the console entry point, reconstructs a generated
record, smoke-tests the benchmark, and runs Ruff. Automated green test results
confirm timing logic, protocol contracts, and validation, but do not prove
live-editor correctness in Google Docs or Word. Local live-editor checks are
tracked and performed separately from automated CI checks.

## Editor smoke test

Use a disposable blank document. Repeat this sequence for each editor/backend you
change, at normal speed and a moderate accelerated speed:

1. Replay a short plain sentence with a fixed seed. Compare the resulting text
   with `target_text` in the saved record.
2. Include repeated letters, uppercase/lowercase transitions, quotes, shifted
   symbols, and paragraph breaks. Include Unicode used by the intended documents.
3. Raise the typo and revision rates to exercise Backspace and retyping. Use
   `--session-chars` with `--emit-max-gap-s` to exercise a session gap quickly.
4. Stop during the countdown, typing, and a gap. Check that typing stops and no
   keys remain held. Start another replay to check recovery.
5. For Google Docs, check a fresh login, reused profile, read-only document, and
   closed tab. Reopen the document after a successful run to check saved content.

Record the editor/browser version, OS, keyboard layout, generator revision,
options, and observed result. Distinguish key-dispatch success, visible text
correctness, and saved-document correctness. A local textarea test is useful for
input regressions but does not substitute for testing Google Docs or Word.

## Code map

| File | Responsibility |
| --- | --- |
| `main.py`, `gui.py` | CLI/GUI input, output, and replay controls |
| `macro_scripter.py`, `error_models.py` | Editing actions and corrected errors |
| `timing_engine.py` | Motor intervals and key holds |
| `pipeline.py` | Absolute timeline, reconstruction checks, statistics |
| `replay.py` | Readable replay rendering |
| `emit_common.py` | Replay scheduling shared by both backends |
| `docs_emitter.py`, `desktop_emitter.py` | Browser and OS key dispatch |
| `benchmark.py` | Statistical measurements on fixed samples |

Keep generated records in `datasets/` and browser login profiles out of commits.
If changing a schema or parameter, update the relevant reference page alongside
the code and tests.
