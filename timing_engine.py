"""
Project Aletheia - Timing Engine
Generates micro-level keystroke timing data based on research baselines.
Adapted from Lax3n/HumanTyping, Make1tRain/HumanType, andyless/human-browser-use.
"""

import math
import random
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
from scipy.stats import gumbel_r
import numpy as np

# --- Configuration Constants (Research Baselines) ---

# Salthouse (1986) Baselines in ms
BASE_DELAY_ALTERNATE_HAND = 136.0
BASE_DELAY_SAME_HAND_DIFF_FINGER = 168.0
BASE_DELAY_SAME_FINGER = 218.0

# Dhakal et al. CHI 2018 Baselines
DWELL_MEAN = 116.0
DWELL_STD = 20.0

# Gumbel Distribution Parameters (Migdal & Rosenberger 2019)
# Tuned so median is ~150ms for average typing
GUMBEL_LOC = 140.0
GUMBEL_SCALE = 25.0

# AR(1) Process Parameters for Burstiness
AR1_PHI = 0.7  # Lag-1 autocorrelation
AR1_SIGMA = 0.15

# Bigram Speedup
SPEED_BOOST_BIGRAM = 0.4
COMMON_BIGRAMS = {
    "th", "he", "in", "er", "an", "re", "on", "at", "en", "nd",
    "ti", "es", "or", "te", "of", "ed", "is", "it", "al", "ar",
    "st", "to", "nt", "ng", "se", "ha", "as", "ou", "io", "le"
}

# Profile Multipliers
PROFILE_MULTIPLIERS = {
    "slow": 1.5,
    "average": 1.0,
    "fast": 0.7
}

# QWERTY Layout Definition (for hand/finger calculation)
# Rows: Top, Home, Bottom
QWERTY_LAYOUT = [
    ['q', 'w', 'e', 'r', 't', 'y', 'u', 'i', 'o', 'p'],
    ['a', 's', 'd', 'f', 'g', 'h', 'j', 'k', 'l'],
    ['z', 'x', 'c', 'v', 'b', 'n', 'm']
]

LEFT_HAND_KEYS = set("qwertasdfgzxcv")
RIGHT_HAND_KEYS = set("yuiophjklbnm")

# Finger mapping (simplified: 1-5 left, 6-10 right)
FINGER_MAP = {
    'q': 1, 'a': 1, 'z': 1,
    'w': 2, 's': 2, 'x': 2,
    'e': 3, 'd': 3, 'c': 3,
    'r': 4, 'f': 4, 'v': 4, 't': 5, 'g': 5, 'b': 5,
    'y': 6, 'h': 6, 'n': 6, 'u': 7, 'j': 7, 'm': 7,
    'i': 8, 'k': 8, ',': 8,
    'o': 9, 'l': 9, '.': 9,
    'p': 10, ';': 10, '/': 10
}

@dataclass
class KeystrokeEvent:
    char: str
    keydown_ts: float
    keyup_ts: float
    dwell_time: float
    flight_time: float  # Time since previous keyup
    is_typo_correction: bool = False

class TimingEngine:
    def __init__(self, profile: str = "average", seed: Optional[int] = None):
        if seed is not None:
            random.seed(seed)
            np.random.seed(seed)
        
        self.profile = profile
        self.multiplier = PROFILE_MULTIPLIERS.get(profile, 1.0)
        
        # AR(1) State
        self.speed_state = 1.0
        
        # Previous key info for digraph calculation
        self.prev_key: Optional[str] = None
        self.prev_keyup_ts: float = 0.0
        
    def _get_key_hand(self, key: str) -> str:
        key = key.lower()
        if key in LEFT_HAND_KEYS: return "left"
        if key in RIGHT_HAND_KEYS: return "right"
        return "unknown"
    
    def _get_finger(self, key: str) -> int:
        return FINGER_MAP.get(key.lower(), 0)
    
    def _calculate_base_delay(self, prev_key: str, curr_key: str) -> float:
        """Calculate base delay based on Salthouse (1986) digraph rules."""
        p_hand = self._get_key_hand(prev_key)
        c_hand = self._get_key_hand(curr_key)
        p_finger = self._get_finger(prev_key)
        c_finger = self._get_finger(curr_key)
        
        if p_hand == "unknown" or c_hand == "unknown":
            return BASE_DELAY_SAME_HAND_DIFF_FINGER
            
        if p_hand != c_hand:
            return BASE_DELAY_ALTERNATE_HAND
        
        if p_finger == c_finger:
            return BASE_DELAY_SAME_FINGER
        
        return BASE_DELAY_SAME_HAND_DIFF_FINGER
    
    def _sample_gumbel_delay(self, base_delay: float) -> float:
        """Sample delay from Gumbel distribution scaled by base."""
        # Adjust loc to match base delay roughly at median
        # Gumbel median = loc + scale * ln(-ln(0.5)) ≈ loc - 0.3665 * scale
        median_offset = -0.3665 * GUMBEL_SCALE
        loc_adjusted = base_delay - median_offset
        
        sample = gumbel_r.rvs(loc=loc_adjusted, scale=GUMBEL_SCALE)
        return max(50.0, sample) # Clamp minimum
    
    def _update_ar1_speed(self) -> float:
        """Update AR(1) latent speed process."""
        epsilon = np.random.normal(1.0, AR1_SIGMA)
        self.speed_state = AR1_PHI * self.speed_state + (1 - AR1_PHI) * epsilon
        self.speed_state = np.clip(self.speed_state, 0.6, 1.4)
        return self.speed_state
    
    def _sample_dwell(self) -> float:
        """Sample dwell time from Normal distribution (Dhakal et al.)."""
        dwell = np.random.normal(DWELL_MEAN, DWELL_STD)
        return max(40.0, dwell)
    
    def _check_rollover(self, prev_key: str, curr_key: str) -> bool:
        """Check if rollover occurs (30% for opposite-hand digraphs)."""
        p_hand = self._get_key_hand(prev_key)
        c_hand = self._get_key_hand(curr_key)
        if p_hand != "unknown" and c_hand != "unknown" and p_hand != c_hand:
            return random.random() < 0.30
        return False
    
    def generate_keystrokes(self, text: str, typo_corrections: List[Tuple[int, str]] = None) -> List[KeystrokeEvent]:
        """
        Generate detailed keystroke events for the given text.
        typo_corrections: List of (index, original_char) where typos occurred.
        """
        events = []
        current_time = 0.0
        typo_set = {idx for idx, _ in typo_corrections} if typo_corrections else set()
        
        for i, char in enumerate(text):
            # Skip non-typable characters for this simulation
            if char not in FINGER_MAP and char not in [' ', '\n', '.', ',', '!', '?', ';', ':', '\'', '"', '(', ')']:
                # Handle common punctuation not in finger map
                if char not in [' ', '\n']:
                    pass # Treat as instant for now
            
            # Apply AR(1) speed factor
            speed_factor = self._update_ar1_speed()
            
            # Calculate base delay
            base_delay = 150.0 # Default
            if self.prev_key:
                base_delay = self._calculate_base_delay(self.prev_key, char)
            
            # Apply bigram speedup
            if self.prev_key:
                bigram = (self.prev_key + char).lower()
                if bigram in COMMON_BIGRAMS:
                    base_delay *= (1 - SPEED_BOOST_BIGRAM)
            
            # Sample flight time
            flight_time = self._sample_gumbel_delay(base_delay)
            flight_time /= speed_factor # Faster speed = lower delay
            flight_time *= self.multiplier # Profile adjustment
            
            # Anti-quantization jitter
            jitter = random.uniform(-2.0, 2.0)
            flight_time += jitter
            flight_time = max(10.0, flight_time)
            
            current_time += flight_time
            
            # Check rollover
            is_rollover = False
            if self.prev_key:
                is_rollover = self._check_rollover(self.prev_key, char)
            
            # Keydown timestamp
            keydown_ts = current_time
            if is_rollover and len(events) > 0:
                # Overlap with previous key's up time
                keydown_ts = max(current_time, events[-1].keyup_ts - 10)
            
            # Dwell time
            dwell_time = self._sample_dwell() / speed_factor
            dwell_time *= self.multiplier
            
            keyup_ts = keydown_ts + dwell_time
            
            is_correction = i in typo_set
            events.append(KeystrokeEvent(
                char=char,
                keydown_ts=keydown_ts,
                keyup_ts=keyup_ts,
                dwell_time=dwell_time,
                flight_time=flight_time,
                is_typo_correction=is_correction
            ))
            
            self.prev_key = char
            self.prev_keyup_ts = keyup_ts
        
        return events

def run_test():
    print("--- Timing Engine Test ---")
    engine_avg = TimingEngine(profile="average", seed=42)
    text = "hello world"
    
    events = engine_avg.generate_keystrokes(text)
    
    total_time = events[-1].keyup_ts if events else 0
    wpm = (len(text) / 5) / (total_time / 60000) if total_time > 0 else 0
    
    print(f"Text: '{text}'")
    print(f"Chars: {len(text)}")
    print(f"Total Time: {total_time:.2f} ms")
    print(f"WPM: {wpm:.1f}")
    print(f"Avg Flight: {sum(e.flight_time for e in events)/len(events):.2f} ms")
    print(f"Avg Dwell: {sum(e.dwell_time for e in events)/len(events):.2f} ms")
    
    # Test profiles
    print("\nProfile Comparison:")
    for prof in ["slow", "average", "fast"]:
        eng = TimingEngine(profile=prof, seed=42)
        evts = eng.generate_keystrokes(text)
        t = evts[-1].keyup_ts if evts else 0
        w = (len(text) / 5) / (t / 60000) if t > 0 else 0
        print(f"  {prof:8}: {t:7.1f} ms, {w:5.1f} WPM")

if __name__ == "__main__":
    run_test()
