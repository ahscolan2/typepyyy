# Project Aletheia

**Synthetic Writing Data Generator for Academic Integrity Research**

## Overview

Project Aletheia is a red-team synthetic data generator that creates high-fidelity simulations of human writing processes in Google Docs. It generates ground-truth datasets with realistic keystroke timing, burst patterns, revisions, and session gaps to train and evaluate defensive ML classifiers.

## Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  macro_scripter │────▶│  timing_engine   │────▶│   cdp_emitter   │
│  (Chenoweth &   │     │  (Lax3n/Human    │     │  (GitLitAF/     │
│   Hayes model)  │     │   Typing +       │     │   Auto-Type)    │
│                 │     │   Gumbel/AR(1))  │     │                 │
└─────────────────┘     └──────────────────┘     └─────────────────┘
        │                       │                        │
        ▼                       ▼                        ▼
  P-bursts/R-bursts      Micro-timings           CDP Events
  Pause hierarchy        Dwell/Flight            JSON Output
  Session gaps           Rollover/Jitter         (dry-run)
```

## Features

### Macro Scripter (`macro_scripter.py`)
- **P-bursts**: 8-13 words of fluent typing (Chenoweth & Hayes)
- **R-bursts**: 3-7 words ending in revision (20% of bursts)
- **Pause hierarchy**: word < clause < sentence < paragraph (lognormal)
- **Typo injection**: 2-5% neighbor-key substitutions with reaction pauses
- **Session gaps**: 20-90 min sessions with realistic break patterns
- **Verification**: Ensures final text exactly matches input

### Timing Engine (`timing_engine.py`)
- **QWERTY matrix**: Hand/finger-based delay calculation (Salthouse 1986)
- **Bigram speedup**: 40% faster for common bigrams
- **Gumbel distribution**: Delay sampling (Migdal & Rosenberger 2019)
- **AR(1) process**: Latent speed burstiness (φ=0.7)
- **Dwell time**: N(μ=116ms, σ=20ms) per Dhakal CHI 2018
- **Rollover**: 30% opposite-hand digraph overlap
- **Jitter**: ±2ms anti-quantization

### CDP Emitter (`cdp_emitter.py`)
- Chrome DevTools Protocol integration
- `Input.dispatchKeyEvent` calls (not keyboard.type())
- Shift-key state management
- Google Docs iframe handling
- Dry-run mode for testing

## Installation

```bash
pip install -r requirements.txt
```

## Usage

### Basic (Dry-Run Mode - Default)
```bash
python main.py --text "Hello world. This is a test." --mode dry-run
```

### With Profile Selection
```bash
python main.py --text "@essay.txt" --profile fast --mode dry-run
```

### Save Output to File
```bash
python main.py --text "Sample text" --output output.json
```

### Verbose Mode
```bash
python main.py --text "Test" --verbose --seed 42
```

## CLI Options

| Option | Description | Default |
|--------|-------------|---------|
| `--text`, `-t` | Text to simulate (or `@file.txt`) | Required |
| `--doc-id`, `-d` | Google Docs document ID | None |
| `--profile`, `-p` | `slow`, `average`, `fast` | `average` |
| `--mode`, `-m` | `dry-run`, `immediate`, `realistic` | `dry-run` |
| `--seed`, `-s` | Random seed for reproducibility | None |
| `--output`, `-o` | Output JSON file path | stdout |
| `--verbose`, `-v` | Print debug info | False |

## Output Format

```json
{
  "generated_by": "Aletheia-Research",
  "purpose": "detection_training",
  "synthetic_research_data": true,
  "metadata": {
    "profile": "average",
    "input_length": 28,
    "word_count": 6
  },
  "statistics": {
    "total_time_ms": 4613.87,
    "estimated_wpm": 15.6,
    "lag1_autocorrelation": -0.0169
  },
  "macro_script": [...],
  "micro_timings": [...],
  "target_text": "..."
}
```

## Research Baselines

| Parameter | Value | Source |
|-----------|-------|--------|
| Alternate-hand digraph | 136ms | Salthouse (1986) |
| Same-hand diff-finger | 168ms | Salthouse (1986) |
| Same-finger | 218ms | Salthouse (1986) |
| Dwell time mean | 116ms | Dhakal et al. CHI 2018 |
| Mean typing speed | 52 WPM | Dhakal et al. CHI 2018 |
| P-burst length | 8-13 words | Chenoweth & Hayes |
| R-burst probability | 20% | Leijten & Van Waes |
| Deletion rate | 10-30% | Literature |

## Safety Features

- **Default dry-run**: No browser interaction unless explicitly requested
- **Watermarking**: All output includes `synthetic_research_data: true`
- **Purpose labeling**: Clearly marked for detection training

## Testing

Run individual module tests:
```bash
python timing_engine.py
python macro_scripter.py
python cdp_emitter.py
```

Run full integration test:
```bash
python -c "from main import generate_full_output; print(generate_full_output('Test', seed=42)['statistics'])"
```

## License

MIT License - For academic research use only.
