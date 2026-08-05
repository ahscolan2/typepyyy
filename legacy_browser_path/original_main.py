"""
Project Aletheia - Main CLI
Wire everything together with command-line interface.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Optional

from timing_engine import TimingEngine, KeystrokeEvent
from macro_scripter import MacroScripter, ScriptEvent
from cdp_emitter import CDPEmitter

# Watermark for all output
WATERMARK = {
    "generated_by": "Aletheia-Research",
    "purpose": "detection_training",
    "synthetic_research_data": True
}

def load_text(text_arg: str) -> str:
    """Load text from argument or file."""
    if text_arg.startswith('@'):
        filepath = Path(text_arg[1:])
        if filepath.exists():
            return filepath.read_text()
        else:
            raise FileNotFoundError(f"Text file not found: {filepath}")
    return text_arg

def generate_full_output(text: str, profile: str = "average", 
                         mode: str = "dry-run", doc_id: str = None,
                         seed: int = None, verbose: bool = False) -> dict:
    """
    Generate complete synthetic data output.
    
    Args:
        text: Input text to simulate
        profile: 'slow', 'average', or 'fast'
        mode: 'dry-run', 'immediate', or 'realistic'
        doc_id: Google Docs document ID
        seed: Random seed for reproducibility
        verbose: Print debug info
    
    Returns:
        Complete JSON output with macro script and micro timings
    """
    # Initialize components
    macro_scripter = MacroScripter(seed=seed)
    timing_engine = TimingEngine(profile=profile, seed=seed)
    
    # Generate macro script
    macro_script, typo_corrections = macro_scripter.generate_script(text)
    
    # Verify script produces correct text
    if not macro_scripter.verify_script(text, macro_script):
        raise ValueError("Macro script verification failed!")
    
    # Convert macro script to list of dicts for JSON serialization
    macro_events = []
    cumulative_time = 0.0
    
    for event in macro_script:
        event_dict = {
            "op": event.op,
            "data": event.data,
            "timestamp_ms": cumulative_time
        }
        macro_events.append(event_dict)
        
        # Update cumulative time
        if event.op == "PAUSE":
            cumulative_time += event.data.get("ms", 0)
        elif event.op == "TYPE":
            cumulative_time += 150  # Estimate, will be refined
        elif event.op == "DELETE":
            cumulative_time += event.data * 150
        # SESSION_GAP doesn't advance time in immediate mode
    
    # Generate micro timings for TYPE events only
    type_chars = []
    for event in macro_script:
        if event.op == "TYPE":
            type_chars.append(event.data)
    
    target_text = "".join(type_chars)
    keystroke_events = timing_engine.generate_keystrokes(target_text, typo_corrections)
    
    # Build micro timing details
    micro_timings = []
    for ke in keystroke_events:
        micro_timings.append({
            "char": ke.char,
            "keydown_ts": ke.keydown_ts,
            "keyup_ts": ke.keyup_ts,
            "dwell_time": ke.dwell_time,
            "flight_time": ke.flight_time,
            "is_typo_correction": ke.is_typo_correction
        })
    
    # Calculate statistics
    total_time_ms = keystroke_events[-1].keyup_ts if keystroke_events else 0
    word_count = len(text.split())
    wpm = (word_count / 5) / (total_time_ms / 60000) if total_time_ms > 0 else 0
    
    # Calculate autocorrelation of flight times
    flight_times = [ke.flight_time for ke in keystroke_events]
    if len(flight_times) > 2:
        mean_ft = sum(flight_times) / len(flight_times)
        variance = sum((x - mean_ft) ** 2 for x in flight_times) / len(flight_times)
        if variance > 0:
            lag1_sum = sum(
                (flight_times[i] - mean_ft) * (flight_times[i+1] - mean_ft)
                for i in range(len(flight_times) - 1)
            )
            autocorrelation = lag1_sum / ((len(flight_times) - 1) * variance)
        else:
            autocorrelation = 0.0
    else:
        autocorrelation = 0.0
    
    # Build final output
    output = {
        **WATERMARK,
        "metadata": {
            "doc_id": doc_id,
            "profile": profile,
            "mode": mode,
            "seed": seed,
            "input_length": len(text),
            "word_count": word_count
        },
        "statistics": {
            "total_time_ms": total_time_ms,
            "estimated_wpm": wpm,
            "total_operations": len(macro_events),
            "type_operations": sum(1 for e in macro_events if e["op"] == "TYPE"),
            "pause_operations": sum(1 for e in macro_events if e["op"] == "PAUSE"),
            "delete_operations": sum(1 for e in macro_events if e["op"] == "DELETE"),
            "session_gaps": sum(1 for e in macro_events if e["op"] == "SESSION_GAP"),
            "typo_corrections": len(typo_corrections),
            "lag1_autocorrelation": round(autocorrelation, 4)
        },
        "macro_script": macro_events,
        "micro_timings": micro_timings,
        "target_text": text
    }
    
    return output

async def execute_mode(output: dict, mode: str, doc_id: str = None):
    """Execute based on mode."""
    if mode == "dry-run":
        print("[DRY-RUN MODE] Skipping browser execution")
        return
    
    emitter = CDPEmitter(dry_run=(mode != "immediate"))
    
    try:
        await emitter.start()
        
        if doc_id and mode == "immediate":
            await emitter.navigate_to_docs(doc_id)
        
        # Execute macro script
        result = await emitter.execute_script(output["macro_script"])
        
        if mode == "immediate":
            print(f"[IMMEDIATE MODE] Executed {result['total_events']} events")
            print(f"Total simulated time: {result['total_time_ms']:.1f} ms")
        
    finally:
        await emitter.stop()

def main():
    parser = argparse.ArgumentParser(
        description="Project Aletheia - Synthetic Writing Data Generator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py --text "Hello world" --mode dry-run
  python main.py --text "@essay.txt" --profile fast --mode dry-run
  python main.py --text "Test" --doc-id "ABC123" --mode immediate
        """
    )
    
    parser.add_argument(
        "--text", "-t",
        required=True,
        help="Text to simulate (or @file.txt to load from file)"
    )
    
    parser.add_argument(
        "--doc-id", "-d",
        default=None,
        help="Google Docs document ID (optional)"
    )
    
    parser.add_argument(
        "--profile", "-p",
        choices=["slow", "average", "fast"],
        default="average",
        help="Typing profile (default: average)"
    )
    
    parser.add_argument(
        "--mode", "-m",
        choices=["dry-run", "immediate", "realistic"],
        default="dry-run",
        help="Execution mode (default: dry-run)"
    )
    
    parser.add_argument(
        "--seed", "-s",
        type=int,
        default=None,
        help="Random seed for reproducibility"
    )
    
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Output JSON file path (default: stdout)"
    )
    
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Print verbose debug information"
    )
    
    args = parser.parse_args()
    
    try:
        # Load text
        text = load_text(args.text)
        
        if args.verbose:
            print(f"Loaded text: {len(text)} chars, {len(text.split())} words")
            print(f"Profile: {args.profile}, Mode: {args.mode}, Seed: {args.seed}")
        
        # Generate output
        output = generate_full_output(
            text=text,
            profile=args.profile,
            mode=args.mode,
            doc_id=args.doc_id,
            seed=args.seed,
            verbose=args.verbose
        )
        
        # Save or print output
        if args.output:
            with open(args.output, 'w') as f:
                json.dump(output, f, indent=2)
            print(f"Output written to: {args.output}")
        else:
            print(json.dumps(output, indent=2))
        
        # Execute if not dry-run
        if args.mode != "dry-run":
            asyncio.run(execute_mode(output, args.mode, args.doc_id))
        
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
