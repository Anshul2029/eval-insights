"""
run_eval.py -- Entry point.

Loads all traces from dataset/, runs the pipeline on each,
saves results to results/all_results.json, and prints a summary table.

Usage:
    python run_eval.py                     # all traces
    python run_eval.py trace_001 trace_008 # specific traces by ID
"""

import sys
import io
import json
import os
import glob
from datetime import datetime, timezone
from dotenv import load_dotenv

# Force UTF-8 output on Windows (avoids cp1252 crash on em-dashes, arrows, etc.)
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

DATASET_DIR = os.path.join(os.path.dirname(__file__), "dataset")
RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")
RESULTS_FILE = os.path.join(RESULTS_DIR, "all_results.json")


def load_trace(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def score_to_label(score: float) -> str:
    if score >= 0.8:
        return "PASS  "
    if score >= 0.5:
        return "AMBER "
    return "FAIL  "


def score_to_bar(score: float, width: int = 20) -> str:
    filled = round(score * width)
    return "#" * filled + "." * (width - filled)


def print_summary(results: list) -> None:
    sep = "=" * 72
    print("\n" + sep)
    print("  TRAJECTORY EVALUATION SUMMARY")
    print(sep)
    print(f"  {'Trace':<12} {'Score':>6}  {'Bar':<22} {'Status':<7} Root Cause")
    print("-" * 72)
    for r in results:
        tid = r["trace_id"]
        score = r["trajectory_score"]
        label = score_to_label(score)
        bar = score_to_bar(score)
        fa = r.get("failure_attribution", {})
        root = ""
        if fa.get("failure_transition_step"):
            root = (
                f"Step {fa['failure_transition_step']} "
                f"({fa.get('failure_type','?')})"
            )
        print(f"  {tid:<12} {score:>5.3f}  {bar:<22} {label} {root}")
    print(sep)
    passed = sum(1 for r in results if r["trajectory_score"] >= 0.5)
    print(f"\n  {passed}/{len(results)} traces passed (score >= 0.50)\n")


def main() -> None:
    from pipeline import run_pipeline  # import here so dotenv runs first

    os.makedirs(RESULTS_DIR, exist_ok=True)

    args = sys.argv[1:]
    mock_mode = "--mock" in args
    filter_ids = set(a for a in args if not a.startswith("--"))

    trace_paths = sorted(glob.glob(os.path.join(DATASET_DIR, "trace_*.json")))

    if filter_ids:
        trace_paths = [
            p for p in trace_paths
            if any(fid in os.path.basename(p) for fid in filter_ids)
        ]

    if not trace_paths:
        print("No trace files found in dataset/")
        sys.exit(1)

    mode_label = " [MOCK MODE -- no API calls]" if mock_mode else ""
    print(f"\nRunning evaluation on {len(trace_paths)} trace(s)...{mode_label}")
    print(f"  Dataset dir : {DATASET_DIR}")
    print(f"  Results file: {RESULTS_FILE}")

    existing: dict = {}
    if os.path.exists(RESULTS_FILE):
        with open(RESULTS_FILE, encoding="utf-8") as f:
            try:
                old = json.load(f)
                existing = {r["trace_id"]: r for r in old}
            except (json.JSONDecodeError, KeyError):
                pass

    results = []
    for path in trace_paths:
        trace = load_trace(path)
        try:
            result = run_pipeline(trace, verbose=True, mock=mock_mode)
        except Exception as exc:
            print(f"  ERROR on {trace.get('trace_id','?')}: {exc}")
            result = {
                "trace_id": trace.get("trace_id", os.path.basename(path)),
                "dataset_file": trace.get("dataset_file", ""),
                "user_prompt": trace.get("user_prompt", ""),
                "trajectory_score": 0.0,
                "plan_score": 0.0,
                "avg_step_score": 0.0,
                "failure_attribution": {},
                "error": str(exc),
            }
        result["evaluated_at"] = datetime.now(timezone.utc).isoformat()
        existing[result["trace_id"]] = result
        results.append(result)
        with open(RESULTS_FILE, "w", encoding="utf-8") as f:
            json.dump(list(existing.values()), f, indent=2, default=str)

    all_results = list(existing.values())
    print(f"\nResults saved -> {RESULTS_FILE}")
    print_summary(results)


if __name__ == "__main__":
    main()
