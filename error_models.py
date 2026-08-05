"""
Advanced Cognitive Error Models for Project Aletheia
Extends basic neighbor-key typos with linguistic and cognitive error patterns.
References: Baayen et al. (2010), Van Severens et al. (2018)
"""

import random
import re
from typing import List, Tuple, Optional

class CognitiveErrorModel:
    """
    Simulates human cognitive errors during typing:
    - Anticipatory errors (typing future letters early)
    - Perseveration errors (repeating previous letters)
    - Exchange errors (swapping adjacent letters)
    - Stuttering (repeating keys)
    - Freudian slips (semantic substitution - simplified)
    """
    
    def __init__(self, error_rate: float = 0.03):
        """
        :param error_rate: Base probability of an error occurring (0.0-1.0)
        """
        self.error_rate = error_rate
        self.vowels = set('aeiouAEIOU')
        
        # Common bigram exchanges (e.g., 'th' -> 'ht')
        self.exchange_pairs = [
            ('th', 'ht'), ('he', 'eh'), ('in', 'ni'), ('er', 're'),
            ('on', 'no'), ('an', 'na'), ('or', 'ro'), ('is', 'si')
        ]
        
        # Common stutter patterns
        self.stutter_chars = set('tprsldkmn')  # Keys often stuttered

    def introduce_errors(self, text: str, context: str = "") -> str:
        """
        Apply a combination of cognitive errors to the text.
        Ensures the final text is still typable but contains realistic flaws.
        
        NOTE: In the Aletheia pipeline, these errors are introduced 
        and then IMMEDIATELY corrected via the macro_scripter's revision logic.
        This function returns the "flawed intermediate state".
        """
        if not text or random.random() > self.error_rate * 5: # Scale up slightly for variety
            return text
            
        error_type = random.choice(['anticipatory', 'perseveration', 'exchange', 'stutter'])
        
        if error_type == 'exchange' and len(text) >= 2:
            # Swap adjacent characters (common in fast typing)
            idx = random.randint(0, len(text) - 2)
            # Avoid swapping spaces or punctuation usually
            if text[idx].isalpha() and text[idx+1].isalpha():
                lst = list(text)
                lst[idx], lst[idx+1] = lst[idx+1], lst[idx]
                return "".join(lst)
                
        elif error_type == 'stutter' and len(text) >= 1:
            # Repeat a character (e.g., "hello" -> "hhello" or "helllo")
            idx = random.randint(0, len(text) - 1)
            if text[idx] in self.stutter_chars:
                lst = list(text)
                lst.insert(idx, text[idx])
                return "".join(lst)
                
        elif error_type == 'anticipatory' and len(text) >= 3:
            # Insert a future character early (e.g., "because" -> "becuase")
            # Simplified: just swap non-adjacent close letters
            idx = random.randint(0, len(text) - 3)
            if text[idx].isalpha() and text[idx+2].isalpha():
                lst = list(text)
                # Swap i and i+2
                lst[idx+1], lst[idx+2] = lst[idx+2], lst[idx+1]
                return "".join(lst)
                
        elif error_type == 'perseveration' and len(text) >= 2:
            # Repeat the previous character (e.g., "the" -> "thee")
            idx = random.randint(1, len(text) - 1)
            lst = list(text)
            lst.insert(idx, lst[idx-1])
            return "".join(lst)
            
        return text

    def generate_correction_script(self, original: str, flawed: str) -> List[dict]:
        """
        Generates a sequence of DELETE/TYPE operations to transform 
        the flawed text back to the original.
        Used by MacroScripter to create R-bursts.
        """
        ops = []
        # Simple diff logic: assume flaw is local
        # Find first difference
        min_len = min(len(original), len(flawed))
        diff_start = 0
        while diff_start < min_len and original[diff_start] == flawed[diff_start]:
            diff_start += 1
            
        # Find last difference
        diff_end_orig = len(original)
        diff_end_flawed = len(flawed)
        while diff_end_orig > diff_start and diff_end_flawed > diff_start:
            if original[diff_end_orig-1] == flawed[diff_end_flawed-1]:
                diff_end_orig -= 1
                diff_end_flawed -= 1
            else:
                break
        
        # Calculate deletions needed
        chars_to_delete = diff_end_flawed - diff_start
        if chars_to_delete > 0:
            ops.append({"type": "DELETE", "count": chars_to_delete})
            
        # Calculate insertions needed
        chars_to_insert = original[diff_start:diff_end_orig]
        if chars_to_insert:
            ops.append({"type": "TYPE", "text": chars_to_insert})
            
        return ops

class SemanticSubstitution:
    """
    Simplified semantic error model (Freudian slips).
    Replaces words with semantically related or phonetically similar words.
    """
    def __init__(self):
        # Tiny lookup table for demo; in production, use WordNet or GloVe
        self.substitutions = {
            "their": ["there", "they're"],
            "there": ["their", "where"],
            "your": ["you're", "yonder"],
            "you're": ["your", "yacht"],
            "its": ["it's", "bits"],
            "it's": ["its", "in"],
            "effect": ["affect", "effectual"],
            "affect": ["effect", "afflict"],
            "then": ["than", "them"],
            "than": ["then", "that"],
            "form": ["from", "farm"],
            "from": ["form", "fog"],
            "public": ["pubic", "publicly"], # Classic dangerous typo
            "manager": ["manger", "management"],
            "expert": ["erpert", "expect"],
        }
    
    def maybe_substitute(self, text: str) -> Tuple[str, Optional[str]]:
        """
        Returns (modified_text, original_word) if a substitution occurs.
        """
        words = re.findall(r'\b\w+\b', text)
        if not words:
            return text, None
            
        word = random.choice(words)
        lower_word = word.lower()
        
        if lower_word in self.substitutions:
            replacement = random.choice(self.substitutions[lower_word])
            # Preserve case
            if word.istitle():
                replacement = replacement.capitalize()
            elif word.isupper():
                replacement = replacement.upper()
                
            new_text = text.replace(word, replacement, 1)
            return new_text, word
            
        return text, None
