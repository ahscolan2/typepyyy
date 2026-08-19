"""Tests for timing_engine.

The engine's job is to emit intervals whose *distribution* is right, so most of
what follows is statistical. Every such test either fixes a seed or averages
over a fixed range of seeds, and states the tolerance it uses, so that a
failure means the model moved rather than that the dice fell badly.

The reference values come from the lead's measurements: 34.7 / 51.98 / 74.3 WPM
for the three profiles, achieved autocorrelation 0.21 / 0.34 / 0.49 against
targets of 0.20 / 0.35 / 0.50 with a per-seed sd of about 0.05, and a mean
dwell near 116ms.
"""

import math
import random
import statistics

import numpy as np
import pytest

import timing_engine as te
from pipeline import lag1_autocorrelation
from timing_engine import BACKSPACE, KeyTiming, TimingEngine

SEEDS = tuple(range(20))


def type_out(engine: TimingEngine, text: str) -> list:
    """Every KeyTiming the engine emits for `text`, after calibrating on it."""
    engine.calibrate(text)
    return [engine.next_keystroke(char) for char in text]


def keystroke_wpm(timings: list, chars: int) -> float:
    """WPM on the keystroke clock: chars/5 over the summed intervals.

    The first keystroke has no predecessor, so its interval is not elapsed
    time; the final dwell is, because the last key has to come back up.
    """
    elapsed = sum(t.iki_ms for t in timings[1:]) + timings[-1].dwell_ms
    return (chars / 5.0) / (elapsed / 60_000.0)


def profile_wpm(profile: str, text: str, seed: int) -> float:
    engine = TimingEngine(profile=profile, seed=seed)
    return keystroke_wpm(type_out(engine, text), len(text))


def digraph_intervals(first: str, second: str, seed: int, repeats: int) -> list:
    """Intervals for the digraph (first, second), sampled in isolation.

    Alternating the two characters means every other interval is the digraph
    under test; the intervening ones are its reverse and are discarded. Feeding
    the pair repeatedly rather than resetting the engine keeps the latent speed
    process running, which is what the engine is designed to do.
    """
    engine = TimingEngine(seed=seed)
    stream = (first + second) * repeats
    intervals = [
        engine.next_keystroke(char).iki_ms for char in stream
    ]
    # Index 1 is the first (first -> second) digraph; drop it along with index
    # 0 so the latent process has warmed up.
    return intervals[3::2]


def mean_digraph_interval(first: str, second: str, repeats: int = 300) -> float:
    return statistics.mean(
        statistics.mean(digraph_intervals(first, second, seed, repeats))
        for seed in SEEDS
    )


# --- construction and validation ---------------------------------------------


@pytest.mark.parametrize(
    "profile", ["", "medium", "AVERAGE", "quick", None, "slow ",]
)
def test_unknown_profile_is_rejected(profile):
    with pytest.raises(ValueError, match="unknown profile"):
        TimingEngine(profile=profile)


@pytest.mark.parametrize("profile", sorted(te.PROFILE_MULTIPLIERS))
def test_known_profiles_are_accepted(profile):
    engine = TimingEngine(profile=profile)
    assert engine.profile == profile
    assert engine.multiplier == te.PROFILE_MULTIPLIERS[profile]


@pytest.mark.parametrize(
    "target", [te.AR1_PHI, te.AR1_PHI + 0.01, 0.95, 1.0, 2.0, -0.01, -1.0]
)
def test_target_autocorrelation_outside_the_range_is_rejected(target):
    # The emitted autocorrelation is a fraction of the latent persistence, so a
    # target at or above phi is unreachable and asking for it is a bug in the
    # caller, not something to silently clamp.
    with pytest.raises(ValueError, match="target_autocorrelation"):
        TimingEngine(target_autocorrelation=target)


@pytest.mark.parametrize("target", [0.0, 0.1, 0.5, 0.89])
def test_reachable_targets_are_accepted(target):
    assert TimingEngine(target_autocorrelation=target).target_autocorrelation == target


def test_a_target_below_the_digraph_floor_needs_no_latent_process():
    # Ordinary text supplies some autocorrelation on its own through the shared
    # key between consecutive digraphs, so asking for zero leaves var_a at zero
    # rather than going negative.
    engine = TimingEngine(seed=0, target_autocorrelation=0.0)
    assert engine.var_a == 0.0
    assert engine.sigma_a == 0.0


# --- determinism and RNG isolation -------------------------------------------


@pytest.mark.parametrize("seed", [0, 1, 42, 20240617])
def test_same_seed_gives_identical_timings(seed, prose):
    first = type_out(TimingEngine(seed=seed), prose)
    second = type_out(TimingEngine(seed=seed), prose)
    assert first == second


def test_different_seeds_give_different_timings(prose):
    first = type_out(TimingEngine(seed=1), prose)
    second = type_out(TimingEngine(seed=2), prose)
    assert first != second


def test_interleaving_another_engine_does_not_change_the_output(prose):
    baseline = type_out(TimingEngine(seed=5), prose)

    subject = TimingEngine(seed=5)
    type_out(TimingEngine(seed=6), prose)
    assert type_out(subject, prose) == baseline


def test_churning_the_global_rngs_does_not_change_the_output(prose):
    baseline = type_out(TimingEngine(seed=5), prose)

    np.random.seed(123)
    np.random.normal(size=1000)
    random.seed(123)
    for _ in range(1000):
        random.random()

    subject = TimingEngine(seed=5)

    np.random.seed(456)
    np.random.normal(size=1000)
    random.seed(456)

    assert type_out(subject, prose) == baseline


def test_generating_does_not_disturb_the_global_rngs(prose):
    np.random.seed(7)
    random.seed(7)
    numpy_before = np.random.get_state()
    random_before = random.getstate()

    type_out(TimingEngine(seed=5), prose)

    after = np.random.get_state()
    assert after[0] == numpy_before[0]
    assert np.array_equal(after[1], numpy_before[1])
    assert after[2:] == numpy_before[2:]
    assert random.getstate() == random_before


# --- dwell -------------------------------------------------------------------

# 200000 draws puts the standard error of the mean at 20/sqrt(200000) = 0.045ms,
# so a 1ms tolerance on the mean is more than twenty standard errors. The sd
# tolerance is 1ms against an analytic 20ms, likewise far outside the sampling
# noise. The truncation at 40ms is 3.8 sigma out and shifts the mean by well
# under 0.01ms, which is why the nominal values are used rather than the
# truncated-normal moments.
DWELL_SAMPLES = 200_000


@pytest.mark.slow
def test_dwell_distribution_matches_the_documented_normal():
    engine = TimingEngine(seed=20240617)
    dwells = [engine._sample_dwell() for _ in range(DWELL_SAMPLES)]

    assert statistics.mean(dwells) == pytest.approx(te.DWELL_MEAN, abs=1.0)
    assert statistics.stdev(dwells) == pytest.approx(te.DWELL_STD, abs=1.0)


@pytest.mark.slow
def test_no_dwell_falls_below_the_floor():
    engine = TimingEngine(seed=99)
    dwells = [engine._sample_dwell() for _ in range(DWELL_SAMPLES)]
    assert min(dwells) >= te.DWELL_FLOOR
    # And the floor is not a point mass: resampling, not clamping.
    assert sum(1 for d in dwells if d == te.DWELL_FLOOR) == 0


def test_emitted_dwell_respects_the_floor_on_every_profile(prose):
    for profile in te.PROFILE_MULTIPLIERS:
        engine = TimingEngine(profile=profile, seed=1)
        multiplier = te.PROFILE_MULTIPLIERS[profile]
        for timing in type_out(engine, prose):
            assert timing.dwell_ms >= te.DWELL_FLOOR * multiplier


# --- autocorrelation ---------------------------------------------------------

# Averaged over 20 seeds the per-seed sd of ~0.05 becomes ~0.011 on the mean,
# so a tolerance of 0.04 on the average is well over three standard errors
# while still being tight enough to catch a model that has stopped controlling
# the quantity at all.
AUTOCORRELATION_TOLERANCE = 0.04


@pytest.mark.slow
@pytest.mark.parametrize("target", [0.20, 0.35, 0.50])
def test_achieved_autocorrelation_matches_the_target(target, prose):
    text = prose * 6
    achieved = []
    for seed in SEEDS:
        engine = TimingEngine(seed=seed, target_autocorrelation=target)
        timings = type_out(engine, text)
        # Drop the first interval: it has no digraph predecessor, so it is not
        # part of the rhythm the target describes.
        achieved.append(lag1_autocorrelation([t.iki_ms for t in timings[1:]]))

    assert statistics.mean(achieved) == pytest.approx(
        target, abs=AUTOCORRELATION_TOLERANCE
    )


@pytest.mark.slow
def test_a_higher_target_gives_a_higher_achieved_autocorrelation(prose):
    text = prose * 6

    def achieved(target: float) -> float:
        return statistics.mean(
            lag1_autocorrelation(
                [
                    t.iki_ms
                    for t in type_out(
                        TimingEngine(seed=seed, target_autocorrelation=target),
                        text,
                    )[1:]
                ]
            )
            for seed in SEEDS
        )

    low, middle, high = achieved(0.20), achieved(0.35), achieved(0.50)
    assert low < middle < high


def test_calibrate_ignores_a_sequence_too_short_to_estimate_from():
    engine = TimingEngine(seed=1)
    before = engine.var_a
    engine.calibrate("abc")
    assert engine.var_a == before


def test_calibrate_handles_a_single_repeated_digraph():
    # Every digraph the same class means the base sequence has no variance;
    # the solve has to cope rather than divide by zero.
    engine = TimingEngine(seed=1)
    engine.calibrate("ab" * 40)
    assert math.isfinite(engine.var_a)
    assert engine.var_a >= 0.0


# --- typing speed ------------------------------------------------------------

# The lead measured 51.98 WPM for `average` on English prose. Across the 20
# seeds here the per-seed sd is about 1.9 WPM, so the mean has a standard error
# near 0.4; a tolerance of 4 WPM is ten standard errors and still tight enough
# that a mis-calibrated engine fails.
WPM_TOLERANCE = 4.0


@pytest.mark.slow
def test_average_profile_types_at_the_target_rate(prose):
    text = prose * 4
    rates = [profile_wpm("average", text, seed) for seed in SEEDS]
    assert statistics.mean(rates) == pytest.approx(
        te.TARGET_WPM_AVERAGE, abs=WPM_TOLERANCE
    )


@pytest.mark.slow
def test_profiles_are_ordered_slow_average_fast(prose):
    text = prose * 4
    rates = {
        profile: statistics.mean(profile_wpm(profile, text, seed) for seed in SEEDS)
        for profile in ("slow", "average", "fast")
    }
    assert rates["slow"] < rates["average"] < rates["fast"]
    # And by roughly the amounts the profile multipliers promise.
    assert rates["slow"] == pytest.approx(35.0, abs=WPM_TOLERANCE)
    assert rates["fast"] == pytest.approx(74.0, abs=WPM_TOLERANCE + 2.0)


def test_no_interval_falls_below_the_physical_floor(prose):
    for profile in te.PROFILE_MULTIPLIERS:
        engine = TimingEngine(profile=profile, seed=3)
        for timing in type_out(engine, prose * 2):
            assert timing.iki_ms >= te.MIN_IKI_MS


def test_intervals_are_finite_and_positive_for_unmapped_characters():
    engine = TimingEngine(seed=1)
    for timing in type_out(engine, "日本語のテキスト \U0001f642 привет"):
        assert math.isfinite(timing.iki_ms) and timing.iki_ms > 0.0
        assert math.isfinite(timing.dwell_ms) and timing.dwell_ms > 0.0


# --- keyboard structure ------------------------------------------------------


@pytest.mark.slow
@pytest.mark.parametrize(
    "bigram,control",
    [
        # Each pair is the same digraph class (opposite hands, so the only
        # difference is whether the pair is in COMMON_BIGRAMS) with the same
        # leading key, which keeps everything but the bigram effect equal.
        ("th", "tj"),
        ("he", "hw"),
        ("in", "ib"),
        ("er", "ec"),
    ],
)
def test_common_bigrams_are_faster_than_matched_non_bigrams(bigram, control):
    assert bigram in te.COMMON_BIGRAMS
    assert control not in te.COMMON_BIGRAMS

    fast = mean_digraph_interval(*bigram)
    slow = mean_digraph_interval(*control)
    assert fast < slow, f"{bigram} ({fast:.1f}ms) not faster than {control} ({slow:.1f}ms)"


# Same-finger pairs, avoiding anything in COMMON_BIGRAMS so the bigram speedup
# does not confound the comparison.
SAME_FINGER_DIGRAPHS = [("d", "e"), ("f", "r"), ("j", "u"), ("k", "i"), ("l", "o")]
ALTERNATE_HAND_DIGRAPHS = [("d", "k"), ("f", "j"), ("s", "l"), ("a", "p"), ("v", "m")]


@pytest.mark.slow
def test_same_finger_digraphs_are_slower_than_alternate_hand_ones():
    same_finger = statistics.mean(
        mean_digraph_interval(a, b) for a, b in SAME_FINGER_DIGRAPHS
    )
    alternate = statistics.mean(
        mean_digraph_interval(a, b) for a, b in ALTERNATE_HAND_DIGRAPHS
    )
    assert same_finger > alternate


@pytest.mark.parametrize("pair", SAME_FINGER_DIGRAPHS)
def test_same_finger_pairs_really_are_same_finger(pair):
    engine = TimingEngine(seed=0)
    first, second = pair
    assert engine._finger(first) == engine._finger(second)
    assert engine._hand(first) == engine._hand(second)
    assert engine._base_delay(first, second) == te.BASE_DELAY_SAME_FINGER
    assert first + second not in te.COMMON_BIGRAMS


@pytest.mark.parametrize("pair", ALTERNATE_HAND_DIGRAPHS)
def test_alternate_hand_pairs_really_alternate(pair):
    engine = TimingEngine(seed=0)
    first, second = pair
    assert engine._hand(first) != engine._hand(second)
    assert engine._base_delay(first, second) == te.BASE_DELAY_ALTERNATE_HAND
    assert first + second not in te.COMMON_BIGRAMS


def test_base_delays_are_ordered_by_difficulty():
    assert (
        te.BASE_DELAY_SPACE
        < te.BASE_DELAY_ALTERNATE_HAND
        < te.BASE_DELAY_SAME_HAND_DIFF_FINGER
        < te.BASE_DELAY_SAME_FINGER
    )


def test_shifted_characters_cost_more_than_unshifted_ones():
    engine = TimingEngine(seed=0)
    assert engine._effective_base("a", "K") > engine._effective_base("a", "k")
    assert engine._effective_base("a", ":") > engine._effective_base("a", ";")


@pytest.mark.parametrize("char", list("!@#$%^&*()_+{}|:\"<>?~"))
def test_shifted_punctuation_is_recognised(char):
    assert TimingEngine._needs_shift(char)
    assert TimingEngine._base_key(char) in te.SHIFTED_CHARS.values()


def test_a_character_with_no_finger_mapping_gets_the_neutral_baseline():
    engine = TimingEngine(seed=0)
    assert engine._hand("日") == "unknown"
    assert engine._base_delay("日", "a") == te.BASE_DELAY_SAME_HAND_DIFF_FINGER


# --- backspace ---------------------------------------------------------------


def test_backspace_is_accepted_and_produces_a_plausible_interval():
    engine = TimingEngine(seed=1)
    engine.next_keystroke("a")
    timing = engine.next_keystroke(BACKSPACE)

    assert isinstance(timing, KeyTiming)
    assert te.MIN_IKI_MS <= timing.iki_ms < 5000.0
    assert timing.dwell_ms >= te.DWELL_FLOOR
    assert math.isfinite(timing.iki_ms)


def test_backspace_is_a_right_hand_pinky_reach():
    engine = TimingEngine(seed=1)
    assert engine._hand(BACKSPACE) == "right"
    assert engine._finger(BACKSPACE) == te.BACKSPACE_FINGER
    assert not engine._needs_shift(BACKSPACE)


def test_a_run_of_backspaces_is_a_same_finger_sequence():
    engine = TimingEngine(seed=1)
    assert engine._base_delay(BACKSPACE, BACKSPACE) == te.BASE_DELAY_SAME_FINGER


def test_newline_is_handled_like_a_key():
    engine = TimingEngine(seed=1)
    assert engine._hand("\n") == "right"
    assert engine._finger("\n") == te.ENTER_FINGER
    timing = type_out(engine, "a\nb")[-1]
    assert timing.iki_ms > 0.0


# --- rollover ----------------------------------------------------------------


def test_rollover_only_happens_between_opposite_hands(prose):
    engine = TimingEngine(seed=2)
    engine.calibrate(prose * 4)
    previous = None
    for char in prose * 4:
        timing = engine.next_keystroke(char)
        if timing.rollover:
            assert previous is not None
            hands = {engine._hand(previous), engine._hand(char)}
            assert len(hands) == 2
            assert "unknown" not in hands
        previous = char


def test_rollover_overlap_stays_within_the_documented_range(prose):
    engine = TimingEngine(seed=2)
    low, high = te.ROLLOVER_OVERLAP_RANGE
    for timing in type_out(engine, prose * 4):
        if timing.rollover:
            assert low <= timing.prev_overlap_ms <= high


def test_key_timing_rollover_property():
    assert not KeyTiming(iki_ms=100.0, dwell_ms=100.0).rollover
    assert KeyTiming(iki_ms=100.0, dwell_ms=100.0, prev_overlap_ms=5.0).rollover


# --- state resets ------------------------------------------------------------


def test_reset_context_forgets_the_previous_key():
    engine = TimingEngine(seed=1)
    engine.next_keystroke("a")
    assert engine.prev_key == "a"
    engine.reset_context()
    assert engine.prev_key is None
    # And the next keystroke cannot roll over something it has forgotten.
    assert engine.next_keystroke("k").prev_overlap_ms == 0.0


def test_reset_context_keeps_the_latent_speed():
    # Typing speed persists across a think pause; only the digraph context is
    # lost. Resetting it would flatten the rhythm at every sentence boundary.
    engine = TimingEngine(seed=1, target_autocorrelation=0.5)
    for char in "hello there":
        engine.next_keystroke(char)
    before = engine._a
    engine.reset_context()
    assert engine._a == before


def test_reset_speed_redraws_the_latent_speed():
    engine = TimingEngine(seed=1, target_autocorrelation=0.5)
    for char in "hello there":
        engine.next_keystroke(char)
    before = engine._a
    engine.reset_speed()
    assert engine._a != before


def test_reset_speed_is_a_no_op_when_there_is_no_latent_process():
    engine = TimingEngine(seed=1, target_autocorrelation=0.0)
    engine.reset_speed()
    assert engine._a == 0.0


# --- within-document dynamics --------------------------------------------------

# Prose long enough that the clocks get somewhere: prose * 30 is roughly forty
# minutes of active typing, so the fatigue drift (~3% per ten minutes) amounts
# to ~12% by the end - far above the sampling noise of a few percent on a
# windowed mean.
LONG_STREAM_REPEATS = 30


@pytest.mark.parametrize(
    "kwarg,bad",
    [
        ("fatigue_rate", -0.001),
        ("fatigue_rate", float("nan")),
        ("warmup_strength", -0.001),
        ("warmup_strength", 1.0),
        ("familiarity_boost", -0.001),
        ("familiarity_boost", 1.0),
    ],
)
def test_invalid_within_document_dynamics_are_rejected(kwarg, bad):
    with pytest.raises(ValueError, match=kwarg):
        TimingEngine(**{kwarg: bad})


@pytest.mark.parametrize("value", [0.0, 0.05, 0.999])
def test_valid_within_document_dynamics_are_accepted(value):
    engine = TimingEngine(
        fatigue_rate=value, warmup_strength=value, familiarity_boost=value
    )
    assert engine.fatigue_rate == value
    assert engine.warmup_strength == value
    assert engine.familiarity_boost == value


def test_within_document_factor_at_the_clock():
    engine = TimingEngine(seed=0, warmup_strength=0.10, fatigue_rate=0.03)
    # At time zero the warmup is fully present and fatigue absent.
    assert engine._within_document_factor() == pytest.approx(1.10)
    engine._active_ms = 600_000.0  # ten minutes of active typing
    assert engine._within_document_factor() == pytest.approx(
        1.03 * (1.0 + 0.10 * math.exp(-24.0))
    )
    # Both disabled: no factor and no clock dependence at all.
    plain = TimingEngine(
        seed=0, warmup_strength=0.0, fatigue_rate=0.0
    )
    plain._active_ms = 600_000.0
    assert plain._within_document_factor() == 1.0


def test_familiarity_speeds_a_repeated_digraph_base():
    engine = TimingEngine(seed=0, familiarity_boost=0.25)
    plain = engine._effective_base("x", "j")
    first_time = engine._effective_base("x", "j", {"ab"})
    repeated = engine._effective_base("x", "j", {"xj"})
    assert first_time == plain
    assert repeated == pytest.approx(plain * 0.75)


@pytest.mark.slow
def test_fatigue_makes_later_intervals_longer(prose):
    # Warmup and familiarity off, so the drift is the only index-dependent
    # effect in the stream. The drift is a document-scale trend and can be
    # masked by a swing of the latent speed in any one seed, so the direction
    # is asserted on the cross-seed mean with a majority cross-check.
    text = prose * LONG_STREAM_REPEATS
    early_means, late_means = [], []
    for seed in SEEDS:
        engine = TimingEngine(seed=seed, warmup_strength=0.0, familiarity_boost=0.0)
        ikis = [t.iki_ms for t in type_out(engine, text)[1:]]
        window = len(ikis) // 10
        early_means.append(statistics.mean(ikis[:window]))
        late_means.append(statistics.mean(ikis[-window:]))

    assert statistics.mean(late_means) > statistics.mean(early_means)
    rising = sum(1 for early, late in zip(early_means, late_means) if late > early)
    assert rising >= len(SEEDS) * 3 // 4


@pytest.mark.slow
def test_warmup_makes_the_first_minute_slower(prose):
    # Fatigue and familiarity off; the first fifteen seconds are then slower
    # than the steady state reached a couple of minutes in. A single seed's
    # early window is short enough for the latent process to drown the
    # effect, so - as with fatigue - the direction is asserted across seeds.
    text = prose * 8
    early_means, steady_means = [], []
    for seed in SEEDS:
        engine = TimingEngine(seed=seed, fatigue_rate=0.0, familiarity_boost=0.0)
        clock = 0.0
        early, steady = [], []
        for timing in type_out(engine, text)[1:]:
            if clock < 15_000.0:
                early.append(timing.iki_ms)
            elif 120_000.0 <= clock < 180_000.0:
                steady.append(timing.iki_ms)
            clock += timing.iki_ms
        early_means.append(statistics.mean(early))
        assert steady, f"seed {seed}: stream never reached the steady window"
        steady_means.append(statistics.mean(steady))

    assert statistics.mean(early_means) > statistics.mean(steady_means)
    slower = sum(
        1 for early, steady in zip(early_means, steady_means) if early > steady
    )
    assert slower >= len(SEEDS) // 2


@pytest.mark.slow
def test_a_repeated_word_gets_faster_with_familiarity(prose):
    # The word's digraphs are unfamiliar on its first occurrence and cached
    # for every later one. The boost is exaggerated so the direction stands
    # far above the per-window noise.
    word = "quetzal"
    block = len(word) + 1
    ratios = []
    for seed in SEEDS:
        engine = TimingEngine(
            seed=seed, familiarity_boost=0.3,
            warmup_strength=0.0, fatigue_rate=0.0,
        )
        timings = [engine.next_keystroke(c) for c in (word + " ") * 40]
        first = [t.iki_ms for t in timings[1:block]]
        later = [
            t.iki_ms
            for i in range(5, 40)
            for t in timings[i * block + 1:(i + 1) * block]
        ]
        ratios.append(statistics.mean(later) / statistics.mean(first))
    assert statistics.mean(ratios) < 0.85


def test_reset_speed_restarts_the_fatigue_and_warmup_clock(prose):
    engine = TimingEngine(seed=1)
    for char in prose:
        engine.next_keystroke(char)
    assert engine._active_ms > 0.0
    engine.reset_speed()
    assert engine._active_ms == 0.0


def test_reset_speed_keeps_the_familiarity_cache(prose):
    # Familiarity belongs to the document, not the sitting: a rest resets the
    # clock but not the memory of what has been typed.
    engine = TimingEngine(seed=1)
    for char in prose:
        engine.next_keystroke(char)
    assert engine._familiar_digraphs
    engine.reset_speed()
    assert engine._familiar_digraphs


def test_disabling_every_dynamic_leaves_first_occurrences_untouched(prose):
    # With all three effects at zero the within-document factor is exactly 1
    # and no digraph is cached, so the stream is the stationary model alone.
    for seed in (0, 1):
        engine = TimingEngine(
            seed=seed, fatigue_rate=0.0, warmup_strength=0.0,
            familiarity_boost=0.0,
        )
        type_out(engine, prose)
        assert engine._familiar_digraphs == set()
        assert engine._within_document_factor() == 1.0


# --- keyboard geometry stays self-consistent ---------------------------------


def test_no_key_is_on_both_hands():
    assert not (te.LEFT_HAND_KEYS & te.RIGHT_HAND_KEYS)


def test_hand_and_finger_agree_about_every_mapped_key():
    """FINGER_MAP numbers 1-5 left, 6-10 right, 11 thumb.

    'b' used to sit in RIGHT_HAND_KEYS while FINGER_MAP gave it finger 5, a
    left-hand finger. The two disagreeing made the same-finger branch of
    _base_delay unreachable for it, so 'gb' and 'tb' - one finger travelling,
    the slowest digraph class there is - were priced as alternate-hand, the
    fastest.
    """
    engine = TimingEngine(seed=1)
    for key, finger in te.FINGER_MAP.items():
        hand = engine._hand(key)
        if finger == 11:
            expected = "thumb"
        elif 1 <= finger <= 5:
            expected = "left"
        else:
            expected = "right"
        assert hand == expected, (
            f"{key!r} is finger {finger} but _hand says {hand!r}"
        )


def test_same_finger_digraphs_are_priced_as_same_finger():
    """The slowest class has to be reachable for every finger on the board."""
    engine = TimingEngine(seed=1)
    by_finger = {}
    for key, finger in te.FINGER_MAP.items():
        if finger != 11 and key.isalnum():
            by_finger.setdefault(finger, []).append(key)

    for finger, keys in sorted(by_finger.items()):
        if len(keys) < 2:
            continue
        first, second = keys[0], keys[1]
        assert engine._base_delay(first, second) == te.BASE_DELAY_SAME_FINGER, (
            f"{first!r}{second!r} share finger {finger} but are not priced as one"
        )


def test_b_is_a_left_index_key():
    engine = TimingEngine(seed=1)
    assert engine._hand("b") == "left"
    assert te.FINGER_MAP["b"] == te.FINGER_MAP["g"] == te.FINGER_MAP["t"]
    assert engine._base_delay("g", "b") == te.BASE_DELAY_SAME_FINGER
    assert engine._base_delay("b", "n") == te.BASE_DELAY_ALTERNATE_HAND
