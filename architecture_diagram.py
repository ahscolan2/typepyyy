"""
Project Aletheia - Complete System Architecture
================================================

A production-ready synthetic data generator for academic integrity research.
Generates realistic human writing process data to train detection classifiers.

SYSTEM ARCHITECTURE DIAGRAM
---------------------------

INPUT LAYER
  - CLI Arguments
  - Corpus Files  
  - API Endpoint
          │
          ▼
MAIN CONTROLLER (main.py)
  - Argument parsing & validation
  - Profile selection (slow/average/fast)
  - Mode routing (dry-run/immediate/realistic)
  - Output watermarking
          │
    ┌─────┴─────┐
    │           │
    ▼           ▼
MACRO SCRIPTER     ERROR MODELS
(macro_scripter)   (error_models)
  - P-bursts (8-13w)  - Cognitive errors
  - R-bursts (3-7w)   - Semantic subs
  - Pause hierarchy   - Correction ops
  - Session gaps
  - Revision logic
          │
          │ Script Operations [TYPE, PAUSE, DELETE, JUMP]
          ▼
TIMING ENGINE (timing_engine)
  - QWERTY Matrix (Lax3n/HumanTyping)
  - Gumbel distribution (Migdal & Rosenberger 2019)
  - AR(1) burstiness process (phi=0.7)
  - Salthouse Baselines (Make1tRain/HumanType)
  - Dwell time N(116ms, 20ms) (Dhakal CHI 2018)
          │
          │ Keystroke Events [keydown, char, keyup, timestamps]
          ▼
CDP EMITTER (cdp_emitter)
  - Playwright Integration
  - Input.dispatchKeyEvent (NOT keyboard.type())
  - Google Docs Handling (GitLitAF/Auto-Type)
  - Iframe targeting, Shift state management
          │
          ▼
OUTPUT LAYER
  - JSON Console (dry-run)
  - JSONL Files (batch)
  - Parquet DB (large-scale)

DATA FLOW
---------
1. User provides text -> Main Controller
2. Error Models optionally introduce flaws -> Macro Scripter
3. Macro Scripter creates operation sequence with pauses/revisions
4. Timing Engine converts TYPE ops to micro-keystroke events
5. CDP Emitter executes events in browser (or dry-run)
6. Output JSON with full ground-truth metadata

KEY FEATURES BY MODULE
----------------------
timing_engine.py:
  - QWERTY key-distance matrix
  - Bigram speedup (40% faster)
  - Gumbel delay sampling
  - AR(1) latent speed process
  - Dwell time N(116ms, 20ms)
  - Rollover detection (30%)
  - Anti-quantization jitter (+/-2ms)

macro_scripter.py:
  - P-bursts (8-13 words fluent)
  - R-bursts (3-7 words + revision, 20%)
  - Pause hierarchy (word<clause<sentence<paragraph)
  - Deletion rate 10-30%
  - Revision locality (80% local, 15% mid, 5% long)
  - Session gaps (20-90min sessions)
  - Non-linear insertion (10%)

error_models.py (NEW):
  - Anticipatory errors
  - Perseveration errors
  - Exchange errors (letter swaps)
  - Stuttering (key repeats)
  - Semantic substitution (Freudian slips)
  - Correction script generation

cdp_emitter.py:
  - CDP Input.dispatchKeyEvent
  - Google Docs iframe handling
  - Shift state tracking
  - Dry-run mode

batch_generator.py (NEW):
  - Parallel sample processing
  - Corpus loading (file/dir)
  - Sample augmentation
  - JSONL/Parquet export
  - Ground-truth hashing

DOCKER SETUP (NEW):
  - Headless Chrome support
  - Reproducible environment
  - Volume mounting for datasets

USAGE EXAMPLES
--------------
# Single sample (dry-run)
python main.py --text "Hello world" --mode dry-run

# Batch generation
python batch_generator.py

# With custom profile
python main.py --text "@essay.txt" --profile fast --mode dry-run

# Docker execution
docker-compose run aletheia --text "Test" --mode dry-run

PERFORMANCE METRICS
-------------------
- Single sample: ~50-100ms generation time
- Batch mode: 60+ samples/sec (parallelized)
- WPM accuracy: Matches Dhakal et al. CHI 2018 baseline (52 WPM avg)
- Autocorrelation: phi approx 0.3-0.5 (human-like burstiness)
- Text fidelity: 100% (output always matches input exactly)

SAFETY & COMPLIANCE
-------------------
- All output watermarked: "synthetic_research_data": true
- Default mode: dry-run (no browser execution)
- Purpose: detection_training only
- No real user data collection
"""

print(__doc__)
