"""
Project TypeTrace - Desktop application

A window over the same generator the CLI drives. Every parameter the CLI
accepts is here, and the result can be viewed as the readable writing replay
or as the raw JSON record, and saved either way.

Built on tkinter so the application has no dependency the library does not
already have. Launch it with:

    python gui.py
"""

import json
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable, Dict, Optional

import macro_scripter as ms
import replay
from main import generate_full_output

WINDOW_TITLE = "TypeTrace - synthetic writing process generator"
DEFAULT_TEXT = (
    "Academic integrity is essential to higher education. Students must "
    "produce original work, and institutions need reliable ways to evaluate "
    "how that work was produced."
)

# Poll interval for results coming back from the worker thread, ms. Short
# enough to feel immediate, long enough not to spin.
POLL_MS = 60

MONO = ("Menlo", 11)


class ParameterRow:
    """One labelled entry with optional validation and a placeholder.

    Kept as a small class rather than a tuple because every parameter needs the
    same three things - a label, a widget, and a way to turn its text back into
    a typed value or a clear error.
    """

    def __init__(
        self,
        parent: tk.Widget,
        row: int,
        label: str,
        default: str = "",
        hint: str = "",
        parse: Optional[Callable[[str], Any]] = None,
    ):
        self.label = label
        self.parse = parse
        self.variable = tk.StringVar(value=default)

        ttk.Label(parent, text=label).grid(
            row=row, column=0, sticky="w", padx=(0, 8), pady=3
        )
        entry = ttk.Entry(parent, textvariable=self.variable, width=14)
        entry.grid(row=row, column=1, sticky="w", pady=3)
        if hint:
            ttk.Label(parent, text=hint, foreground="grey").grid(
                row=row, column=2, sticky="w", padx=(8, 0), pady=3
            )

    def value(self) -> Any:
        """The parsed value, or None if the field is blank.

        Blank means "use the generator's own default", which is why an unset
        seed and an unset session length do not need separate checkboxes.
        """
        raw = self.variable.get().strip()
        if not raw:
            return None
        if self.parse is None:
            return raw
        try:
            return self.parse(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{self.label}: {exc}") from None


class Application(ttk.Frame):
    def __init__(self, master: tk.Tk):
        super().__init__(master, padding=10)
        self.grid(row=0, column=0, sticky="nsew")
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        # Results arrive from a worker thread; tkinter is not thread-safe, so
        # they are queued and picked up by the main loop rather than touched
        # from the thread itself.
        self._results: "queue.Queue[tuple]" = queue.Queue()
        self._record: Optional[Dict[str, Any]] = None
        self._busy = False

        self._build_input()
        self._build_parameters()
        self._build_output()
        self._build_status()

        self.after(POLL_MS, self._drain)

    # -- layout --------------------------------------------------------------

    def _build_input(self) -> None:
        frame = ttk.LabelFrame(self, text="Text", padding=8)
        frame.grid(row=0, column=0, sticky="ew")
        frame.columnconfigure(0, weight=1)

        self.text_input = tk.Text(frame, height=5, wrap="word", font=MONO)
        self.text_input.grid(row=0, column=0, sticky="ew")
        self.text_input.insert("1.0", DEFAULT_TEXT)

        scroll = ttk.Scrollbar(frame, command=self.text_input.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.text_input.configure(yscrollcommand=scroll.set)

        buttons = ttk.Frame(frame)
        buttons.grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Button(buttons, text="Load from file…", command=self._load_file).pack(
            side="left"
        )
        ttk.Button(buttons, text="Clear", command=self._clear_text).pack(
            side="left", padx=(6, 0)
        )

    def _build_parameters(self) -> None:
        frame = ttk.LabelFrame(self, text="Parameters", padding=8)
        frame.grid(row=1, column=0, sticky="ew", pady=(10, 0))

        grid = ttk.Frame(frame)
        grid.grid(row=0, column=0, sticky="w")

        ttk.Label(grid, text="Profile").grid(
            row=0, column=0, sticky="w", padx=(0, 8), pady=3
        )
        self.profile = tk.StringVar(value="average")
        ttk.Combobox(
            grid,
            textvariable=self.profile,
            values=["slow", "average", "fast"],
            state="readonly",
            width=12,
        ).grid(row=0, column=1, sticky="w", pady=3)
        ttk.Label(grid, text="average is ~52 WPM", foreground="grey").grid(
            row=0, column=2, sticky="w", padx=(8, 0), pady=3
        )

        self.seed = ParameterRow(
            grid, 1, "Seed", "42", "blank for a random run", int
        )
        self.typo_rate = ParameterRow(
            grid, 2, "Typo rate", str(ms.TYPO_RATE), "per character", float
        )
        self.r_burst = ParameterRow(
            grid,
            3,
            "Revision probability",
            str(ms.R_BURST_PROBABILITY),
            "chance a burst ends in a rewrite",
            float,
        )
        self.session_chars = ParameterRow(
            grid,
            4,
            "Session length",
            "",
            "characters; blank for 20-90 real minutes",
            int,
        )
        self.autocorrelation = ParameterRow(
            grid,
            5,
            "Rhythm autocorrelation",
            "",
            "blank for 0.35; must be under 0.9",
            float,
        )

        actions = ttk.Frame(frame)
        actions.grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.generate_button = ttk.Button(
            actions, text="Generate", command=self._generate
        )
        self.generate_button.pack(side="left")
        ttk.Button(actions, text="Save…", command=self._save).pack(
            side="left", padx=(6, 0)
        )

    def _build_output(self) -> None:
        frame = ttk.LabelFrame(self, text="Result", padding=8)
        frame.grid(row=2, column=0, sticky="nsew", pady=(10, 0))
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)

        chooser = ttk.Frame(frame)
        chooser.grid(row=0, column=0, sticky="w", pady=(0, 6))
        self.view = tk.StringVar(value="replay")
        for label, value in (
            ("Writing replay", "replay"),
            ("Every keystroke", "replay-full"),
            ("JSON record", "json"),
        ):
            ttk.Radiobutton(
                chooser,
                text=label,
                value=value,
                variable=self.view,
                command=self._refresh_view,
            ).pack(side="left", padx=(0, 12))

        self.output = tk.Text(frame, wrap="none", font=MONO, height=22)
        self.output.grid(row=1, column=0, sticky="nsew")
        self.output.configure(state="disabled")

        y_scroll = ttk.Scrollbar(frame, command=self.output.yview)
        y_scroll.grid(row=1, column=1, sticky="ns")
        x_scroll = ttk.Scrollbar(
            frame, orient="horizontal", command=self.output.xview
        )
        x_scroll.grid(row=2, column=0, sticky="ew")
        self.output.configure(
            yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set
        )

    def _build_status(self) -> None:
        self.status = tk.StringVar(value="Ready.")
        ttk.Label(self, textvariable=self.status, foreground="grey").grid(
            row=3, column=0, sticky="w", pady=(8, 0)
        )

    # -- actions -------------------------------------------------------------

    def _current_text(self) -> str:
        # Text.get appends a trailing newline the user never typed; drop it so
        # the generated record matches what is on screen.
        return self.text_input.get("1.0", "end-1c")

    def _clear_text(self) -> None:
        self.text_input.delete("1.0", "end")

    def _load_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Open a text file",
            filetypes=[("Text files", "*.txt *.md"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            content = Path(path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            messagebox.showerror("Could not read the file", str(exc))
            return
        self.text_input.delete("1.0", "end")
        self.text_input.insert("1.0", content)
        self.status.set(f"Loaded {len(content)} characters from {Path(path).name}")

    def _generate(self) -> None:
        if self._busy:
            return

        text = self._current_text()
        if not text.strip():
            messagebox.showwarning("Nothing to generate", "Enter some text first.")
            return

        try:
            kwargs = {
                "profile": self.profile.get(),
                "seed": self.seed.value(),
                "session_chars": self.session_chars.value(),
                "target_autocorrelation": self.autocorrelation.value(),
            }
            # These two have real defaults rather than None, so a blank field
            # means "leave it alone" rather than "pass None".
            typo_rate = self.typo_rate.value()
            if typo_rate is not None:
                kwargs["typo_rate"] = typo_rate
            r_burst = self.r_burst.value()
            if r_burst is not None:
                kwargs["r_burst_probability"] = r_burst
        except ValueError as exc:
            messagebox.showerror("Check the parameters", str(exc))
            return

        self._busy = True
        self.generate_button.configure(state="disabled")
        self.status.set(f"Generating {len(text)} characters…")

        # A long document takes a noticeable moment, and a frozen window looks
        # like a crash. The work happens off the UI thread and reports back
        # through the queue.
        thread = threading.Thread(
            target=self._worker, args=(text, kwargs), daemon=True
        )
        thread.start()

    def _worker(self, text: str, kwargs: Dict[str, Any]) -> None:
        try:
            record = generate_full_output(text=text, **kwargs)
        except Exception as exc:  # surfaced in the dialog, not swallowed
            self._results.put(("error", exc))
        else:
            self._results.put(("ok", record))

    def _drain(self) -> None:
        try:
            while True:
                status, payload = self._results.get_nowait()
                self._busy = False
                self.generate_button.configure(state="normal")
                if status == "error":
                    self.status.set("Generation failed.")
                    messagebox.showerror(
                        "Generation failed",
                        f"{type(payload).__name__}: {payload}",
                    )
                    continue
                self._record = payload
                self._refresh_view()
                stats = payload["statistics"]
                self.status.set(
                    f"{payload['metadata']['input_chars']} characters, "
                    f"{stats['keystrokes']} keystrokes, "
                    f"{stats['backspaces']} backspaces, "
                    f"{stats['session_gaps']} session gaps, "
                    f"{stats['wpm_active']:.1f} WPM"
                )
        except queue.Empty:
            pass
        self.after(POLL_MS, self._drain)

    def _rendered(self) -> str:
        if self._record is None:
            return ""
        view = self.view.get()
        if view == "json":
            return json.dumps(self._record, indent=2, ensure_ascii=False)
        return replay.render(self._record, full=(view == "replay-full"))

    def _refresh_view(self) -> None:
        self.output.configure(state="normal")
        self.output.delete("1.0", "end")
        self.output.insert("1.0", self._rendered())
        self.output.configure(state="disabled")

    def _save(self) -> None:
        if self._record is None:
            messagebox.showinfo("Nothing to save", "Generate a record first.")
            return

        is_json = self.view.get() == "json"
        path = filedialog.asksaveasfilename(
            title="Save result",
            defaultextension=".json" if is_json else ".txt",
            filetypes=(
                [("JSON", "*.json")] if is_json else [("Text", "*.txt")]
            )
            + [("All files", "*.*")],
        )
        if not path:
            return
        try:
            Path(path).write_text(self._rendered() + "\n", encoding="utf-8")
        except OSError as exc:
            messagebox.showerror("Could not save", str(exc))
            return
        self.status.set(f"Saved to {Path(path).name}")


def main() -> int:
    root = tk.Tk()
    root.title(WINDOW_TITLE)
    root.minsize(900, 720)
    Application(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
