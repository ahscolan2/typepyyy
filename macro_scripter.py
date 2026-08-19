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

# Lognormal pause parameters (mu, sigma) on log-milliseconds. Medians are
# exp(mu): 90ms, 181ms, 493ms, 1097ms respectively. Verified in
# tests/test_macro_scripter.py::test_pause_medians.
PAUSE_WORD = (4.5, 0.4)
PAUSE_CLAUSE = (5.2, 0.5)
PAUSE_SENTENCE = (6.2, 0.6)
PAUSE_PARAGRAPH = (7.0, 0.7)

# Reaction time before noticing and fixing a typo, ms.
TYPO_REACTION_RANGE = (300.0, 800.0)

# Pause before beginning a revision, and before retyping after one.
REVISION_PAUSE_RANGE = (400.0, 1500.0)

TYPO_RATE = 0.03

# Fraction of a completed R-burst that gets deleted and retyped.
#
# With R_BURST_PROBABILITY at the cited 0.20 this yields an overall deletion
# ratio of roughly 0.09 - just below the 0.10-0.30 range reported for real
# composition. The shortfall is a known limitation of the model, not a
# mis-tuning: revision here is local to the burst just written, and does not
# include the structural rewriting (deleting and reworking whole sentences or
# paragraphs) that makes up much of the deletion in real writing. Raising
# r_burst_probability to ~0.25 brings the ratio into the reported range if a
# consumer would rather match the aggregate than the cited burst rate.
REVISION_FRACTION_RANGE = (0.4, 1.0)

# A writing session runs 20-90 minutes. Converted to characters using a nominal
# composition rate, since this module works in characters rather than time.
SESSION_MINUTES_RANGE = (20.0, 90.0)
NOMINAL_COMPOSITION_CPM = 160.0

# Gaps between sessions, in hours, with the weights of a student working over
# several days: mostly overnight or next-evening, occasionally a short break.
SESSION_GAP_HOURS = (0.5, 1.0, 2.0, 4.0, 16.0, 24.0, 48.0)
SESSION_GAP_WEIGHTS = (0.10, 0.12, 0.15, 0.15, 0.18, 0.20, 0.10)

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
    ):
        if not 0.0 <= typo_rate <= 1.0:
            raise ValueError(f"typo_rate must be in [0, 1], got {typo_rate}")
        if not 0.0 <= r_burst_probability <= 1.0:
            raise ValueError(
                f"r_burst_probability must be in [0, 1], got {r_burst_probability}"
            )
        if session_chars is not None and session_chars <= 0:
            raise ValueError(f"session_chars must be positive, got {session_chars}")

        self._rng = random.Random(seed)
        self.typo_rate = typo_rate
        self.r_burst_probability = r_burst_probability
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
        return self._rng.lognormvariate(mu, sigma)

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
                events.append(
                    ScriptEvent(OP_PAUSE, duration_ms=self._pause_ms(context))
                )
                events.append(ScriptEvent(OP_TYPE, char=token))
                committed += 1
                chars_this_session += 1
                pending_context = None
                continue

            # Word token. If the burst is finished, pause and start a new one -
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

        return events

    def _sample_session_gap_ms(self) -> float:
        hours = self._rng.choices(SESSION_GAP_HOURS, weights=SESSION_GAP_WEIGHTS)[0]
        # Jitter so gaps are not all exactly round numbers of hours.
        hours *= self._rng.uniform(0.85, 1.15)
        return hours * 3_600_000.0

    def _revise(self, text: str, burst_start: int, committed: int) -> List[ScriptEvent]:
        """Delete back into the just-written burst and retype it.

        This is what makes an R-burst a revision burst rather than just a short
        one. The deleted span is retyped verbatim from the target text, so the
        buffer ends where it started and the replay invariant holds.
        """
        span = committed - burst_start
        if span <= 0:
            return []

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
