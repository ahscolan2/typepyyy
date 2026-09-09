# Live editor checks

Google Docs in a normal, logged-in Chrome window on Windows 11 accepted both
desktop replay samples. The user visually confirmed that the paragraph came
out correctly. These runs exercised real typing, mistakes, Backspace corrections,
punctuation, and pauses.

| Sample | Settings | Recorded result |
| --- | --- | --- |
| Short punctuation sample | average rhythm, seed 42, typo rate 0.06, replay speed 2.5 | 89 keystrokes, 5 backspaces, 178 dispatches; completed in 11.22 s |
| Community workshop paragraph | average rhythm, seed 23, typo rate 0.06, replay speed 2.5 | 1,021 keystrokes, 133 backspaces, 2,042 dispatches; completed in 123.02 s |

The **Confirmed Google Docs** setup preserves the paragraph settings. Its example
begins with two blank lines because the original test appended below the short
sample. Selecting a setup does not replace the user's text.

The **Slower writing** and **Writing with breaks** setups are variations for the
user to try.

## Packaged Windows app

On September 9, 2026, the one-file Windows EXE was opened outside the source
directory and exercised through its visible GUI. Preview generation and setup
selection worked; selecting a setup preserved the existing text and loading an
example required an explicit button click.

The **Quick editor check** typed the exact punctuation sample into a scratch
Notepad document: 178 key events in 11.2 seconds. Esc stopped another run after
125 events in 8.7 seconds, leaving a partial sentence. A subsequent run completed
with the exact sample again. The saved file was read back and reopened in Notepad;
the original, partial cancellation sample, and recovery sample persisted.

The frozen self-check also verifies dependency imports, offline reconstruction,
all GUI setups, and a Playwright driver round trip without a network request or
browser launch. It runs from a temporary directory with Python absent from PATH.

Automated browser login was rejected by Google in this environment. The verified
route uses desktop replay into the user's normal browser. The direct browser
backend and Microsoft Word have not been verified live.
