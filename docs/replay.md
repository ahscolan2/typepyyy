# Editor replay

`--emit` generates a record, writes the selected output, then sends the record's
editing actions to an editor. `--format replay` only renders a text explanation;
it does not type anything. The CLI currently generates from `--text` on each run,
rather than accepting a saved JSON record as input.

Use a blank document for the first test. Google Docs replay jumps to the end
of the document (`Control+End` / `Meta+ArrowDown`) and appends; desktop replay
types at the current focused cursor. Replay uses Backspace for corrections and
revisions. Existing selections, automatic lists, smart quotes, autocorrect, and
input methods can change the result.

Both emitters pre-validate the keystroke sequence before opening a browser or
touching the keyboard. If typos or revisions delete within a multi-codepoint
Unicode grapheme cluster (combining accents, emoji skin-tone modifiers, zero-width
joiner sequences, or regional indicator flags), replay rejects the run: editors
delete entire grapheme clusters on Backspace rather than single codepoints,
causing character drift. Unhandled control characters (except LF and Tab) are
likewise rejected. Disable typos and revisions or supply precomposed text if hit.

## Google Docs

Install the browser backend from the repository:

```sh
python -m pip install -e ".[docs]"
python -m playwright install chromium
```

Create a blank Google Doc that you can edit, then pass its ID or full URL:

```sh
typetrace --text "Hello world. This is a replay test." --seed 42 -o record.json --emit docs --doc-id YOUR_DOCUMENT_ID
```

The browser launches with the Chromium sandbox explicitly enabled
(`chromium_sandbox=True`). The window opens visibly by default so you can sign in
when prompted. The default wait for the editor is five minutes; increase it with
`--editor-timeout-s 600` if needed. The profile is stored in
`.typetrace-browser-profile/` relative to the working directory and reused on
later runs. It contains login state, so keep it local; the default directory is
ignored by Git.

The Windows GUI defaults to desktop replay. Its separate browser backend uses
installed Chrome and stores its profile under `%LOCALAPPDATA%\TypeTrace\browser_profile`.

| Option | Use |
| --- | --- |
| `--doc-id ID_OR_URL` | Target document; requires edit access |
| `--browser-channel chromium` | Bundled Chromium, the default |
| `--browser-channel chrome` | Installed Google Chrome |
| `--browser-channel msedge` | Installed Microsoft Edge |
| `--browser-profile PATH` | Use a separate persistent profile directory |
| `--editor-timeout-s SECONDS` | Positive time allowed for login/editor readiness |
| `--headless` | Hide the browser; use after establishing login visibly |

Google may reject automated browser sign-in in some environments ("This browser
or app may not be secure"). If this occurs, try installed Chrome or Edge with the
matching `--browser-channel` flag, or switch to desktop replay into your normal
browser window. Use a dedicated profile directory, and close any existing
TypeTrace browser instance before reusing the same profile. Headless mode cannot
be used for initial interactive login.

Keep the editing surface active during replay. **Ctrl+C** in the terminal,
clicking **Stop replay** in the GUI, or closing the replay browser stops the run.
When stopped early or when emission fails, the generated record is preserved and
can still be saved. Afterward, reopen the document to check the saved content.
Editor version history is useful for inspection but is not a keystroke log and
need not expose the timings stored in TypeTrace's JSON.

## Desktop editors

```sh
python -m pip install -e ".[desktop]"
typetrace --text "Hello world." --seed 42 -o record.json --emit desktop
```

Open a blank document in Word, Notepad, or another editor. During the five-second
countdown, click its text area and release any modifier keys. **Esc** stops replay
during the countdown or typing. The GUI also provides **Stop**.
On Windows, switching to another foreground window also stops replay.

The backend uses the operating system's keyboard input. It needs a desktop
session and input permissions; support depends on your OS, keyboard layout, and
editor. The key mapping assumes US QWERTY. Unicode characters that generate
successfully may still be unsupported by the desktop input backend. Test a short
sample containing your actual punctuation and language before a long replay.

## Timing

The default speed is `1.0`. `--emit-speed 2` halves the scheduled timings,
including key holds. Values between zero and one slow playback down.

`--emit-max-gap-s 5` caps silence between keys at five seconds **before** applying
the speed multiplier. At speed 2, that cap becomes 2.5 seconds in playback.
Capping gaps does not independently shorten key holds. The JSON record keeps
its original timings regardless of playback settings.

For a quick test, add `--emit-speed 3 --emit-max-gap-s 1`. Very high speeds may
overwhelm the editor or backend. Dispatch runs on the wall clock, so scheduler
and browser delays limit timing precision.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Output file already exists | Choose another path or add `--force` |
| Missing Playwright browser | Run `python -m playwright install chromium` in the same environment |
| Google sign-in rejected | Use installed Chrome/Edge via `--browser-channel`, or desktop replay |
| Unicode cluster or control character rejected | Live replay rejects deletions inside combining marks or emoji clusters, and control characters except LF/Tab. Remove clusters or set typo/revision rates to 0. |
| Editor never appears | Complete login, check document permissions, and increase the editor timeout |
| Browser profile is in use | Close the other browser using it or choose another profile directory |
| Typed text differs | Start blank; check focus, selection, autocorrect, automatic formatting, and keyboard layout |
| Editor replay fails after generation | The generated record was written to disk or kept in GUI memory first; inspect that saved record and the reported error |

The returned summary reports dispatch counts, elapsed time, and whether the run
stopped early. See [live-test results](live-testing.md) and compare your saved
document with `target_text` when trying a new editor.
