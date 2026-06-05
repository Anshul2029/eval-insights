"""
run_comparison_real.py — Real execution comparison (like run_comparison.py but with actual workflows).

Usage:
  python run_comparison_real.py trace_002
  python run_comparison_real.py trace_002 trace_004
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


def _div(char="-", width=W):
    print(char * width)


def load_trace(trace_id: str) -> dict:
    path = os.path.join(DATASET_DIR, f"{trace_id}.json")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Trace not found: {path}")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def print_token_rationale(results: dict):
    """Print token rationale for each LLM."""
    _div("=", W)
    print("  TOKEN CONSUMPTION RATIONALE — WHY TOKENS WERE USED")
    _div("=", W)
    
    for llm, r in results.items():
        print(f"\n  [{llm.upper()}]")
        _div("-", W)
        
        rationale = r.get("token_rationale", {})
        per_step = r["agent_result"]["token_usage"]["per_step"]
        
        for step_data in per_step:
            step_num = step_data["step_number"]
            action = step_data["action_type"]
            total = step_data["total_tokens"]
            
            step_rat = rationale.get(step_num, {})
            reason = step_rat.get("reason", "N/A")
            
            print(f"  Step {step_num} ({action}): {total} tokens")
            print(f"    Why: {reason}")
            
            if "data_points" in step_rat:
                print(f"    Data: {step_rat['data_points']}")
            if "computation" in step_rat:
                print(f"    Computation: {step_rat['computation']}")
            if "facts_handled" in step_rat:
                print(f"    Facts: {step_rat['facts_handled']}")
        
        _div("-", W)


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
        exec_mode = r["agent_result"]["trace"].get("execution_mode", "SIMULATED")
        print(f"\n  [{llm.upper()} / {model}] — Mode: {exec_mode}")
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


def run_comparison(trace_id: str) -> dict:
    from agent_runner_real import AgentRunnerReal
    from pipeline import run_pipeline
    
    _div("=", W)
    print(f"  REAL EXECUTION COMPARISON: {trace_id}")
    _div("=", W)
    
    source = load_trace(trace_id)
    user_prompt = source["user_prompt"]
    src_summary = source["source_data_summary"]
    dataset_file = source["dataset_file"]
    
    print(f"  Prompt  : {user_prompt}")
    print(f"  Dataset : {dataset_file}")
    print(f"  LLMs    : {', '.join(LLMS)}")
    print(f"  Mode    : REAL EXECUTION (actual data processing + Word generation)")
    
    results = {}
    for llm_name in LLMS:
        runner = AgentRunnerReal(llm_name)
        agent_result = runner.run(user_prompt, src_summary, dataset_file, trace_id)
        
        print(f"\n  Evaluating [{llm_name.upper()}] trace with Groq grader...")
        eval_result = run_pipeline(agent_result["trace"], verbose=False, mock=False)
        
        results[llm_name] = {
            "agent_result": agent_result,
            "eval_result": eval_result,
            "token_rationale": agent_result.get("token_rationale", {}),
        }
    
    return results


def save_report(trace_id: str, results: dict) -> str:
    os.makedirs(RESULTS_DIR, exist_ok=True)
    report = {
        "comparison_id": f"{trace_id}_real",
        "mode": "REAL_EXECUTION",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "llms_compared": LLMS,
    }
    
    for llm, r in results.items():
        report[llm] = {
            "llm_model": r["agent_result"]["trace"].get("llm_model", llm),
            "execution_mode": r["agent_result"]["trace"].get("execution_mode", "REAL"),
            "word_doc_path": r["agent_result"]["trace"].get("word_doc_path"),
            "token_usage": r["agent_result"]["token_usage"],
            "token_rationale": r.get("token_rationale", {}),
            "trajectory_score": r["eval_result"]["trajectory_score"],
            "plan_score": r["eval_result"]["plan_score"],
            "avg_step_score": r["eval_result"]["avg_step_score"],
            "failure_attribution": r["eval_result"]["failure_attribution"],
            "step_results": r["eval_result"].get("step_results", []),
            "trace": r["agent_result"]["trace"],
        }
    
    path = os.path.join(RESULTS_DIR, f"comparison_{trace_id}_real.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\n  Report saved -> {path}")
    return path


def main():
    args = sys.argv[1:]
    if not args:
        print("Usage: python run_comparison_real.py trace_002 [trace_004 ...]")
        sys.exit(1)
    
    trace_ids = [a for a in args if not a.startswith("--")]
    
    for trace_id in trace_ids:
        try:
            results = run_comparison(trace_id)
            print_token_rationale(results)
            print_token_table(results)
            save_report(trace_id, results)
        except Exception as exc:
            print(f"  ERROR on {trace_id}: {exc}")
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    main()
