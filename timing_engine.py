"""
Project TypeTrace - Timing Engine

Generates micro-level keystroke timing (inter-key intervals and dwell times)
for a stream of key actions.

The engine is *stateful*: it models a latent "current speed" that drifts slowly
over time, so consecutive keystrokes are correlated rather than independent.
It does not own a clock - it reports intervals, and the caller (pipeline.py)
accumulates them into absolute timestamps. This keeps a single source of truth
for time.

Timing model, per keystroke i:

    log(iki_i) = log(base_delay(prev_key, key)) + a_i + e_i

    a_i = phi * a_{i-1} + innovation      (AR(1) latent speed, stationary sd = SIGMA_A)
    e_i ~ centred Gumbel with scale SIGMA_E   (right-skewed noise, per Migdal &
                                               Rosenberger 2019)

Working in log space keeps intervals positive without clamping and makes the
autocorrelation of the emitted series analytically tractable: the lag-1
autocorrelation of log(iki) is approximately

    rho = phi * var(a) / (var(a) + var(e) + var(log base_delay))

which is why SIGMA_A is solved for a *target* rho rather than set by hand. The
previous version applied AR(1) as a divisor on a linear-space Gumbel draw, where
the noise term dominated so completely that the emitted autocorrelation was
~0.02 against a documented target of 0.70.
"""

import math
from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np

# --- Configuration Constants (Research Baselines) ---

# Salthouse (1986) digraph baselines, in ms.
BASE_DELAY_ALTERNATE_HAND = 136.0
BASE_DELAY_SAME_HAND_DIFF_FINGER = 168.0
BASE_DELAY_SAME_FINGER = 218.0

# Space bar is struck with a thumb and overlaps well with either hand.
BASE_DELAY_SPACE = 120.0

# Dhakal et al. CHI 2018.
DWELL_MEAN = 116.0
DWELL_STD = 20.0
DWELL_FLOOR = 40.0

# Target mean typing speed for profile="average", Dhakal et al. CHI 2018.
TARGET_WPM_AVERAGE = 52.0

# Multiplicative noise on the inter-key interval, in log space.
# SIGMA_E is the scale of the Gumbel term; SIGMA_A is solved from the target
# autocorrelation in __init__.
SIGMA_E = 0.22
AR1_PHI = 0.90
DEFAULT_TARGET_AUTOCORRELATION = 0.35

# Variance and lag-1 autocorrelation of log(base_delay) over ordinary English
# text, measured empirically across the digraph mix of a large English sample.
#
# Both are needed to solve for SIGMA_A. The base-delay sequence is *itself*
# autocorrelated (rho ~ +0.16) because consecutive digraphs share a key: if key
# i is a right-hand key, both digraph (i-1, i) and digraph (i, i+1) are
# constrained by that. Ignoring this term undershoots the target by ~40%.
#
# These are fallbacks. calibrate() replaces them with the values measured on
# the text actually being typed, which matters because the digraph mix varies
# a lot between passages. Note that a text whose own base-delay sequence is
# strongly autocorrelated imposes a floor: no setting of the latent process
# can drive the emitted autocorrelation below what the digraph order already
# supplies. In that case var_a clamps to zero and the achieved value sits
# above the requested one.
VAR_LOG_BASE_TYPICAL = 0.0583
RHO_LOG_BASE_TYPICAL = 0.1608

# Absolute floor on an inter-key interval, ms. Physical key travel makes
# anything below this implausible even during rollover.
MIN_IKI_MS = 15.0

# Bigram speedup.
SPEED_BOOST_BIGRAM = 0.4
COMMON_BIGRAMS = {
    "th", "he", "in", "er", "an", "re", "on", "at", "en", "nd",
    "ti", "es", "or", "te", "of", "ed", "is", "it", "al", "ar",
    "st", "to", "nt", "ng", "se", "ha", "as", "ou", "io", "le",
}

# Profile multipliers. These are applied to the inter-key interval; `average`
# is 1.0 by definition and the others are relative. Calibration of `average`
# against TARGET_WPM_AVERAGE is handled by WPM_CALIBRATION below.
PROFILE_MULTIPLIERS = {
    "slow": 1.5,
    "average": 1.0,
    "fast": 0.7,
}

# Global scale factor applied to every inter-key interval so that
# profile="average" lands near TARGET_WPM_AVERAGE on ordinary English prose.
# Derived empirically: the raw Salthouse digraph baselines produce a faster
# stream than the Dhakal population mean, because Salthouse measured skilled
# transcription typists. See tests/test_timing_engine.py::test_average_profile_wpm.
WPM_CALIBRATION = 1.6236

# Probability that an *eligible* opposite-hand digraph is rolled over (the next
# key goes down before the previous comes up).
ROLLOVER_PROBABILITY = 0.30

# Rollover is only physically meaningful when the previous key is still nearly
# down as the next arrives. If releasing it naturally would already precede the
# next keydown by more than this, the typist is not rolling over and no
# extension is applied - forcing one would mean holding a key for the entire
# interval, which produced 300ms "dwells" in an earlier revision of this model.
# This gates the achieved rollover rate well below ROLLOVER_PROBABILITY; the
# achieved rate is reported in the output statistics rather than assumed.
ROLLOVER_REACH_MS = 40.0

# How far the previous key stays down past the next keydown, in ms.
ROLLOVER_OVERLAP_RANGE = (5.0, 30.0)

# --- Keyboard geometry -------------------------------------------------------

LEFT_HAND_KEYS = set("qwertasdfgzxcv12345`~!@#$%")
RIGHT_HAND_KEYS = set("yuiophjklbnm67890-=[]\\;',./^&*()_+{}|:\"<>?")

# Finger numbering: 1-5 left (1 = pinky ... 5 = index reaching inward),
# 6-10 right (6 = index reaching inward ... 10 = pinky), 11 = thumb.
FINGER_MAP = {
    'q': 1, 'a': 1, 'z': 1, '1': 1, '`': 1,
    'w': 2, 's': 2, 'x': 2, '2': 2,
    'e': 3, 'd': 3, 'c': 3, '3': 3,
    'r': 4, 'f': 4, 'v': 4, '4': 4,
    't': 5, 'g': 5, 'b': 5, '5': 5,
    'y': 6, 'h': 6, 'n': 6, '6': 6,
    'u': 7, 'j': 7, 'm': 7, '7': 7,
    'i': 8, 'k': 8, ',': 8, '8': 8,
    'o': 9, 'l': 9, '.': 9, '9': 9,
    'p': 10, ';': 10, '/': 10, '0': 10, '-': 10, '=': 10,
    '[': 10, ']': 10, '\\': 10, "'": 10,
    ' ': 11,
}

# Characters that require Shift, mapped to the unshifted key in the same
# position. Typing these costs an extra pinky press on the opposite hand.
SHIFTED_CHARS = {
    '!': '1', '@': '2', '#': '3', '$': '4', '%': '5',
    '^': '6', '&': '7', '*': '8', '(': '9', ')': '0',
    '_': '-', '+': '=', '{': '[', '}': ']', '|': '\\',
    ':': ';', '"': "'", '<': ',', '>': '.', '?': '/', '~': '`',
}

# Cost of the extra Shift press, as a multiplier on the inter-key interval.
SHIFT_PENALTY = 1.18

# Newline (Enter) and Backspace are both right-pinky reaches.
ENTER_FINGER = 10
BACKSPACE_FINGER = 10

# Backspace is represented in the keystroke stream by this character.
BACKSPACE = "\b"


@dataclass
class KeyTiming:
    """Timing for one key action. All values in milliseconds.

    iki_ms is the interval from the *previous keydown* to this keydown. It is
    the standard inter-key interval used in keystroke-dynamics literature.
    dwell_ms is how long this key is held. The caller derives absolute
    timestamps and the keyup-to-keydown "flight" from these two.

    prev_overlap_ms is non-zero when this keystroke rolls over the previous one:
    the previous key stays down this many milliseconds past *this* key's
    keydown. The caller applies it by extending the previous event's keyup.
    Rollover is modelled as the previous key being released late rather than
    this key arriving early, which is both what physically happens and what
    leaves the inter-key interval series undisturbed.
    """

    iki_ms: float
    dwell_ms: float
    prev_overlap_ms: float = 0.0

    @property
    def rollover(self) -> bool:
        return self.prev_overlap_ms > 0.0


class TimingEngine:
    """Stateful generator of keystroke intervals.

    Each instance owns its own random number generators, so two engines with
    the same seed produce identical streams regardless of what else in the
    process has consumed randomness.
    """

    def __init__(
        self,
        profile: str = "average",
        seed: Optional[int] = None,
        target_autocorrelation: float = DEFAULT_TARGET_AUTOCORRELATION,
        phi: float = AR1_PHI,
    ):
        if profile not in PROFILE_MULTIPLIERS:
            raise ValueError(
                f"unknown profile {profile!r}; expected one of "
                f"{sorted(PROFILE_MULTIPLIERS)}"
            )
        if not 0.0 <= target_autocorrelation < phi:
            raise ValueError(
                f"target_autocorrelation must be in [0, phi); got "
                f"{target_autocorrelation} with phi={phi}. The emitted "
                f"autocorrelation cannot reach the latent persistence."
            )

        self.profile = profile
        self.multiplier = PROFILE_MULTIPLIERS[profile]
        self.phi = phi
        self.target_autocorrelation = target_autocorrelation

        # Per-instance RNGs. Nothing here touches the global random state.
        self._rng = np.random.Generator(np.random.PCG64(seed))

        # Solve for the AR(1) stationary variance that yields the target
        # autocorrelation in the *emitted* series. log(iki) is the sum of three
        # terms, two of which are autocorrelated, so
        #
        #   rho = (phi * var_a + rho_base * var_base)
        #         / (var_a + var_e + var_base)
        #
        # =>  var_a = (rho * (var_e + var_base) - rho_base * var_base)
        #             / (phi - rho)
        self._var_e = (SIGMA_E * math.pi / math.sqrt(6.0)) ** 2
        self._solve_latent_variance(VAR_LOG_BASE_TYPICAL, RHO_LOG_BASE_TYPICAL)

        self.prev_key: Optional[str] = None
        self._prev_dwell: Optional[float] = None

    def _solve_latent_variance(self, var_base: float, rho_base: float) -> None:
        """Set the AR(1) variance that yields the target autocorrelation."""
        rho = self.target_autocorrelation
        numerator = rho * (self._var_e + var_base) - rho_base * var_base
        # A target below what the base-delay sequence already contributes on
        # its own needs no latent process at all.
        self.var_a = max(0.0, numerator / (self.phi - rho))
        self.sigma_a = math.sqrt(self.var_a)

        # Innovation sd that makes the AR(1) stationary with variance var_a.
        self._innovation_sd = self.sigma_a * math.sqrt(1.0 - self.phi * self.phi)

        # Draw the latent speed from its stationary distribution so the very
        # first keystrokes are not systematically average.
        self._a = (
            float(self._rng.normal(0.0, self.sigma_a)) if self.sigma_a > 0.0 else 0.0
        )

    def calibrate(self, chars: Sequence[str]) -> None:
        """Re-solve the latent variance against the text about to be typed.

        VAR_LOG_BASE_TYPICAL and RHO_LOG_BASE_TYPICAL are measured on one
        English sample, but both quantities depend on the actual digraph mix -
        a passage heavy in same-finger sequences has a different base-delay
        variance than one that alternates. Using the defaults leaves the
        emitted autocorrelation biased by up to about 0.06 on texts unlike the
        calibration sample. Measuring the real sequence removes that.

        Call before generating. Does nothing for a sequence too short to
        estimate from.
        """
        logs = []
        prev: Optional[str] = None
        for char in chars:
            if prev is not None:
                logs.append(math.log(self._effective_base(prev, char)))
            prev = char

        if len(logs) < 30:
            return

        mean = sum(logs) / len(logs)
        var_base = sum((x - mean) ** 2 for x in logs) / len(logs)
        if var_base <= 0.0:
            # Every digraph is the same class, so the base sequence
            # contributes no variance and no autocorrelation of its own.
            self._solve_latent_variance(0.0, 0.0)
            return

        rho_base = sum(
            (logs[i] - mean) * (logs[i + 1] - mean) for i in range(len(logs) - 1)
        ) / sum((x - mean) ** 2 for x in logs)

        self._solve_latent_variance(var_base, rho_base)

    # -- keyboard helpers ----------------------------------------------------

    @staticmethod
    def _base_key(char: str) -> str:
        """Map a character to the physical key used to type it."""
        if char in SHIFTED_CHARS:
            return SHIFTED_CHARS[char]
        return char.lower()

    @staticmethod
    def _needs_shift(char: str) -> bool:
        return char in SHIFTED_CHARS or (len(char) == 1 and char.isupper())

    def _hand(self, char: str) -> str:
        key = self._base_key(char)
        if key == ' ':
            return "thumb"
        # Enter and Backspace both sit off the right edge of the board.
        if key in ('\n', '\r', BACKSPACE):
            return "right"
        if key in LEFT_HAND_KEYS:
            return "left"
        if key in RIGHT_HAND_KEYS:
            return "right"
        return "unknown"

    def _finger(self, char: str) -> int:
        key = self._base_key(char)
        if key in ('\n', '\r'):
            return ENTER_FINGER
        if key == BACKSPACE:
            return BACKSPACE_FINGER
        return FINGER_MAP.get(key, 0)

    def _base_delay(self, prev_char: str, char: str) -> float:
        """Salthouse (1986) digraph classification."""
        p_hand, c_hand = self._hand(prev_char), self._hand(char)

        # The space bar overlaps with either hand, so a digraph involving it is
        # effectively an alternation.
        if p_hand == "thumb" or c_hand == "thumb":
            return BASE_DELAY_SPACE

        if p_hand == "unknown" or c_hand == "unknown":
            return BASE_DELAY_SAME_HAND_DIFF_FINGER

        if p_hand != c_hand:
            return BASE_DELAY_ALTERNATE_HAND

        p_finger, c_finger = self._finger(prev_char), self._finger(char)
        if p_finger == c_finger and p_finger != 0:
            return BASE_DELAY_SAME_FINGER

        return BASE_DELAY_SAME_HAND_DIFF_FINGER

    # -- sampling ------------------------------------------------------------

    def _advance_latent_speed(self) -> float:
        """Step the AR(1) latent speed process and return its current value."""
        if self.sigma_a <= 0.0:
            return 0.0
        self._a = self.phi * self._a + float(
            self._rng.normal(0.0, self._innovation_sd)
        )
        return self._a

    def _sample_noise(self) -> float:
        """Centred Gumbel noise in log space (mean zero, scale SIGMA_E)."""
        # numpy's gumbel(loc, scale) has mean loc + scale * euler_gamma.
        raw = float(self._rng.gumbel(0.0, SIGMA_E))
        return raw - SIGMA_E * np.euler_gamma

    def _sample_dwell(self) -> float:
        """Dwell time, N(116, 20) truncated below at 40ms (Dhakal et al.).

        Resampled rather than clamped so the emitted distribution is a genuine
        truncated normal with no point mass at the floor. The practical
        difference is small - 40ms is 3.8 sigma below the mean, so roughly 1 in
        14000 draws is affected - but resampling keeps the distribution
        analytically what the docstring says it is.
        """
        for _ in range(16):
            dwell = float(self._rng.normal(DWELL_MEAN, DWELL_STD))
            if dwell >= DWELL_FLOOR:
                return dwell
        return DWELL_FLOOR

    def _rolls_over(self, prev_char: str, char: str) -> bool:
        p_hand, c_hand = self._hand(prev_char), self._hand(char)
        if "unknown" in (p_hand, c_hand):
            return False
        if p_hand == c_hand:
            return False
        return bool(self._rng.random() < ROLLOVER_PROBABILITY)

    # -- public API ----------------------------------------------------------

    def reset_context(self) -> None:
        """Forget the previous key.

        Called after a pause long enough that the previous keystroke no longer
        influences the next one - a session gap, or a deliberate think pause.
        The latent speed state is deliberately *not* reset: typing speed
        persists across a short pause.
        """
        self.prev_key = None
        self._prev_dwell = None

    def reset_speed(self) -> None:
        """Redraw the latent speed from its stationary distribution.

        Called after a session gap, where the typist returns in an unrelated
        state.
        """
        self._a = float(self._rng.normal(0.0, self.sigma_a)) if self.sigma_a > 0 else 0.0

    def _effective_base(self, prev_char: str, char: str) -> float:
        """Base interval for a digraph, including bigram and shift effects."""
        base = self._base_delay(prev_char, char)
        if self._base_key(prev_char) + self._base_key(char) in COMMON_BIGRAMS:
            base *= (1.0 - SPEED_BOOST_BIGRAM)
        if self._needs_shift(char):
            base *= SHIFT_PENALTY
        return base

    def next_keystroke(self, char: str) -> KeyTiming:
        """Timing for typing `char` given everything typed so far."""
        a = self._advance_latent_speed()
        e = self._sample_noise()

        if self.prev_key is None:
            # No digraph context: use the same-hand baseline as a neutral start.
            base = BASE_DELAY_SAME_HAND_DIFF_FINGER
            if self._needs_shift(char):
                base *= SHIFT_PENALTY
        else:
            base = self._effective_base(self.prev_key, char)

        iki = math.exp(math.log(base) + a + e)
        iki *= self.multiplier * WPM_CALIBRATION
        iki = max(MIN_IKI_MS, iki)

        dwell = self._sample_dwell() * self.multiplier

        # Rollover: the previous key is released *after* this one goes down.
        # The old code instead shortened this key's interval (and used max(),
        # so the overlap it documented never actually occurred). Extending the
        # previous keyup is both physically right and leaves the interval
        # series - and therefore its autocorrelation - untouched.
        # Rollover only applies when the previous key would otherwise come up
        # shortly before this one goes down. If it is still down anyway
        # (iki < prev dwell) the overlap is already there and needs no help.
        overlap = 0.0
        if self.prev_key is not None and self._prev_dwell is not None:
            reach = iki - self._prev_dwell
            if 0.0 < reach <= ROLLOVER_REACH_MS and self._rolls_over(
                self.prev_key, char
            ):
                lo, hi = ROLLOVER_OVERLAP_RANGE
                overlap = float(self._rng.uniform(lo, hi))

        self.prev_key = char
        self._prev_dwell = dwell
        return KeyTiming(iki_ms=iki, dwell_ms=dwell, prev_overlap_ms=overlap)
