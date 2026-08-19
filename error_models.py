"""
Project TypeTrace - Cognitive error models (optional, standalone).

A library of typing errors richer than the single neighbour-key substitution
that macro_scripter implements: anticipations, perseverations, transpositions,
stutters, and homophone confusions.

NOTHING ELSE IN THIS PROJECT IMPORTS THIS MODULE. `python main.py` and the
desktop app both run macro_scripter's own typo model and never call anything
here. This module is an optional extra for experiments that want a wider error
taxonomy - a caller who wants one imports it and drives it directly. It is not
part of the default generator, and the docstrings here do not claim otherwise.
Wiring it into MacroScripter would mean changing that class's script
construction, which is deliberately out of scope.

It is written to compose with macro_scripter rather than to replace it. The
scripts it emits are macro_scripter.ScriptEvent objects in the same op
vocabulary, so they replay under the same invariant the rest of the project
depends on:

    macro_scripter.replay(model.build_error_script(text)) == text

Each class owns its own random.Random, so a seed reproduces its output
regardless of any other use of randomness in the process, and the global RNG is
never touched.

The error taxonomy follows Dell (1986) on anticipation and perseveration in
production, and Salthouse (1986) on transposition in transcription typing. The
rates below are illustrative defaults chosen to be plausible, not values
measured from a corpus; a consumer who needs calibrated rates should set them.
"""

import random
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import macro_scripter as ms
from macro_scripter import ScriptEvent

# Error kinds. Each names what the writer's hands did wrong, not what the text
# ends up looking like, because the correction script depends on the mechanism.
KIND_ANTICIPATION = "anticipation"
KIND_PERSEVERATION = "perseveration"
KIND_EXCHANGE = "exchange"
KIND_STUTTER = "stutter"

# How far ahead an anticipated character is pulled from, and how far back a
# perseverated one is repeated from. One entry per plausible distance: each
# becomes its own candidate, so a longer list makes those kinds more likely
# relative to exchange and stutter.
ANTICIPATION_DISTANCES = (2, 3)
PERSEVERATION_DISTANCES = (1, 2)

# Keys that tend to be struck twice when the hand runs ahead of the intention.
# Restricting stutter to these is what makes it a distinct error kind rather
# than "double any character".
STUTTER_KEYS = frozenset("tprsldkmn")

DEFAULT_ERROR_RATE = 0.03


@dataclass(frozen=True)
class ErrorEdit:
    """One error, described against the *original* text.

    `text[index:index + len(intended)]` is what the writer meant to type and
    `typed` is what came out instead. Errors from one call to `plan_errors` are
    non-overlapping and sorted by index, so applying them left to right is
    well defined.
    """

    kind: str
    index: int
    intended: str
    typed: str

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "index": self.index,
            "intended": self.intended,
            "typed": self.typed,
        }


def _common_prefix_length(a: str, b: str) -> int:
    limit = min(len(a), len(b))
    i = 0
    while i < limit and a[i] == b[i]:
        i += 1
    return i


def is_suffix_edit(original: str, flawed: str) -> bool:
    """True if the difference between the two strings reaches the end of both.

    A DELETE in this project's op vocabulary is a backspace at the end of the
    buffer: there is no cursor-movement operation. So a correction is only
    expressible without destroying correct text when the difference runs all
    the way to the end of both strings. "helol" -> "hello" qualifies; "helol
    world" -> "hello world" does not, because the shared " world" would have to
    be destroyed and retyped to reach the transposition.

    Note this is narrower than "reachable by backspacing at all" - every pair is
    reachable if you are willing to delete the whole buffer and retype it. What
    is being tested is whether a correction can be expressed without touching
    text that is already right, which is why ("aab", "ab") is False: the two
    share a trailing "b".
    """
    prefix = _common_prefix_length(original, flawed)
    if prefix == len(original) or prefix == len(flawed):
        # One is a prefix of the other: delete the excess, or type the rest.
        return True
    # Both still have characters past the divergence. If they end with the same
    # character there is a common suffix, so the edit is interior.
    return original[-1] != flawed[-1]


class CognitiveErrorModel:
    """Introduces cognitive typing errors and the scripts that correct them.

    The model works on a text and returns explicit `ErrorEdit` descriptions
    rather than only a mangled string, so a caller always knows what was
    changed, where, and why. That is what makes the correction script exact.
    """

    def __init__(
        self,
        error_rate: float = DEFAULT_ERROR_RATE,
        seed: Optional[int] = None,
    ):
        if not 0.0 <= error_rate <= 1.0:
            raise ValueError(f"error_rate must be in [0, 1], got {error_rate}")
        self.error_rate = error_rate
        self._rng = random.Random(seed)

    # -- error planning ------------------------------------------------------

    def _candidates(self, text: str, index: int) -> List[ErrorEdit]:
        """Every error kind that is physically applicable at `index`.

        Only applicable kinds are offered. The previous version of this module
        chose a kind first and then silently returned the text unchanged when
        that kind did not fit, which made the achieved error rate unrelated to
        the configured one.
        """
        candidates: List[ErrorEdit] = []
        char = text[index]
        if not char.isalpha():
            # Spaces and punctuation are not subject to these errors; a space is
            # struck by the thumb and is not part of a letter sequence.
            return candidates

        following = text[index + 1] if index + 1 < len(text) else ""
        if following.isalpha() and following != char:
            candidates.append(
                ErrorEdit(
                    KIND_EXCHANGE,
                    index,
                    text[index:index + 2],
                    following + char,
                )
            )

        if char.lower() in STUTTER_KEYS:
            candidates.append(ErrorEdit(KIND_STUTTER, index, char, char * 2))

        for distance in ANTICIPATION_DISTANCES:
            ahead = index + distance
            if ahead < len(text) and text[ahead].isalpha() and text[ahead] != char:
                candidates.append(
                    ErrorEdit(KIND_ANTICIPATION, index, char, text[ahead])
                )

        for distance in PERSEVERATION_DISTANCES:
            behind = index - distance
            if behind >= 0 and text[behind].isalpha() and text[behind] != char:
                candidates.append(
                    ErrorEdit(KIND_PERSEVERATION, index, char, text[behind])
                )

        return candidates

    def plan_errors(self, text: str) -> List[ErrorEdit]:
        """Decide where errors happen, without changing the text.

        One independent Bernoulli(error_rate) trial per character position,
        scanning left to right and skipping past any error that fires so the
        edits never overlap.

        The rate is per *eligible* position. Positions with no applicable error
        kind - spaces, punctuation, an isolated letter with no alphabetic
        neighbours - never produce one, so the error count is close to
        error_rate times the number of eligible positions rather than
        error_rate times len(text), and slightly under it because a position
        consumed by an error is not tried again.
        """
        edits: List[ErrorEdit] = []
        index = 0
        while index < len(text):
            if self._rng.random() < self.error_rate:
                candidates = self._candidates(text, index)
                if candidates:
                    edit = self._rng.choice(candidates)
                    edits.append(edit)
                    index += len(edit.intended)
                    continue
            index += 1
        return edits

    @staticmethod
    def apply_edits(text: str, edits: Sequence[ErrorEdit]) -> str:
        """Return the text as it would look with `edits` typed but not fixed."""
        pieces: List[str] = []
        cursor = 0
        for edit in edits:
            if edit.index < cursor:
                raise ValueError(
                    f"edits overlap at index {edit.index}; expected them "
                    f"non-overlapping and in order"
                )
            if text[edit.index:edit.index + len(edit.intended)] != edit.intended:
                raise ValueError(
                    f"edit at index {edit.index} does not match the text it "
                    f"describes ({edit.intended!r})"
                )
            pieces.append(text[cursor:edit.index])
            pieces.append(edit.typed)
            cursor = edit.index + len(edit.intended)
        pieces.append(text[cursor:])
        return "".join(pieces)

    def introduce_errors(self, text: str) -> str:
        """The flawed intermediate state: `text` with uncorrected errors in it.

        Convenience wrapper over plan_errors + apply_edits for callers that only
        want the string. Callers that need to correct the errors afterwards
        should keep the edits, since they carry the positions.
        """
        return self.apply_edits(text, self.plan_errors(text))

    # -- correction ----------------------------------------------------------

    def _reaction_pause(self, role: str) -> ScriptEvent:
        """The delay between making an error and noticing it."""
        return ScriptEvent(
            ms.OP_PAUSE,
            duration_ms=self._rng.uniform(*ms.TYPO_REACTION_RANGE),
            role=role,
        )

    def correction_events(self, edit: ErrorEdit) -> List[ScriptEvent]:
        """Type the error, notice it, backspace it, type what was intended.

        The error is fixed immediately, before typing continues past it, which
        is what keeps the deletion at the end of the buffer where a backspace
        can reach it.
        """
        events: List[ScriptEvent] = [
            ScriptEvent(ms.OP_TYPE, char=ch, role=ms.ROLE_TYPO)
            for ch in edit.typed
        ]
        events.append(self._reaction_pause(ms.ROLE_TYPO))
        events.append(
            ScriptEvent(ms.OP_DELETE, count=len(edit.typed), role=ms.ROLE_CORRECTION)
        )
        events.extend(
            ScriptEvent(ms.OP_TYPE, char=ch, role=ms.ROLE_CORRECTION)
            for ch in edit.intended
        )
        return events

    def generate_correction_script(
        self, original: str, flawed: str
    ) -> List[ScriptEvent]:
        """Events that turn `flawed` into `original` by backspacing and retyping.

        Assumes `flawed` is the whole buffer and the cursor sits at its end.
        Raises ValueError when the difference does not reach the end of both
        strings, because a backspace cannot get to an interior edit without
        cursor movement, which this project's op vocabulary does not model. Use
        is_suffix_edit() to check first. Every edit produced by plan_errors and
        corrected through correction_events satisfies this by construction.
        """
        if original == flawed:
            return []
        if not is_suffix_edit(original, flawed):
            raise ValueError(
                "correction is not expressible as backspaces: the difference "
                f"between {flawed!r} and {original!r} is interior, and DELETE "
                "only removes characters from the end of the buffer"
            )

        prefix = _common_prefix_length(original, flawed)
        events: List[ScriptEvent] = [self._reaction_pause(ms.ROLE_CORRECTION)]

        delete_count = len(flawed) - prefix
        if delete_count:
            events.append(
                ScriptEvent(ms.OP_DELETE, count=delete_count, role=ms.ROLE_CORRECTION)
            )
        events.extend(
            ScriptEvent(ms.OP_TYPE, char=ch, role=ms.ROLE_CORRECTION)
            for ch in original[prefix:]
        )
        return events

    # -- composition with macro_scripter -------------------------------------

    def build_error_script(self, text: str) -> List[ScriptEvent]:
        """A script that types `text`, making and immediately fixing errors.

        Guarantees `macro_scripter.replay(result) == text` for any input.

        This is the smallest thing that demonstrates the error model composing
        with macro_scripter's op vocabulary - it has no bursts, pause hierarchy,
        revisions or session gaps, and it is not what the pipeline runs.
        MacroScripter.generate_script is the real script builder.
        """
        edits = {edit.index: edit for edit in self.plan_errors(text)}
        events: List[ScriptEvent] = []
        index = 0
        while index < len(text):
            edit = edits.get(index)
            if edit is None:
                events.append(ScriptEvent(ms.OP_TYPE, char=text[index]))
                index += 1
                continue
            events.extend(self.correction_events(edit))
            index += len(edit.intended)
        return events


# Sets of words a writer genuinely selects the wrong member of. These are
# lexical-selection errors: the hands type exactly what was intended and the
# intention was wrong. Keyboard slips ("expert" -> "erpert") and transpositions
# ("form" -> "from") are motor errors and belong to CognitiveErrorModel or to
# macro_scripter's neighbour-key model, so they are deliberately absent here.
#
# Each set is unordered and any member can be confused for any other, which
# keeps the table self-consistent - the previous asymmetric mapping contained
# pairs that no writer produces ("you're" -> "yacht", "it's" -> "in").
CONFUSION_SETS: Tuple[Tuple[str, ...], ...] = (
    ("their", "there", "they're"),
    ("your", "you're"),
    ("its", "it's"),
    ("then", "than"),
    ("affect", "effect"),
    ("to", "too"),
    ("lose", "loose"),
    ("accept", "except"),
    ("principal", "principle"),
    ("complement", "compliment"),
    ("weather", "whether"),
    ("advice", "advise"),
    ("who's", "whose"),
    ("passed", "past"),
    ("led", "lead"),
)


def _build_confusion_table() -> Dict[str, Tuple[str, ...]]:
    table: Dict[str, Tuple[str, ...]] = {}
    for group in CONFUSION_SETS:
        for word in group:
            table[word] = tuple(other for other in group if other != word)
    return table


CONFUSIONS = _build_confusion_table()

# A word, optionally carrying one internal apostrophe so "it's" and "they're"
# are single tokens. \w+ would split them and never match the table.
WORD_PATTERN = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)?")

TYPOGRAPHIC_APOSTROPHE = "’"


@dataclass(frozen=True)
class Substitution:
    """One word replaced by a confusable one, located in the source text."""

    index: int
    original: str
    replacement: str

    def to_dict(self) -> dict:
        return {
            "kind": "lexical_confusion",
            "index": self.index,
            "original": self.original,
            "replacement": self.replacement,
        }


def _match_case(source: str, replacement: str) -> str:
    """Give `replacement` the capitalisation of the word it replaces."""
    if len(source) > 1 and source.isupper():
        return replacement.upper()
    if source[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


class SemanticSubstitution:
    """Replaces a word with one it is genuinely confused with.

    This models lexical selection going wrong - homophones and near-homophones
    - and nothing else. The result is a *different text*, not a mistyping of the
    same text: unlike CognitiveErrorModel's edits, a caller that substitutes
    here has changed what the writer meant to say, so whatever records the
    output must record the substituted text as the text that was written.
    """

    def __init__(self, seed: Optional[int] = None):
        self.substitutions = CONFUSIONS
        self._rng = random.Random(seed)

    def candidates(self, text: str) -> List[re.Match]:
        """Every whole-word occurrence in `text` that has a confusable form."""
        return [
            match
            for match in WORD_PATTERN.finditer(text)
            if self._lookup_key(match.group(0)) in self.substitutions
        ]

    @staticmethod
    def _lookup_key(word: str) -> str:
        return word.lower().replace(TYPOGRAPHIC_APOSTROPHE, "'")

    def maybe_substitute(self, text: str) -> Tuple[str, Optional[Substitution]]:
        """Substitute one randomly chosen confusable word, if there is one.

        "Maybe" refers to the text: if it contains no confusable word the input
        is returned unchanged with None.

        The replacement is spliced in by position. The previous version called
        str.replace(word, repl, 1), which rewrote the first occurrence anywhere
        in the text rather than the one that was chosen, and matched inside
        longer words - substituting "its" would rewrite the middle of "bits".
        """
        matches = self.candidates(text)
        if not matches:
            return text, None

        match = self._rng.choice(matches)
        word = match.group(0)
        replacement = self._rng.choice(self.substitutions[self._lookup_key(word)])
        replacement = _match_case(word, replacement)
        # Keep the apostrophe style of the text being edited.
        if TYPOGRAPHIC_APOSTROPHE in word:
            replacement = replacement.replace("'", TYPOGRAPHIC_APOSTROPHE)

        new_text = text[:match.start()] + replacement + text[match.end():]
        return new_text, Substitution(match.start(), word, replacement)
