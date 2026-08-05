"""
Project Aletheia - Macro Scripter
Generates high-level writing process events (bursts, pauses, revisions).
Ensures the final text state exactly matches the input.
Based on Chenoweth & Hayes / Leijten & Van Waes literature.
"""

import random
import math
from typing import List, Tuple, Dict, Any, Optional
from dataclasses import dataclass

@dataclass
class ScriptEvent:
    op: str  # TYPE, PAUSE, DELETE, JUMP, SELECT, REPLACE, SESSION_GAP
    data: Any = None
    timestamp_ms: float = 0.0

class MacroScripter:
    def __init__(self, seed: Optional[int] = None):
        if seed is not None:
            random.seed(seed)
        
        # Parameters from Chenoweth & Hayes / Leijten & Van Waes
        self.p_burst_min, self.p_burst_max = 8, 13  # P-burst: 8-13 words
        self.r_burst_min, self.r_burst_max = 3, 7   # R-burst: 3-7 words
        self.r_burst_prob = 0.20                    # 20% of bursts are revision bursts
        
        # Pause distributions (lognormal mu, sigma) - ms
        self.pause_word = (4.5, 0.4)      # ~90ms median
        self.pause_clause = (5.2, 0.5)    # ~180ms median
        self.pause_sentence = (6.2, 0.6)  # ~500ms median
        self.pause_paragraph = (7.0, 0.7) # ~1100ms median
        
        # Revision parameters
        self.deletion_rate = 0.15  # 15% of chars typed get deleted eventually
        self.typo_rate = 0.03      # 3% typo injection
        
        # Neighbor keys for typo injection
        self.neighbor_keys = {
            'e': ['w', 'r', 'd', 's', 'f'], 't': ['r', 'y', 'f', 'g'], 
            'y': ['t', 'u', 'g', 'h'], 'u': ['y', 'i', 'h', 'j'],
            'i': ['u', 'o', 'j', 'k'], 'o': ['i', 'p', 'k', 'l'],
            'p': ['o', 'l', ';'], 'a': ['q', 'w', 's', 'z', 'x'],
            's': ['a', 'w', 'd', 'z', 'x', 'c'], 'd': ['s', 'e', 'f', 'x', 'c', 'v'],
            'f': ['d', 'r', 'g', 'c', 'v', 'b'], 'g': ['f', 't', 'h', 'v', 'b', 'n'],
            'h': ['g', 'y', 'j', 'b', 'n', 'm'], 'j': ['h', 'u', 'k', 'n', 'm'],
            'k': ['j', 'i', 'l', 'm'], 'l': ['k', 'o', ';', ','],
            'z': ['a', 's', 'x'], 'x': ['z', 's', 'd', 'c'],
            'c': ['x', 'd', 'f', 'v'], 'v': ['c', 'f', 'g', 'b'],
            'b': ['v', 'g', 'h', 'n'], 'n': ['b', 'h', 'j', 'm'],
            'm': ['n', 'j', 'k', ',']
        }

    def _lognormal_sample(self, mu: float, sigma: float) -> float:
        """Generate lognormal sample for pause duration."""
        u1 = max(random.random(), 1e-10)
        u2 = random.random()
        z0 = math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)
        normal_val = mu + sigma * z0
        return math.exp(normal_val)

    def _get_pause(self, context: str) -> float:
        """Determine pause based on punctuation context."""
        if context in ['\n', '\n\n']:
            return self._lognormal_sample(*self.pause_paragraph)
        elif context in ['.', '!', '?']:
            return self._lognormal_sample(*self.pause_sentence)
        elif context in [',', ';', ':']:
            return self._lognormal_sample(*self.pause_clause)
        else:
            return self._lognormal_sample(*self.pause_word)

    def _inject_typo(self, char: str) -> Tuple[str, bool]:
        """Inject a neighbor-key typo."""
        if random.random() > self.typo_rate or char.lower() not in self.neighbor_keys:
            return char, False
        
        typo_char = random.choice(self.neighbor_keys[char.lower()])
        if char.isupper():
            typo_char = typo_char.upper()
        return typo_char, True

    def generate_script(self, text: str) -> Tuple[List[ScriptEvent], List[Tuple[int, str]]]:
        """
        Generate a sequence of operations that results in `text`.
        Returns: (script, typo_corrections) where typo_corrections is list of (index, original_char)
        """
        script = []
        typo_corrections = []
        
        # Pre-process text into tokens (words + punctuation)
        # Simple tokenization: split by spaces but keep punctuation attached
        tokens = []
        current_token = ""
        for char in text:
            if char == ' ':
                if current_token:
                    tokens.append(current_token)
                    current_token = ""
                tokens.append(' ')
            elif char == '\n':
                if current_token:
                    tokens.append(current_token)
                    current_token = ""
                tokens.append('\n')
            else:
                current_token += char
        if current_token:
            tokens.append(current_token)
        
        # Session gap logic
        session_gap_interval = max(150, len(text) // 4)
        chars_processed = 0
        next_gap_at = session_gap_interval
        
        # Burst logic
        word_count_in_burst = 0
        burst_limit = random.randint(self.p_burst_min, self.p_burst_max)
        is_r_burst = random.random() < self.r_burst_prob
        if is_r_burst:
            burst_limit = random.randint(self.r_burst_min, self.r_burst_max)
        
        global_char_index = 0
        
        for token in tokens:
            # Check session gap
            if chars_processed >= next_gap_at and len(script) > 0:
                gap_hours = random.choice([0.5, 1, 2, 4, 24, 48])
                script.append(ScriptEvent("SESSION_GAP", data={"hours": gap_hours}))
                next_gap_at += session_gap_interval
            
            # Handle space - just add pause and type space
            if token == ' ':
                pause_dur = self._get_pause(' ')
                script.append(ScriptEvent("PAUSE", data={"ms": pause_dur}))
                script.append(ScriptEvent("TYPE", data=' '))
                chars_processed += 1
                global_char_index += 1
                continue
            
            # Handle newline
            if token == '\n':
                pause_dur = self._get_pause('\n')
                script.append(ScriptEvent("PAUSE", data={"ms": pause_dur}))
                script.append(ScriptEvent("TYPE", data='\n'))
                chars_processed += 1
                global_char_index += 1
                continue
            
            # Process word token
            word_chars = list(token)
            for i, char in enumerate(word_chars):
                # Check burst limit (only at word boundaries)
                if i == 0 and word_count_in_burst >= burst_limit:
                    # End burst with pause
                    context = token[-1] if token else ' '
                    if context in '.!?': context = '.'
                    elif context in ',;:': context = ','
                    pause_dur = self._get_pause(context)
                    script.append(ScriptEvent("PAUSE", data={"ms": pause_dur}))
                    
                    # Start new burst
                    is_r_burst = random.random() < self.r_burst_prob
                    burst_limit = random.randint(
                        self.r_burst_min if is_r_burst else self.p_burst_min,
                        self.r_burst_max if is_r_burst else self.p_burst_max
                    )
                    word_count_in_burst = 0
                
                # Typo injection
                typo_char, is_typo = self._inject_typo(char)
                
                if is_typo:
                    script.append(ScriptEvent("TYPE", data=typo_char))
                    reaction_pause = random.uniform(300, 800)
                    script.append(ScriptEvent("PAUSE", data={"ms": reaction_pause}))
                    script.append(ScriptEvent("DELETE", data=1))
                    script.append(ScriptEvent("TYPE", data=char))
                    typo_corrections.append((global_char_index, char))
                else:
                    script.append(ScriptEvent("TYPE", data=char))
                
                chars_processed += 1
                global_char_index += 1
            
            word_count_in_burst += 1
        
        return script, typo_corrections

    def verify_script(self, text: str, script: List[ScriptEvent]) -> bool:
        """Replay script virtually to ensure it produces exact text."""
        buffer = []
        for event in script:
            if event.op == "TYPE":
                buffer.append(event.data)
            elif event.op == "DELETE":
                if buffer:
                    buffer.pop()
            elif event.op == "REPLACE":
                if isinstance(event.data, dict) and 'text' in event.data:
                    buffer.append(event.data['text'])
        
        result = "".join(buffer)
        return result == text

def run_test():
    print("--- Macro Scripter Test ---")
    test_text = "First sentence. Second sentence."
    scripter = MacroScripter(seed=42)
    script, typos = scripter.generate_script(test_text)
    
    is_valid = scripter.verify_script(test_text, script)
    print(f"Input: '{test_text}'")
    print(f"Script Length: {len(script)} events")
    print(f"Typos Injected: {len(typos)}")
    print(f"Verification: {'PASS' if is_valid else 'FAIL'}")
    
    if not is_valid:
        buffer = []
        for e in script:
            if e.op == "TYPE": buffer.append(e.data)
            elif e.op == "DELETE" and buffer: buffer.pop()
        generated = ''.join(buffer)
        print(f"Generated: '{generated}'")
        print(f"Mismatch at index: ", end="")
        for i, (a, b) in enumerate(zip(test_text, generated)):
            if a != b:
                print(i)
                break
        else:
            print("Length mismatch")
    
    # Test longer text
    print("\n--- Longer Text Test ---")
    long_text = "This is a longer paragraph. It has multiple sentences. We need to verify it works."
    script2, typos2 = scripter.generate_script(long_text)
    valid2 = scripter.verify_script(long_text, script2)
    print(f"Input: '{long_text}'")
    print(f"Verification: {'PASS' if valid2 else 'FAIL'}")
    
    return is_valid and valid2

if __name__ == "__main__":
    success = run_test()
    exit(0 if success else 1)
