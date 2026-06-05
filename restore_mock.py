"""
restore_mock.py — Re-evaluate the original 14 traces in mock mode and
overwrite all_results.json with a clean, consistent baseline.

Original 14 traces:
  trace_001..trace_010, trace_fail_001, trace_fail_002,
  fail_context_loss, fail_parsing
"""

import io
import json
import os
import sys
from datetime import datetime, timezone
from dotenv import load_dotenv

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

DATASET_DIR = os.path.join(os.path.dirname(__file__), "dataset")
RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")
RESULTS_FILE = os.path.join(RESULTS_DIR, "all_results.json")

ORIGINAL_14 = [
    "trace_001", "trace_002", "trace_003", "trace_004", "trace_005",
    "trace_006", "trace_007", "trace_008", "trace_009", "trace_010",
    "trace_fail_001", "trace_fail_002",
    "fail_context_loss", "fail_parsing",
]


def main():
    from pipeline import run_pipeline

    os.makedirs(RESULTS_DIR, exist_ok=True)

    # Resolve file paths for original 14 trace IDs
    trace_paths = []
    for tid in ORIGINAL_14:
        path = os.path.join(DATASET_DIR, f"{tid}.json")
        if os.path.exists(path):
            trace_paths.append(path)
        else:
            print(f"  WARNING: {path} not found, skipping")

    print(f"\nRestoring {len(trace_paths)} traces in MOCK mode...")
    print(f"  Results file: {RESULTS_FILE}")

    results = {}
    for path in trace_paths:
        with open(path, encoding="utf-8") as f:
            trace = json.load(f)
        tid = trace.get("trace_id", os.path.basename(path))
        print(f"  Processing {tid}...")
        try:
            result = run_pipeline(trace, verbose=False, mock=True)
        except Exception as exc:
            print(f"    ERROR: {exc}")
            result = {
                "trace_id": tid,
                "dataset_file": trace.get("dataset_file", ""),
                "user_prompt": trace.get("user_prompt", ""),
                "trajectory_score": 0.0,
                "plan_score": 0.0,
                "avg_step_score": 0.0,
                "failure_attribution": {},
                "error": str(exc),
            }
        result["evaluated_at"] = datetime.now(timezone.utc).isoformat()
        results[tid] = result
        with open(RESULTS_FILE, "w", encoding="utf-8") as f:
            json.dump(list(results.values()), f, indent=2, default=str)
        print(f"    score={result.get('trajectory_score', 0):.3f}")

    print(f"\nDone. {len(results)} traces written to {RESULTS_FILE}")
    for tid in ORIGINAL_14:
        r = results.get(tid)
        if r:
            print(f"  {tid:<22} {r.get('trajectory_score', 0):.3f}")


if __name__ == "__main__":
    main()
