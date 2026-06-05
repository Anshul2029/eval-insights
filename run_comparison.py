"""
run_comparison.py — Run same task through Groq and Qwen, evaluate with Groq grader, compare.

What you see:
  1. Trace side-by-side  — what each LLM produced at each step
  2. Token usage table   — input / output / thinking / total per step per LLM
  3. Evaluation metrics  — trajectory score, plan score, step scores per LLM

Output:
  results/comparison_<trace_id>.json

Usage:
  python run_comparison.py trace_002
  python run_comparison.py trace_002 trace_004
"""

import sys
import os
import io
import json
import glob
from datetime import datetime, timezone
from dotenv import load_dotenv

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

DATASET_DIR = os.path.join(os.path.dirname(__file__), "dataset")
RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")
LLMS = ["groq", "qwen"]
W = 80
PASS_ICON = "[PASS]"
FAIL_ICON = "[FAIL]"


# ── helpers ───────────────────────────────────────────────────────────────────

def _div(char="-", width=W):
    print(char * width)


def load_trace(trace_id: str) -> dict:
    path = os.path.join(DATASET_DIR, f"{trace_id}.json")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Trace not found: {path}")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ── display ───────────────────────────────────────────────────────────────────

def print_trace_comparison(results: dict):
    """Print each LLM's step outputs side by side (sequentially per step)."""
    _div("=", W)
    print("  TRACE COMPARISON — STEP OUTPUTS")
    _div("=", W)

    step_labels = {
        1: "data_parsing",
        2: "computation",
        3: "context_handoff",
        4: "report_structuring",
        5: "narrative_generation",
    }

    for sn in range(1, 6):
        print(f"\n  STEP {sn}: {step_labels[sn].upper()}")
        _div(".", W)
        for llm, r in results.items():
            trace = r["agent_result"]["trace"]
            model = trace.get("llm_model", llm)
            step = next((s for s in trace["steps"] if s["step_number"] == sn), {})
            output = step.get("output", "(empty)")[:220]
            print(f"\n  [{llm.upper()} / {model}]")
            print(f"  {output}")
            kf = step.get("key_facts_produced", [])[:5]
            for fact in kf:
                print(f"    - {fact}")
        _div(".", W)

    _div("=", W)


def print_token_table(results: dict):
    """Print per-step token usage for each LLM."""
    _div("=", W)
    print("  TOKEN USAGE PER STEP")
    print(f"  {'':30}  {'INPUT':>7}  {'OUTPUT':>7}  {'THINKING':>9}  {'TOTAL':>7}")
    _div("=", W)

    step_labels = {
        0: "planning",
        1: "data_parsing",
        2: "computation",
        3: "context_handoff",
        4: "report_structuring",
        5: "narrative_generation",
    }

    for llm, r in results.items():
        model = r["agent_result"]["trace"].get("llm_model", llm)
        print(f"\n  [{llm.upper()} / {model}]")
        _div("-", W)
        per_step = r["agent_result"]["token_usage"]["per_step"]
        for s in per_step:
            sn = s["step_number"]
            label = f"Step {sn}: {step_labels.get(sn, s['action_type'])}"
            print(
                f"  {label:<30}  {s['input_tokens']:>7}  {s['output_tokens']:>7}"
                f"  {s['thinking_tokens']:>9}  {s['total_tokens']:>7}"
            )
        _div("-", W)
        t = r["agent_result"]["token_usage"]["total"]
        print(
            f"  {'TOTAL':<30}  {t['input_tokens']:>7}  {t['output_tokens']:>7}"
            f"  {t['thinking_tokens']:>9}  {t['total_tokens']:>7}"
        )

    # side-by-side summary
    _div("=", W)
    print("\n  SIDE-BY-SIDE TOKEN SUMMARY")
    _div("-", W)
    llm_names = list(results.keys())
    header = f"  {'Step':<30}"
    for llm in llm_names:
        header += f"  {llm.upper():>12}(total)"
    print(header)
    _div("-", W)

    all_step_nums = sorted({
        s["step_number"]
        for r in results.values()
        for s in r["agent_result"]["token_usage"]["per_step"]
    })
    for sn in all_step_nums:
        label = f"Step {sn}: {step_labels.get(sn, '?')}"
        row = f"  {label:<30}"
        vals = []
        for llm in llm_names:
            per_step = results[llm]["agent_result"]["token_usage"]["per_step"]
            s = next((x for x in per_step if x["step_number"] == sn), None)
            val = s["total_tokens"] if s else 0
            vals.append((llm, val))
            row += f"  {val:>19}"
        winner = max(vals, key=lambda x: x[1])[0]
        row += f"  <- more: {winner}"
        print(row)

    _div("-", W)
    total_row = f"  {'TOTAL':<30}"
    totals = []
    for llm in llm_names:
        t = results[llm]["agent_result"]["token_usage"]["total"]["total_tokens"]
        totals.append((llm, t))
        total_row += f"  {t:>19}"
    winner = max(totals, key=lambda x: x[1])[0]
    total_row += f"  <- more: {winner}"
    print(total_row)
    _div("=", W)


def print_eval_table(results: dict):
    """Print evaluation metric comparison."""
    _div("=", W)
    print("  EVALUATION METRICS COMPARISON")
    _div("=", W)

    llm_names = list(results.keys())
    header = f"  {'Metric':<38}"
    for llm in llm_names:
        header += f"  {llm.upper():>10}"
    print(header)
    _div("-", W)

    top_metrics = [
        ("trajectory_score", "Trajectory Score"),
        ("plan_score",        "Plan Score"),
        ("avg_step_score",    "Avg Step Score"),
    ]
    for key, label in top_metrics:
        row = f"  {label:<38}"
        vals = []
        for llm in llm_names:
            v = results[llm]["eval_result"].get(key, 0.0)
            vals.append(v)
            row += f"  {v:>10.4f}"
        if len(vals) == 2 and vals[0] != vals[1]:
            winner = llm_names[0] if vals[0] > vals[1] else llm_names[1]
            row += f"  <- {winner}"
        print(row)

    _div("-", W)
    print(f"  {'Per-Step Scores':<38}")

    all_step_nums = sorted({
        sr["step_number"]
        for r in results.values()
        for sr in r["eval_result"].get("step_results", [])
    })
    step_labels = {
        1: "data_parsing", 2: "computation", 3: "context_handoff",
        4: "report_structuring", 5: "narrative_generation",
    }
    for sn in all_step_nums:
        label = f"  Step {sn} ({step_labels.get(sn, '?')})"
        row = f"  {label:<38}"
        vals = []
        for llm in llm_names:
            sr = next(
                (r for r in results[llm]["eval_result"].get("step_results", []) if r["step_number"] == sn),
                None,
            )
            if sr:
                v = sr["grade"]["step_score"]
                icon = PASS_ICON if sr["grade"]["step_pass"] else FAIL_ICON
                vals.append(v)
                row += f"  {icon} {v:>5.3f} "
            else:
                vals.append(0.0)
                row += f"  {'--':>10}"
        if len(vals) == 2 and vals[0] != vals[1]:
            winner = llm_names[0] if vals[0] > vals[1] else llm_names[1]
            row += f"  <- {winner}"
        print(row)

    _div("=", W)


# ── core run ──────────────────────────────────────────────────────────────────

def run_comparison(trace_id: str) -> dict:
    from agent_runner import AgentRunner
    from pipeline import run_pipeline

    _div("=", W)
    print(f"  COMPARISON: {trace_id}")
    _div("=", W)

    source = load_trace(trace_id)
    user_prompt = source["user_prompt"]
    src_summary = source["source_data_summary"]
    dataset_file = source["dataset_file"]

    print(f"  Prompt  : {user_prompt}")
    print(f"  Dataset : {dataset_file}")
    print(f"  LLMs    : {', '.join(LLMS)}")

    results = {}
    for llm_name in LLMS:
        runner = AgentRunner(llm_name)
        agent_result = runner.run(user_prompt, src_summary, dataset_file, trace_id)

        print(f"\n  Evaluating [{llm_name.upper()}] trace with Groq grader...")
        eval_result = run_pipeline(agent_result["trace"], verbose=False, mock=False)

        results[llm_name] = {"agent_result": agent_result, "eval_result": eval_result}

    return results


def save_report(trace_id: str, results: dict) -> str:
    os.makedirs(RESULTS_DIR, exist_ok=True)
    report = {
        "comparison_id": trace_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "llms_compared": LLMS,
    }
    for llm, r in results.items():
        report[llm] = {
            "llm_model": r["agent_result"]["trace"].get("llm_model", llm),
            "token_usage": r["agent_result"]["token_usage"],
            "trajectory_score": r["eval_result"]["trajectory_score"],
            "plan_score": r["eval_result"]["plan_score"],
            "avg_step_score": r["eval_result"]["avg_step_score"],
            "failure_attribution": r["eval_result"]["failure_attribution"],
            "step_results": r["eval_result"].get("step_results", []),
            "plan_result": r["eval_result"].get("plan_result", {}),
            "trace": r["agent_result"]["trace"],
        }
    path = os.path.join(RESULTS_DIR, f"comparison_{trace_id}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\n  Report saved -> {path}")
    return path


# ── entry point ───────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]
    if not args:
        print("Usage: python run_comparison.py trace_002 [trace_004 ...]")
        sys.exit(1)

    trace_ids = [a for a in args if not a.startswith("--")]

    for trace_id in trace_ids:
        try:
            results = run_comparison(trace_id)
            print_trace_comparison(results)
            print_token_table(results)
            print_eval_table(results)
            save_report(trace_id, results)
        except Exception as exc:
            print(f"  ERROR on {trace_id}: {exc}")
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    main()
