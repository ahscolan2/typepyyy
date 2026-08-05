# Legacy browser path

This folder holds the original Aletheia browser-injection code, preserved
verbatim. It is **not part of the installed package** — nothing in the current
codebase imports it, the CLI has no path into it, and it is excluded from the
test, lint and packaging runs.

It is kept because it is the repository owner's work, and the 2.0 rewrite had no
standing to delete it.

## Contents

| File | What it is |
|---|---|
| `cdp_emitter.py` | The CDP emitter, byte-identical to the initial commit. Drives a Google Docs document over the Chrome DevTools Protocol. |
| `original_main.py` | The **original** `main.py`, byte-identical to the initial commit. Contains the browser wiring the rewrite removed: `execute_mode()`, the `--mode` and `--doc-id` arguments, and the `CDPEmitter` import. |
| `requirements.txt` | The dependency this path needs and the current package does not: `playwright>=1.40.0`. |

Both source files are verifiable against history:

```bash
git show 12abed5:cdp_emitter.py | diff - legacy_browser_path/cdp_emitter.py
git show 12abed5:main.py        | diff - legacy_browser_path/original_main.py
```

## What the browser path did

- `CDPEmitter.navigate_to_docs(doc_id)` opened
  `https://docs.google.com/document/d/<id>/edit`, waited for the
  `.kix-appview-editor` element and clicked into it.
- `CDPEmitter.execute_script()` dispatched `Input.dispatchKeyEvent` calls into
  that live document, replaying a macro script with human-like key timing.
- `original_main.py:execute_mode()` was the entry point, selected by
  `--mode immediate` (or `realistic`) together with `--doc-id`.

## Why it is separated

The rest of the repository generates data files. It reads a piece of text and
writes a record of how that text could plausibly have been typed. Nothing else
opens a browser, touches a document, or makes a network request, and the test
suite asserts that.

This code is the exception, and it differs in kind rather than degree. The
maintainer who did the 2.0 rewrite declined to repair or re-integrate it,
because three of its behaviours have no meaning for a dataset and only have
meaning for a live document's revision history: multi-hour session gaps, typing
a character and then correcting it when the target text is already known, and
the deliberate choice of `Input.dispatchKeyEvent` over Playwright's
`keyboard.type()`. Together those make a real document's version history look
like the text was composed by hand over several sittings.

The repository owner's position is that this is academic integrity research and
that studying the artefact requires being able to produce it. Both positions are
recorded here rather than settled by deleting anyone's code. The code is intact
either way, and what follows is the state it is actually in.

## State of this code

It has never worked end to end. These defects predate the rewrite and are
unfixed:

- CDP key events are sent without `windowsVirtualKeyCode`, so Backspace and
  Enter do not register in Chrome.
- A `char` event is emitted for Backspace, which is not a valid character event.
- Timestamps are passed as millisecond offsets; `Input.dispatchKeyEvent` expects
  seconds since an epoch.
- Shift is pressed for an uppercase letter and never released, so the state
  desynchronises after the first capital.
- `start()` leaks a Chromium process if page or CDP session creation fails.
- `execute_script()` uses a flat 150 ms per keystroke and never consults the
  timing engine, so the emitted rhythm is not the modelled one.

Note the last point in particular: the browser path and the timing model were
never actually connected.

## What changed in the main package

None of these have been restored, and the 2.0 modules do not provide them:

| Removed | From |
|---|---|
| `playwright>=1.40.0` | `requirements.txt`, `pyproject.toml` |
| `--mode dry-run/immediate/realistic` | `main.py` argument parser |
| `--doc-id` | `main.py` argument parser |
| `execute_mode()` | `main.py` |

`original_main.py` in this folder is the last version in which all four existed
together. It targets the 1.0 module API, which no longer exists: `MacroScripter.
generate_script()` returned a `(script, typo_corrections)` tuple, `TimingEngine.
generate_keystrokes()` existed, and the output record used a `micro_timings`
key. The current modules use a single event timeline instead — see the root
`README.md` for the 2.0 schema.

## One other removal, for the record

`error_models.py` originally mapped `"public"` to `"pubic"` in its homophone
substitution table. That was removed as unsuitable for a generated research
corpus, not for any of the reasons above. It is a single dictionary entry and is
recoverable from git history.

---

# What was not done

A record of the gaps left by the 2.0 rewrite, so nobody discovers them by being
surprised. Each entry says what the decision means in practice and what it costs.
None of these are hidden by the code: the statistics the generator reports are
the statistics it actually achieves.

### The browser path was not repaired or re-integrated

**Means:** the files above are preserved but unwired, with the defects listed.
**Upside:** the package has one job, no browser dependency, and a test suite that
can assert it touches nothing outside its own process.
**Downside:** the original end-to-end capability is gone. Anyone continuing it
starts from code that never worked, against a module API that has since changed.

### Deletion ratio sits at the bottom of the reported range

**Means:** roughly 0.10 of typed characters are later deleted. The literature
reports 0.10–0.30 for real composition.
**Upside:** `r_burst_probability` stays at the 0.20 that Leijten & Van Waes
report, so a cited parameter still means what it cites.
**Downside:** generated writing is tidier than real writing, and a detector
trained on it may under-weight revision as a signal. Raising
`r_burst_probability` to about 0.25 moves the aggregate mid-range at the cost of
that correspondence.

### Structural revision is not modelled

**Means:** revision deletes back into the burst just written and retypes it.
Deleting a sentence written ten minutes ago, reordering paragraphs, or rewriting
an argument does not happen.
**Upside:** the replay invariant is easy to guarantee and every revision is
local and explainable.
**Downside:** this is the direct cause of the deletion ratio above, and the
single largest fidelity gap in the model. Real composition revises at every
scale, not just the last few words.

### Not validated against real keystroke logs

**Means:** the model reproduces the summary statistics it was built from —
Salthouse digraph latencies, Dhakal dwell times and typing rate, Chenoweth &
Hayes burst lengths. It has never been compared against a corpus of real typing.
**Upside:** every parameter is traceable to a published figure rather than
fitted to something unexamined.
**Downside:** matching a handful of summary statistics is a weak guarantee. Real
keystroke data has structure this model does not attempt — per-word familiarity,
fatigue over an hour, learning within a document. Whether a detector trained
here transfers to real writing is untested and should not be assumed. This is
the most important open question for anyone continuing the work.

### English on a US QWERTY keyboard only

**Means:** key geometry, hand and finger assignments, the common-bigram list and
the neighbour-key typo map all assume one layout and one language.
**Upside:** the timing model uses a concrete physical keyboard rather than an
abstraction, which is what makes the digraph classes meaningful.
**Downside:** any other layout or language produces output that looks plausible
and is uncalibrated. Nothing warns you.

### Flat modules rather than a package

**Means:** the modules sit at the repository root instead of in an `aletheia/`
directory.
**Upside:** `python main.py` and `python gui.py` work from a checkout with no
install step.
**Downside:** the import namespace is global, and a module here can shadow a
third-party one of the same name.

### No batch or multi-record generation

**Means:** `batch_generator.py` was removed at the owner's request. With it went
labelled datasets, train/val/test splits, parallel generation and Parquet export.
**Upside:** far less surface area, no multiprocessing, no optional pyarrow
dependency.
**Downside:** there is no supported way to produce a labelled dataset. The CLI
generates one record per invocation; anything larger has to be scripted around
it. For a team training a detector this is the first thing to rebuild — it is in
git history at the commit before this one.

### The GUI was verified functionally, not visually

**Means:** every code path was driven programmatically — determinism, all three
views, unicode round-trip, parameter validation, error dialogs. Nobody has
looked at the window.
**Upside:** behaviour is covered by something repeatable rather than by
someone's memory of it looking fine.
**Downside:** layout problems — clipping, resize behaviour, spacing on another
display — would not have been caught. Run `python gui.py` once.

### Docker was removed

**Means:** the `Dockerfile`, `docker-compose.yml` and `.dockerignore` are gone at
the owner's request.
**Upside:** nothing to keep in step with the dependency list.
**Downside:** no pinned reproducible environment. The CI matrix on 3.10–3.13 is
the only guard that this runs somewhere other than one laptop.
