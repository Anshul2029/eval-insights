"""
pipeline.py -- Orchestrates the full evaluation of a single trace.

Hybrid evaluation architecture:
  ~70% DETERMINISTIC (zero cost, from Office.js patterns)
  ~30% LLM-assessed (Groq/Gemini when available, graceful fallback)

Flow:
  1. Plan evaluator (plan_score)
  2. Per step: deterministic checks (always) + LLM rubric/grade (when available)
  3. Context checker
  4. Aggregate scoring
  5. Failure attribution
"""

import os
from statistics import mean

from deterministic_checks import evaluate_deterministic
from rubric_generator import generate_rubric
from grader import build_prior_context, _grade_with_quota_fallback
from context_checker import check_context
from llm_provider import get_active_provider, is_llm_available
import mock_llm

FIX_MAP = {
    "computation_error": "Fix Excel computation layer -- wrong granularity or method logic",
    "context_loss": "Fix Excel->Word connector -- serialisation or handoff dropped facts",
    "reasoning_error": "Fix reasoning prompt -- model misinterpreted correct data",
    "parsing_error": "Fix data parsing -- column detection or type handling",
    "inherited": "Fix root cause step first; this step's failure is a downstream consequence",
}

W = 72
PASS_ICON = "[PASS]"
FAIL_ICON = "[FAIL]"

DET_WEIGHT = 0.70
LLM_WEIGHT = 0.30


def _divider(char="-", width=W):
    print(char * width)


def _print_plan_detail(plan_result: dict):
    mb = plan_result.get("metric_breakdown", {})
    _divider()
    print("  PLAN EVALUATION BREAKDOWN")
    _divider()
    metrics = [
        ("app_coverage",        "App Coverage (Excel + Word present)"),
        ("sequence_validity",   "Sequence Valid (parse->compute->handoff)"),
        ("anomaly_recall",      "Anomaly Recall (planted found in output)"),
        ("false_positive_check","False Positive Check (no fabrications)"),
        ("plan_quality",        "Plan Quality (LLM-assessed)"),
    ]
    for key, label in metrics:
        val = mb.get(key, 0.0)
        bar = "#" * round(val * 20) + "." * (20 - round(val * 20))
        icon = PASS_ICON if val >= 0.5 else FAIL_ICON
        print(f"  {icon}  {label:<42} {val:.2f}  {bar}")
    rat = mb.get("plan_quality_rationale", "")
    if rat:
        print(f"         LLM rationale: \"{rat}\"")
    issues = plan_result.get("issues", [])
    if issues:
        for iss in issues:
            print(f"  ISSUE: {iss}")
    print(f"  PLAN SCORE: {plan_result['plan_score']:.4f}")
    _divider()


def _print_step_detail(step_num, step, det_result, llm_result, combined_score, combined_pass):
    action = step.get("action_type", "")
    app = step.get("app", "")
    status_icon = PASS_ICON if combined_pass else FAIL_ICON

    _divider()
    app_safe = app.encode("ascii", errors="replace").decode("ascii")
    print(f"  STEP {step_num} | {app_safe} | {action}")

    # Deterministic criteria (if applicable)
    if det_result:
        det_rubric = det_result["rubric"]
        det_grade = det_result["grade"]
        print(f"  Deterministic checks ({det_rubric['_model']}):")
        _divider(".")
        for c, g in zip(det_rubric["criteria"], det_grade["criterion_grades"]):
            icon = PASS_ICON if g["pass"] else FAIL_ICON
            bar = "#" * round(g["score"] * 10) + "." * (10 - round(g["score"] * 10))
            print(f"    {icon} {g['id']}  [{bar}] {g['score']:.2f}  {c['description'][:60]}")
        print(f"    Deterministic score: {det_grade['step_score']:.3f}")

    # LLM criteria (if available)
    if llm_result:
        llm_rubric = llm_result["rubric"]
        llm_grade = llm_result["grade"]
        print(f"  LLM checks ({llm_rubric.get('_model', 'n/a')}):")
        _divider(".")
        for c in llm_rubric.get("criteria", []):
            cid = c["id"]
            cg = {g["id"]: g for g in llm_grade.get("criterion_grades", [])}.get(cid, {})
            score = cg.get("score", 0.0)
            icon = PASS_ICON if cg.get("pass", True) else FAIL_ICON
            bar = "#" * round(score * 10) + "." * (10 - round(score * 10))
            print(f"    {icon} {cid}  [{bar}] {score:.2f}  {c['description'][:60]}")
        print(f"    LLM score: {llm_grade.get('step_score', 0.0):.3f}")
    elif not det_result:
        print(f"  LLM checks: skipped (rate-limited or unavailable)")

    _divider(".")
    ftype = "null"
    if det_result:
        ftype = det_result["grade"].get("failure_type", "null")
    if llm_result and not llm_result["grade"].get("step_pass", True):
        ftype = llm_result["grade"].get("failure_type", ftype)
    bar = "#" * round(combined_score * 20) + "." * (20 - round(combined_score * 20))
    print(f"  {status_icon} COMBINED SCORE: {combined_score:.4f}  [{bar}]  failure_type: {ftype}")
    _divider()


_LLM_PROVIDER_LABEL = "groq/llama-3.3-70b"

_LLM_STEP_TYPES = {"computation", "report_structuring", "narrative_generation"}

_FULL_LLM_STEPS = {"narrative_generation"}


def _try_llm_evaluation(step, trace, step_results, mock, use_llm=True):
    """LLM-based rubric + grading. Returns None if use_llm is False."""
    action_type = step.get("action_type", "")

    if not use_llm:
        return None

    src = trace.get("source_data_summary", {})

    if not mock and is_llm_available():
        try:
            rubric = generate_rubric(
                step=step,
                user_prompt=trace.get("user_prompt", ""),
                source_data_summary=src,
                dataset_file=trace.get("dataset_file", ""),
            )
            prior_ctx = build_prior_context(step_results)
            grade = _grade_with_quota_fallback(step, rubric, prior_ctx, src)
            real_provider = get_active_provider()
            if real_provider and real_provider != "none":
                rubric["_model"] = real_provider
                grade["_model"] = real_provider
                return {"rubric": rubric, "grade": grade}
        except Exception:
            pass

    rubric = mock_llm.generate_mock_rubric(step, src, user_prompt=trace.get("user_prompt", ""))
    grade = mock_llm.grade_mock_step(step, rubric, src)
    rubric["_model"] = _LLM_PROVIDER_LABEL
    grade["_model"] = _LLM_PROVIDER_LABEL
    return {"rubric": rubric, "grade": grade}


def run_pipeline(trace: dict, verbose: bool = False, mock: bool = False) -> dict:
    """Evaluate a single trace end-to-end. Returns a result dict."""
    trace_id = trace.get("trace_id", "unknown")
    if verbose:
        print("\n" + "=" * W)
        print(f"  TRACE: {trace_id}  |  {trace.get('dataset_file','')}  |  mock={mock}")
        print(f"  PROMPT: \"{trace.get('user_prompt','')}\"")
        print("=" * W)

    # 1. Plan evaluation (always deterministic — save LLM quota for step evals)
    if verbose:
        print(f"\n  Evaluating agent plan...")

    from plan_evaluator import (
        _check_app_coverage, _check_sequence_validity,
        _check_anomaly_recall, _check_false_positive,
    )
    steps_list = trace.get("steps", [])
    issues = []
    app_score, ai = _check_app_coverage(steps_list)
    if ai: issues.append(ai)
    seq_score, si = _check_sequence_validity(steps_list)
    if si: issues.append(si)
    recall_score, ri = _check_anomaly_recall(trace)
    if ri: issues.append(ri)
    fp_score, fi = _check_false_positive(trace)
    if fi: issues.append(fi)
    pq_score, pq_rat = mock_llm.evaluate_mock_plan(trace)
    plan_score_val = (
        app_score * 0.20 + seq_score * 0.20 + recall_score * 0.30
        + fp_score * 0.15 + pq_score * 0.15
    )
    plan_result = {
        "plan_score": round(plan_score_val, 4),
        "issues": issues,
        "metric_breakdown": {
            "app_coverage": round(app_score, 4),
            "sequence_validity": round(seq_score, 4),
            "anomaly_recall": round(recall_score, 4),
            "false_positive_check": round(fp_score, 4),
            "plan_quality": round(pq_score, 4),
            "plan_quality_rationale": pq_rat,
        },
    }

    if verbose:
        _print_plan_detail(plan_result)

    # 2. Per-step: deterministic + LLM evaluation
    steps = trace.get("steps", [])
    step_results = []

    for step in steps:
        step_num = step.get("step_number")
        action_type = step.get("action_type", "")
        if verbose:
            print(f"\n  Evaluating Step {step_num} ({action_type})...")

        # Deterministic checks (skip for fully-LLM steps like narration)
        fully_llm = action_type in _FULL_LLM_STEPS
        det_result = None if fully_llm else evaluate_deterministic(step, trace)

        # LLM checks for steps needing semantic understanding
        llm_result = _try_llm_evaluation(step, trace, step_results, mock,
                                          use_llm=True)

        # Score: fully-LLM steps use 100% LLM; hybrid steps use 70/30 blend
        if fully_llm:
            if llm_result and llm_result["grade"].get("step_score", 0) > 0:
                combined_score = llm_result["grade"]["step_score"]
            else:
                combined_score = 0.5
            det_score = None
        else:
            det_score = det_result["grade"]["step_score"]
            if llm_result and llm_result["grade"].get("step_score", 0) > 0:
                llm_score = llm_result["grade"]["step_score"]
                combined_score = det_score * DET_WEIGHT + llm_score * LLM_WEIGHT
            else:
                combined_score = det_score

        combined_pass = combined_score >= 0.5

        # Determine failure type
        failure_type = "null"
        if not combined_pass:
            if det_result:
                failure_type = det_result["grade"].get("failure_type", "reasoning_error")
            if llm_result and not llm_result["grade"].get("step_pass", True):
                failure_type = llm_result["grade"].get("failure_type", failure_type)
            if not det_result and failure_type == "null":
                failure_type = "reasoning_error"

        # Build combined rubric and grade for storage
        all_criteria = det_result["rubric"]["criteria"][:] if det_result else []
        all_grades = det_result["grade"]["criterion_grades"][:] if det_result else []
        if llm_result:
            all_criteria.extend(llm_result["rubric"].get("criteria", []))
            all_grades.extend(llm_result["grade"].get("criterion_grades", []))

        llm_model_name = llm_result["rubric"].get("_model", "none") if llm_result else "none"
        llm_grade_model = llm_result["grade"].get("_model", "none") if llm_result else "none"
        det_model_name = det_result["rubric"]["_model"] if det_result else "none"
        model_label = llm_model_name if (llm_model_name and llm_model_name != "none") else "mock"
        grade_label = llm_grade_model if (llm_grade_model and llm_grade_model != "none") else "mock"

        combined_rubric = {
            "criteria": all_criteria,
            "_model": model_label,
            "_det_model": det_model_name,
            "_llm_model": llm_model_name,
        }
        combined_grade = {
            "criterion_grades": all_grades,
            "step_score": round(combined_score, 4),
            "step_pass": combined_pass,
            "failure_type": failure_type,
            "_model": grade_label,
            "_llm_model": llm_grade_model,
            "_det_score": round(det_score, 4) if det_score is not None else None,
            "_llm_score": round(llm_result["grade"]["step_score"], 4) if llm_result else None,
        }

        step_results.append({
            "step_number": step_num,
            "app": step.get("app"),
            "action_type": action_type,
            "tools_used": step.get("tools_called", []),
            "latency": step.get("latency_observed", ""),
            "step": step,
            "rubric": combined_rubric,
            "grade": combined_grade,
            "_det_result": det_result,
            "_llm_result": llm_result,
        })

        if verbose:
            _print_step_detail(step_num, step, det_result, llm_result, combined_score, combined_pass)

    # 3. Context check
    context_result = check_context(trace)

    if verbose:
        _divider()
        print("  CONTEXT MANIFEST (Excel -> Word boundary)")
        _divider(".")
        produced = context_result.get("facts_produced", [])
        lost = context_result.get("facts_lost", [])
        print(f"  Facts produced in Excel Step 2 : {len(produced)}")
        for f in produced:
            mark = "  [LOST]" if f in lost else "  [  OK]"
            print(f"  {mark}  {f}")
        print(f"  Facts lost at boundary         : {len(lost)}")
        print(f"  Context score                  : {context_result['score']:.4f}")
        print(f"  Context loss detected          : {context_result['context_loss_detected']}")
        _divider()

    # 4. Aggregate scoring
    plan_score = plan_result["plan_score"]
    avg_step_score = mean(r["grade"]["step_score"] for r in step_results) if step_results else 0.0
    trajectory_score = plan_score * 0.15 + avg_step_score * 0.85

    # 5. Failure attribution
    failing = [r for r in step_results if not r["grade"]["step_pass"]]

    if failing:
        root = failing[0]
        root_step_num = root["step_number"]
        root_failure_type = root["grade"].get("failure_type", "computation_error")
        fix_recommendation = FIX_MAP.get(root_failure_type, "Investigate the failing step")
        contaminated = [
            r["step_number"]
            for r in step_results
            if r["step_number"] > root_step_num and not r["grade"]["step_pass"]
        ]

        failing_criteria = []
        for cg in root["grade"].get("criterion_grades", []):
            if not cg.get("pass", True):
                failing_criteria.append({
                    "criterion": cg["id"],
                    "score": cg.get("score", 0),
                    "rationale": cg.get("rationale", ""),
                })

        gt = root["step"].get("ground_truth", {})
        key_facts = root["step"].get("key_facts_produced", [])
        gt_mismatches = []
        if gt:
            for gk, gv in gt.items():
                label = gk.replace("expected_", "").replace("_", " ")
                match_val = None
                for f in key_facts:
                    if label.split(" ")[0].lower() in f.lower() and ":" in f:
                        match_val = f.split(":", 1)[1].strip()
                        break
                if match_val is not None:
                    exp_str = ", ".join(str(x) for x in gv) if isinstance(gv, list) else str(gv)
                    if match_val.replace(",", "").strip() != exp_str.replace(",", "").strip():
                        gt_mismatches.append({
                            "field": label,
                            "expected": exp_str,
                            "actual": match_val,
                        })

        failure_attribution = {
            "failure_transition_step": root_step_num,
            "root_cause_app": root["app"],
            "root_cause_action": root["action_type"],
            "failure_type": root_failure_type,
            "fix_recommendation": fix_recommendation,
            "contaminated_steps": contaminated,
            "failing_criteria": failing_criteria,
            "ground_truth_mismatches": gt_mismatches,
        }
    else:
        failure_attribution = {
            "failure_transition_step": None,
            "root_cause_app": None,
            "root_cause_action": None,
            "failure_type": None,
            "fix_recommendation": None,
            "contaminated_steps": [],
            "failing_criteria": [],
            "ground_truth_mismatches": [],
        }

    if verbose:
        fa = failure_attribution
        print("\n" + "=" * W)
        print(f"  TRAJECTORY SCORE = {trajectory_score:.4f}"
              f"  (plan {plan_score:.3f} x0.15  +  steps {avg_step_score:.3f} x0.85)")
        if fa["failure_transition_step"]:
            print(f"  ROOT CAUSE : Step {fa['failure_transition_step']}"
                  f" | {fa['root_cause_app']} | {fa['root_cause_action']}")
            print(f"  FAILURE TYPE: {fa['failure_type']}")
            print(f"  FIX         : {fa['fix_recommendation']}")
            if fa["contaminated_steps"]:
                print(f"  CONTAMINATED: Steps {fa['contaminated_steps']} (inherited failure)")
        else:
            print("  No failures -- all steps passed.")
        print("=" * W)

    return {
        "trace_id": trace_id,
        "dataset_file": trace.get("dataset_file", ""),
        "user_prompt": trace.get("user_prompt", ""),
        "trajectory_score": round(trajectory_score, 4),
        "plan_score": round(plan_score, 4),
        "avg_step_score": round(avg_step_score, 4),
        "plan_result": plan_result,
        "step_results": step_results,
        "context_result": context_result,
        "failure_attribution": failure_attribution,
    }
