# TypeTrace

Generate synthetic typing records from text, inspect the writing process, and
optionally replay it into Google Docs or a desktop editor. Records include key
timings, pauses, corrected typos, revisions, and session breaks. JSON output is
marked as synthetic research data.

## Windows app

Download **TypeTrace.exe** from [Releases](https://github.com/ahscolan2/typepyyy/releases/latest).
Open it, enter your text, and choose a writing setup. Python is included.

| Setup | Behavior |
| --- | --- |
| Preview only | Generate and save a record without live typing; the default |
| Confirmed Google Docs | The paragraph settings confirmed in a real Google Doc |
| Slower writing | Half the replay speed of the confirmed setup |
| Writing with breaks | Slower typing, with breaks after roughly 240 characters |
| Quick editor check | The short punctuation and correction sample tested in Google Docs |

Selecting a setup keeps your text. **Use example text** loads its sample.
For live typing, click **Generate and type…**, then focus your editor during the
five-second countdown. **Esc**, **Stop replay**, or switching windows stops it.
The slower setups are provided for you to tune in your editor.

The app types into your existing browser or editor. Open Google Docs in your
normal browser to use your existing login. See [build instructions](docs/windows-build.md)
to compile the EXE and [live-test results](docs/live-testing.md) for what was checked.

## Install

Python 3.10+ is required. Run these commands from this repository:

```sh
python -m venv .venv
```

Activate the environment:

- Windows PowerShell: `.venv\Scripts\Activate.ps1`
- macOS/Linux: `source .venv/bin/activate`

```sh
python -m pip install -e .
```

The app uses NumPy for timing and wcwidth for readable replay layout.
Editor replay uses [optional dependencies](docs/replay.md).

## Generate a record

```sh
typetrace --text "Hello world. This is a test." --seed 42
typetrace --text "@essay.txt" --profile fast --seed 42 --output record.json
```

Input files must be UTF-8 plain text; export Word or PDF content to text first.
Quote `"@essay.txt"` so the command also works in PowerShell. A seed reproduces a
record when the text, options, generator, and dependency versions are the same.
Unseeded runs store their chosen seed in the record.

Inspect a readable replay instead of JSON:

```sh
typetrace --text "@essay.txt" --seed 42 --format replay
```

`replay` groups ordinary typing and shows typos, corrections, pauses, and revisions.
`replay-full` shows every keystroke. Neither format opens an editor.

Useful options:

| Option | Use |
| --- | --- |
| `--profile slow`, `average`, or `fast` | Change modeled typing speed; default `average` |
| `--typo-model rich` | Add transpositions, repeated letters, and other corrected errors |
| `--session-chars 500` | Insert session boundaries in a short sample |
| `--output PATH` | Save output; add `--force` to replace an existing file |
| `--verbose` | Print statistics to stderr |
| `--help` | List all options and defaults |

Use `--text '\@name'` for literal text starting with `@`.
All examples also work with `python main.py` in place of `typetrace`.

## Desktop app

```sh
typetrace-gui
```

Or run `python gui.py` from the checkout. Enter text or load a file, adjust the
parameters, generate a preview, and save it. The app also exposes editor replay
controls. It requires Tk: `python -m tkinter` checks whether your Python
installation includes it. If that fails, install the Tk support supplied by your
Python distributor or operating system.

## Type into an editor

Start with a new, blank document. Replay sends real editing actions;
corrections use Backspace. Google Docs replay moves to the end of the document
(`Control+End` / `Meta+ArrowDown`) and appends; desktop replay types at the
current focused cursor. Keep the target focused and avoid typing during the run.

Google Docs:

```sh
python -m pip install -e ".[docs]"
python -m playwright install chromium
typetrace --text "Hello world." --seed 42 -o record.json --emit docs --doc-id YOUR_DOCUMENT_ID
```

Browser replay launches with the Chromium sandbox explicitly enabled. If Google
rejects automated browser sign-in, log in interactively in a visible browser or
use desktop replay into your normal browser window.

Desktop editor such as Word or Notepad:

```sh
python -m pip install -e ".[desktop]"
typetrace --text "Hello world." --seed 42 -o record.json --force --emit desktop
```

Desktop replay gives you five seconds to focus the editor; **Esc** stops it.
The GUI also provides **Stop replay**, which stops typing while preserving the
generated record to save.

See [editor replay](docs/replay.md) for browser login, timing controls, and
troubleshooting. Automated tests verify event contracts; editor features such
as autocorrect, smart quotes, and automatic lists can change live results.

## Reference and development

- [Record format](docs/record-format.md): schema, timing fields, and statistics.
- [Model](docs/model.md): what the parameters control and how to benchmark them.
- [Development and testing](docs/development.md): checks and editor test procedure.
