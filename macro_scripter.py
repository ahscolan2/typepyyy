"""
Project TypeTrace - Macro Scripter

Generates the high-level writing process: bursts of fluent typing separated by
pauses, typos that get corrected, revisions that delete and retype, and gaps
between writing sessions.

The output is a flat list of ScriptEvent operations that, replayed in order,
reproduce the target text exactly. That invariant is the point of this module:
everything downstream assumes `replay(generate_script(text)) == text`.

Based on Chenoweth & Hayes (P-bursts and R-bursts) and Leijten & Van Waes
(revision behaviour).
"""

import random
import unicodedata
from dataclasses import dataclass
from typing import Dict, List, Optional

# Operation names.
OP_TYPE = "TYPE"
OP_DELETE = "DELETE"
OP_PAUSE = "PAUSE"
OP_SESSION_GAP = "SESSION_GAP"

# Roles, recorded on each event so a downstream consumer can label keystrokes
# without having to re-derive intent from the operation sequence.
ROLE_TEXT = "text"
ROLE_TYPO = "typo"
ROLE_CORRECTION = "correction"
ROLE_REVISION_DELETE = "revision_delete"
ROLE_REVISION_RETYPE = "revision_retype"

# Chenoweth & Hayes burst lengths, in words.
P_BURST_RANGE = (8, 13)
R_BURST_RANGE = (3, 7)
R_BURST_PROBABILITY = 0.20

# Probability, rolled at each completed sentence, that the writer deletes the
# whole sentence back and retypes it verbatim. This is the sentence/paragraph
# scale of revision that the R-burst below does not cover: the deletion is
# measured from the sentence's start, so it reaches back across the boundary
# of the burst that produced it, and across a paragraph break when the
# sentence trails one. The retype emits the identical characters from the
# target text, so the replay invariant holds by construction. 0 disables it.
#
# 0.08 is a model choice, not a literature figure: the sources behind the
# other constants do not pin down a sentence-scale revision rate, and this is
# picked small enough that roughly one sentence in twelve is reworked.
STRUCTURAL_REVISION_RATE = 0.08

# When a structural revision fires and the previous sentence was the trailing
# sentence of the paragraph before this one, the chance the writer reaches
# back over the paragraph break to rework that sentence instead. Model choice.
STRUCTURAL_BACK_ACROSS_PARAGRAPH = 0.3

# Lognormal pause parameters (mu, sigma) on log-milliseconds. Medians are
# exp(mu): 90ms, 181ms, 493ms, 1097ms respectively. Verified in
# tests/test_macro_scripter.py::test_pause_medians.
PAUSE_WORD = (4.5, 0.4)
PAUSE_CLAUSE = (5.2, 0.5)
PAUSE_SENTENCE = (6.2, 0.6)
PAUSE_PARAGRAPH = (7.0, 0.7)

# How often a syntactic boundary inside a burst actually becomes a PAUSE op.
# Plain word boundaries are absent from the table and never pause: hesitation
# between words already lives in the inter-key variance of the timing engine,
# and pausing after nearly every word produced one pause every couple of
# seconds of output. Sentence boundaries pause sometimes, clause boundaries
# less often, and a paragraph break always pauses. Burst-end pauses are a
# separate mechanism (the P-burst/R-burst structure of Chenoweth & Hayes) and
# are unaffected. The probabilities are model choices, not literature figures:
# the sources fix the burst structure and the pause magnitudes, but say
# nothing about how often a boundary turns into a measurable pause.
BOUNDARY_PAUSE_PROBABILITIES = {
    "clause": 0.2,
    "sentence": 0.4,
    "paragraph": 1.0,
}

# Hard ceiling on any single recorded silence - thinking pause or session
# gap - in milliseconds. The product rule is that no fifteen-minute-plus
# silence appears in a record. The session-gap table below is expressed in
# minutes and drawn entirely below this ceiling, so the clamp is a guard
# rather than the thing that decides the value. The lognormal pause sampler
# reaches the ceiling only in its far tail.
MAX_SILENCE_MS = 900_000.0

# Reaction time before noticing and fixing a typo, ms.
TYPO_REACTION_RANGE = (300.0, 800.0)

# Pause before beginning a revision, and before retyping after one.
REVISION_PAUSE_RANGE = (400.0, 1500.0)

TYPO_RATE = 0.03

# Fraction of a completed R-burst that gets deleted and retyped.
#
# With R_BURST_PROBABILITY at the cited 0.20, this burst-local revision alone
# yields a deletion ratio of roughly 0.09; STRUCTURAL_REVISION_RATE adds the
# sentence-scale deletion on top, bringing the combined ratio into the
# 0.10-0.30 range reported for real composition. Raising r_burst_probability
# higher is not the way to get there: burst-local deletion is mechanically
# capped by the burst it revises.
REVISION_FRACTION_RANGE = (0.4, 1.0)

# A writing session runs 20-90 minutes. Converted to characters using a nominal
# composition rate, since this module works in characters rather than time.
SESSION_MINUTES_RANGE = (20.0, 90.0)
NOMINAL_COMPOSITION_CPM = 160.0

# Gaps between sessions, in minutes. A break long enough that the typist's
# rhythm and digraph context do not carry over, but still inside the fifteen
# minute ceiling on any recorded silence: getting up, making coffee, taking a
# call. The weights lean short, because most interruptions are.
#
# An earlier revision drew 0.5-48 hours here and let MAX_SILENCE_MS clamp the
# result. Every gap then came out at exactly 900000.0 ms, which is a constant
# rather than a distribution and is trivially learnable by anything trained on
# these records. The table is now written in the units it is actually sampled
# in, so what the record contains is what this table says.
SESSION_GAP_MINUTES = (3.0, 5.0, 8.0, 11.0, 13.0)
SESSION_GAP_WEIGHTS = (0.22, 0.28, 0.24, 0.16, 0.10)

SENTENCE_ENDERS = frozenset(".!?")
CLAUSE_ENDERS = frozenset(",;:")

# Physically adjacent keys on a QWERTY board, used for substitution typos.
NEIGHBOR_KEYS: Dict[str, List[str]] = {
    'q': ['w', 'a', 's'],
    'w': ['q', 'e', 'a', 's', 'd'],
    'e': ['w', 'r', 's', 'd', 'f'],
    'r': ['e', 't', 'd', 'f', 'g'],
    't': ['r', 'y', 'f', 'g', 'h'],
    'y': ['t', 'u', 'g', 'h', 'j'],
    'u': ['y', 'i', 'h', 'j', 'k'],
    'i': ['u', 'o', 'j', 'k', 'l'],
    'o': ['i', 'p', 'k', 'l'],
    'p': ['o', 'l', ';'],
    'a': ['q', 'w', 's', 'z'],
    's': ['a', 'w', 'e', 'd', 'z', 'x'],
    'd': ['s', 'e', 'r', 'f', 'x', 'c'],
    'f': ['d', 'r', 't', 'g', 'c', 'v'],
    'g': ['f', 't', 'y', 'h', 'v', 'b'],
    'h': ['g', 'y', 'u', 'j', 'b', 'n'],
    'j': ['h', 'u', 'i', 'k', 'n', 'm'],
    'k': ['j', 'i', 'o', 'l', 'm'],
    'l': ['k', 'o', 'p', ';'],
    'z': ['a', 's', 'x'],
    'x': ['z', 's', 'd', 'c'],
    'c': ['x', 'd', 'f', 'v'],
    'v': ['c', 'f', 'g', 'b'],
    'b': ['v', 'g', 'h', 'n'],
    'n': ['b', 'h', 'j', 'm'],
    'm': ['n', 'j', 'k'],
}


@dataclass
class ScriptEvent:
    """One operation in the writing process.

    Exactly one payload field is meaningful per op:
      TYPE         -> char
      DELETE       -> count
      PAUSE        -> duration_ms
      SESSION_GAP  -> duration_ms
    """

    op: str
    char: Optional[str] = None
    count: int = 0
    duration_ms: float = 0.0
    role: str = ROLE_TEXT

    def to_dict(self) -> dict:
        d = {"op": self.op, "role": self.role}
        if self.op == OP_TYPE:
            d["char"] = self.char
        elif self.op == OP_DELETE:
            d["count"] = self.count
        else:
            d["duration_ms"] = round(self.duration_ms, 3)
        return d


def replay(events: List[ScriptEvent]) -> str:
    """Apply the script to an empty buffer and return the resulting text.

    This is the ground truth for what a script produces. DELETE is a backspace
    at the end of the buffer, matching how the events are emitted.
    """
    buffer: List[str] = []
    for event in events:
        if event.op == OP_TYPE:
            if event.char is None:
                raise ValueError("TYPE event has no char")
            buffer.append(event.char)
        elif event.op == OP_DELETE:
            if event.count < 0:
                raise ValueError(f"DELETE count must be >= 0, got {event.count}")
            if event.count > len(buffer):
                raise ValueError(
                    f"DELETE of {event.count} exceeds buffer length {len(buffer)}"
                )
            if event.count:
                del buffer[-event.count:]
    return "".join(buffer)


def _is_newline(ch: str) -> bool:
    return ch in ("\n", "\r", " ", " ")


def tokenize(text: str) -> List[str]:
    """Split into word tokens and individual whitespace characters.

    Whitespace is emitted one character at a time so each space, tab and
    newline becomes its own keystroke. Every other run of non-space characters
    is one word token. Concatenating the result reproduces the input exactly.
    """
    tokens: List[str] = []
    current: List[str] = []
    for ch in text:
        # str.isspace() is true for unicode separators too, which is what we
        # want - they are all single keystrokes or pasted whitespace.
        if ch.isspace():
            if current:
                tokens.append("".join(current))
                current = []
            tokens.append(ch)
        else:
            current.append(ch)
    if current:
        tokens.append("".join(current))
    return tokens


class MacroScripter:
    """Generates a writing-process script for a target text.

    Owns its own Random instance, so two scripters with the same seed produce
    identical scripts regardless of any other use of randomness in the process.
    """

    def __init__(
        self,
        seed: Optional[int] = None,
        typo_rate: float = TYPO_RATE,
        r_burst_probability: float = R_BURST_PROBABILITY,
        session_chars: Optional[int] = None,
        structural_revision_rate: float = STRUCTURAL_REVISION_RATE,
    ):
        if not 0.0 <= typo_rate <= 1.0:
            raise ValueError(f"typo_rate must be in [0, 1], got {typo_rate}")
        if not 0.0 <= r_burst_probability <= 1.0:
            raise ValueError(
                f"r_burst_probability must be in [0, 1], got {r_burst_probability}"
            )
        if not 0.0 <= structural_revision_rate <= 1.0:
            raise ValueError(
                "structural_revision_rate must be in [0, 1], got "
                f"{structural_revision_rate}"
            )
        if session_chars is not None and session_chars <= 0:
            raise ValueError(f"session_chars must be positive, got {session_chars}")

        self._rng = random.Random(seed)
        self.typo_rate = typo_rate
        self.r_burst_probability = r_burst_probability
        self.structural_revision_rate = structural_revision_rate
        self.session_chars = session_chars

    # -- sampling ------------------------------------------------------------

    def _pause_ms(self, context: str) -> float:
        """Sample a pause appropriate to what was just typed."""
        if context == "paragraph":
            mu, sigma = PAUSE_PARAGRAPH
        elif context == "sentence":
            mu, sigma = PAUSE_SENTENCE
        elif context == "clause":
            mu, sigma = PAUSE_CLAUSE
        else:
            mu, sigma = PAUSE_WORD
        return min(self._rng.lognormvariate(mu, sigma), MAX_SILENCE_MS)

    def _next_session_length(self) -> int:
        minutes = self._rng.uniform(*SESSION_MINUTES_RANGE)
        return int(minutes * NOMINAL_COMPOSITION_CPM)

    def _maybe_typo(self, char: str) -> Optional[str]:
        """Return a neighbour-key substitution for `char`, or None."""
        if self._rng.random() >= self.typo_rate:
            return None
        neighbors = NEIGHBOR_KEYS.get(char.lower())
        if not neighbors:
            return None
        typo = self._rng.choice(neighbors)
        if char.isupper():
            typo = typo.upper()
        # A "typo" identical to the intended character is not a typo. It cannot
        # happen with the current table, but the guard keeps the invariant
        # explicit if the table is ever edited.
        if typo == char:
            return None
        return typo

    def _burst_limit(self) -> tuple:
        """Return (word_limit, is_revision_burst) for the next burst."""
        if self._rng.random() < self.r_burst_probability:
            return self._rng.randint(*R_BURST_RANGE), True
        return self._rng.randint(*P_BURST_RANGE), False

    @staticmethod
    def _context_after(token: str) -> str:
        """Classify the pause that should follow a word token."""
        if not token:
            return "word"
        # Trailing punctuation may be followed by a quote or bracket.
        for ch in reversed(token):
            if ch in SENTENCE_ENDERS:
                return "sentence"
            if ch in CLAUSE_ENDERS:
                return "clause"
            category = unicodedata.category(ch)
            # Skip closing punctuation and quotes to find the real terminator.
            if category in ("Pe", "Pf", "Po") and ch in "\"')]}’”":
                continue
            break
        return "word"

    @staticmethod
    def _paragraph_trailing(text: str, sentence_start: int, next_start: int) -> bool:
        """True if the sentence at `sentence_start` trails its paragraph.

        Everything between two consecutive sentence starts is whitespace by
        construction, so a blank line in that span can only mean the earlier
        sentence ended its paragraph.
        """
        between = text[sentence_start:next_start].replace("\r", "")
        return "\n\n" in between

    # -- script construction -------------------------------------------------

    def generate_script(self, text: str) -> List[ScriptEvent]:
        """Build the full writing-process script for `text`.

        Guarantees `replay(result) == text`.
        """
        events: List[ScriptEvent] = []
        if not text:
            return events

        tokens = tokenize(text)

        burst_limit, is_r_burst = self._burst_limit()
        words_in_burst = 0

        session_limit = self.session_chars or self._next_session_length()
        chars_this_session = 0

        # Characters of the target text committed so far, used to bound how far
        # a revision may delete back.
        committed = 0
        # Where the current burst started, in committed characters.
        burst_start = 0

        pending_context: Optional[str] = None

        # Offsets where each sentence in the target text began, so a
        # structural revision can delete one back whole rather than stopping
        # at the burst boundary.
        sentence_starts: List[int] = []
        sentence_opens = True

        for index, token in enumerate(tokens):
            is_whitespace = token.isspace()

            # A session gap goes at a token boundary, never mid-word, and never
            # after the final token (nobody stops writing then comes back to
            # type nothing).
            if chars_this_session >= session_limit and index < len(tokens) - 1:
                events.append(
                    ScriptEvent(
                        OP_SESSION_GAP,
                        duration_ms=self._sample_session_gap_ms(),
                    )
                )
                chars_this_session = 0
                session_limit = self.session_chars or self._next_session_length()
                pending_context = None
                # Returning to the document restarts the burst.
                burst_limit, is_r_burst = self._burst_limit()
                words_in_burst = 0
                burst_start = committed

            if is_whitespace:
                context = pending_context or (
                    "paragraph" if _is_newline(token) else "word"
                )
                # A boundary becomes a thinking pause only sometimes; plain
                # word boundaries (probability 0) never do.
                if self._rng.random() < BOUNDARY_PAUSE_PROBABILITIES.get(
                    context, 0.0
                ):
                    events.append(
                        ScriptEvent(
                            OP_PAUSE, duration_ms=self._pause_ms(context)
                        )
                    )
                events.append(ScriptEvent(OP_TYPE, char=token))
                committed += 1
                chars_this_session += 1
                pending_context = None
                continue

            # Word token. The first word token of a sentence fixes where that
            # sentence began, before any revision at the burst boundary can
            # move the burst start.
            if sentence_opens:
                sentence_starts.append(committed)
                sentence_opens = False

            # If the burst is finished, pause and start a new one -
            # possibly revising what was just written first.
            if words_in_burst >= burst_limit:
                if is_r_burst and committed > burst_start:
                    events.extend(self._revise(text, burst_start, committed))
                events.append(
                    ScriptEvent(OP_PAUSE, duration_ms=self._pause_ms("sentence"))
                )
                burst_limit, is_r_burst = self._burst_limit()
                words_in_burst = 0
                burst_start = committed

            for char in token:
                typo = self._maybe_typo(char)
                if typo is not None:
                    events.append(
                        ScriptEvent(OP_TYPE, char=typo, role=ROLE_TYPO)
                    )
                    events.append(
                        ScriptEvent(
                            OP_PAUSE,
                            duration_ms=self._rng.uniform(*TYPO_REACTION_RANGE),
                            role=ROLE_TYPO,
                        )
                    )
                    events.append(
                        ScriptEvent(OP_DELETE, count=1, role=ROLE_CORRECTION)
                    )
                    events.append(
                        ScriptEvent(OP_TYPE, char=char, role=ROLE_CORRECTION)
                    )
                else:
                    events.append(ScriptEvent(OP_TYPE, char=char))
                committed += 1
                chars_this_session += 1

            words_in_burst += 1
            pending_context = self._context_after(token)
            if pending_context == "sentence":
                sentence_opens = True

            # A completed sentence is eligible for a structural revision:
            # delete it back whole - across the burst boundary, and sometimes
            # across a paragraph break when the previous sentence trailed one -
            # and retype it verbatim. The rate check comes first so a disabled
            # rate consumes no randomness and the stream above is undisturbed.
            if (
                pending_context == "sentence"
                and self.structural_revision_rate > 0.0
                and committed > sentence_starts[-1]
                and self._rng.random() < self.structural_revision_rate
            ):
                span_start = sentence_starts[-1]
                if (
                    len(sentence_starts) > 1
                    and self._paragraph_trailing(
                        text, sentence_starts[-2], sentence_starts[-1]
                    )
                    and self._rng.random() < STRUCTURAL_BACK_ACROSS_PARAGRAPH
                ):
                    span_start = sentence_starts[-2]
                events.extend(self._revise(text, span_start, committed, 1.0))
                # The retype is its own burst of work; the meter restarts.
                burst_limit, is_r_burst = self._burst_limit()
                words_in_burst = 0
                burst_start = committed

        return events

    def _sample_session_gap_ms(self) -> float:
        minutes = self._rng.choices(
            SESSION_GAP_MINUTES, weights=SESSION_GAP_WEIGHTS
        )[0]
        # Jitter so gaps are not all exactly round numbers of minutes.
        minutes *= self._rng.uniform(0.85, 1.15)
        # The table tops out at 13 minutes and the jitter at +15%, so this is
        # a guard on the product rule rather than the thing that sets the
        # value: gaps keep their spread instead of piling up on the ceiling.
        return min(minutes * 60_000.0, MAX_SILENCE_MS)

    def _revise(
        self,
        text: str,
        span_start: int,
        committed: int,
        fraction: Optional[float] = None,
    ) -> List[ScriptEvent]:
        """Delete back over a span of the target text and retype it verbatim.

        For an R-burst `span_start` is where the burst began and the deleted
        fraction is sampled from REVISION_FRACTION_RANGE; for a structural
        revision `span_start` is where the sentence began and the whole
        sentence goes (fraction 1.0), which may lie several bursts - or one
        paragraph break - before the current position. Either way the deleted
        span is retyped verbatim from the target text, so the buffer ends
        where it started and the replay invariant holds.
        """
        span = committed - span_start
        if span <= 0:
            return []

        if fraction is None:
            fraction = self._rng.uniform(*REVISION_FRACTION_RANGE)
        delete_count = max(1, min(span, int(round(span * fraction))))

        retyped = text[committed - delete_count:committed]
        if not retyped:
            return []

        events: List[ScriptEvent] = [
            ScriptEvent(
                OP_PAUSE,
                duration_ms=self._rng.uniform(*REVISION_PAUSE_RANGE),
                role=ROLE_REVISION_DELETE,
            ),
            ScriptEvent(
                OP_DELETE, count=delete_count, role=ROLE_REVISION_DELETE
            ),
            ScriptEvent(
                OP_PAUSE,
                duration_ms=self._rng.uniform(*REVISION_PAUSE_RANGE),
                role=ROLE_REVISION_RETYPE,
            ),
        ]
        events.extend(
            ScriptEvent(OP_TYPE, char=ch, role=ROLE_REVISION_RETYPE)
            for ch in retyped
        )
        return events

    def verify_script(self, text: str, events: List[ScriptEvent]) -> bool:
        """True if replaying `events` reproduces `text` exactly."""
        try:
            return replay(events) == text
        except ValueError:
            return False
