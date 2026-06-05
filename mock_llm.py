"""
mock_llm.py -- Deterministic rubric generation and grading for offline testing.

No API calls. Uses trace metadata and key_facts_produced to produce realistic
rubrics and grades without requiring Anthropic or OpenAI credits.

Grading heuristics:
  - If key_facts includes "planted_anomaly_caught: FALSE" -> fail anomaly criterion
  - If key_facts includes "false_positive_fabricated: FALSE" -> pass
  - Missing-value / error keys reduce score
  - Otherwise defaults to 0.9 (good step)
"""

from __future__ import annotations

from intent_signals import extract_intents

_RUBRIC_TEMPLATES = {
    "data_parsing": [
        ("C1", "Agent must load the correct number of rows and validate all expected columns are present"),
        ("C2", "Agent must detect and report any missing values or data quality issues (nulls, non-positive values, duplicates)"),
        ("C3", "Agent must apply correct month ordering or time-series preparation for downstream analysis"),
    ],
    "computation": [
        ("C1", "Goal Alignment: computation method matches the user's analytical goal (e.g. anomaly detection, KPI extraction)"),
        ("C2", "Anomaly Surfacing: agent detects planted anomaly at correct granularity, or correctly reports clean data"),
    ],
    "context_handoff": [
        ("C1", "Agent must transfer all key computed facts (KPIs, anomaly findings) into the Word script context without loss"),
        ("C2", "Agent must resolve any errors (e.g. KeyError, NameError) before handoff, not pass broken state"),
        ("C3", "Agent must include chart generation and pass chart paths to the Word builder"),
    ],
    "report_structuring": [
        ("C1", "Goal Alignment: report structure addresses the user's stated goal with appropriate sections"),
        ("C2", "Synthesis Gap: all computed findings are represented in report sections — nothing dropped"),
    ],
    "narrative_generation": [
        ("C1", "Anomaly Surfacing: narrative explicitly names planted anomaly with specific numbers, not generic observations"),
        ("C2", "Goal Alignment: narrative addresses the user's stated analytical goal and key questions"),
        ("C3", "Synthesis Gap: executive summary accurately reflects dataset state — no false clean verdicts"),
        ("C4", "Goal Drift Detection: recommendations are targeted to detected issues, not boilerplate advice"),
    ],
}

_DEFAULT_RUBRIC = [
    ("C1", "Agent must complete the step without unhandled errors"),
    ("C2", "Agent must produce output consistent with the inputs provided"),
]

_INTENT_OVERRIDES = {
    ("computation", "C1", "anomaly"): "Goal Alignment: anomaly detection method matches the user's request for identifying data anomalies",
    ("computation", "C1", "trends"):  "Goal Alignment: trend analysis method matches the user's request for analyzing data patterns",
    ("computation", "C1", "issues"):  "Goal Alignment: data quality analysis method matches the user's request for finding issues",
    ("computation", "C2", "anomaly"): "Anomaly Surfacing: agent detects planted anomaly at correct per-Region x Product granularity",
    ("report_structuring", "C1", "anomaly"):    "Goal Alignment: report structure includes dedicated anomaly detail section as requested",
    ("report_structuring", "C1", "leadership"): "Goal Alignment: report structure addresses leadership's strategic needs with appropriate sections",
    ("report_structuring", "C1", "trends"):     "Goal Alignment: report structure includes trend analysis sections as requested",
    ("report_structuring", "C2", "anomaly"):    "Synthesis Gap: all detected anomalies are represented in report sections — nothing dropped",
    ("report_structuring", "C2", "trends"):     "Synthesis Gap: all identified trends are represented in report sections — nothing dropped",
    ("narrative_generation", "C1", "anomaly"):   "Anomaly Surfacing: narrative explicitly names detected anomalies with specific numbers and context",
    ("narrative_generation", "C1", "trends"):    "Trend Surfacing: narrative explicitly names significant trends with specific numbers and time periods",
    ("narrative_generation", "C2", "anomaly"):   "Goal Alignment: narrative addresses the user's anomaly investigation request with actionable findings",
    ("narrative_generation", "C2", "leadership"): "Goal Alignment: narrative addresses leadership's strategic questions with executive-level language",
    ("narrative_generation", "C2", "trends"):    "Goal Alignment: narrative addresses the user's trend analysis request with specific patterns identified",
    ("narrative_generation", "C4", "anomaly"):   "Goal Drift Detection: recommendations specifically target detected anomalies, not generic business advice",
    ("narrative_generation", "C4", "trends"):    "Goal Drift Detection: recommendations address identified trends and patterns, not generic boilerplate",
    ("narrative_generation", "C4", "leadership"): "Goal Drift Detection: recommendations are strategic and actionable for leadership, not operational boilerplate",
}


def _has_fact(key_facts: list[str], pattern: str) -> bool:
    p = pattern.lower()
    return any(p in f.lower() for f in key_facts)


def generate_mock_rubric(step: dict, source_data_summary: dict, user_prompt: str = "") -> dict:
    action_type = step.get("action_type", "")
    templates = _RUBRIC_TEMPLATES.get(action_type, _DEFAULT_RUBRIC)
    intents = extract_intents(user_prompt)

    planted = (
        source_data_summary.get("ground_truth_anomalies", {})
        .get("planted", "")
    )
    has_planted = planted and not planted.lower().startswith("none")

    criteria = []
    for cid, desc in templates:
        for intent_key in ("anomaly", "trends", "issues", "leadership"):
            if intents.get(intent_key):
                override = _INTENT_OVERRIDES.get((action_type, cid, intent_key))
                if override:
                    desc = override
                    break
        if action_type == "computation" and cid == "C2" and not has_planted:
            desc = "Anomaly Surfacing: agent must NOT fabricate anomalies on clean data; correct verdict is 'no significant anomalies'"
        criteria.append({"id": cid, "description": desc, "rationale": "mock rubric"})

    return {"criteria": criteria, "_model": "mock"}


def grade_mock_step(step: dict, rubric: dict, source_data_summary: dict) -> dict:
    action_type = step.get("action_type", "")
    key_facts = step.get("key_facts_produced", [])
    gt = step.get("ground_truth", {})

    planted = (
        source_data_summary.get("ground_truth_anomalies", {})
        .get("planted", "")
    )
    has_planted = planted and not planted.lower().startswith("none")

    # Ground truth comparison flags
    gt_issues: list[str] = []
    if gt and action_type == "computation":
        expected_revenue = gt.get("expected_total_revenue")
        if expected_revenue:
            rev_fact = None
            for f in key_facts:
                if "total_revenue" in f.lower():
                    parts = f.split(":", 1)
                    if len(parts) == 2:
                        rev_fact = parts[1].strip().replace(",", "")
                        break
            if rev_fact:
                try:
                    if int(rev_fact) != expected_revenue:
                        gt_issues.append(f"revenue mismatch: reported {rev_fact}, expected {expected_revenue}")
                except ValueError:
                    pass
        expected_count = gt.get("expected_anomaly_count", 0)
        if expected_count > 0:
            det_fact = None
            for f in key_facts:
                if "anomalies_detected" in f.lower():
                    det_fact = f
                    break
            if det_fact:
                import re as _re
                nums = _re.findall(r'\d+', det_fact.split(":")[0]) if ":" in det_fact else []
                if not nums:
                    bracket_content = det_fact.split("[")[-1] if "[" in det_fact else ""
                    reported_count = bracket_content.count("/") + (1 if bracket_content.strip().rstrip("]") else 0)
                else:
                    reported_count = expected_count
                # Check if any expected anomalies are missing from the detected list
                for ea in gt.get("expected_anomalies", []):
                    region_product = ea.split(" ")[0] if " " in ea else ea
                    if not _has_fact([det_fact], region_product.split("/")[0]):
                        gt_issues.append(f"missed anomaly: {ea}")
                        break

    criteria = rubric.get("criteria", [])
    grades = []
    total_score = 0.0

    for c in criteria:
        cid = c["id"]
        score = 0.9  # default: good step
        rationale = "Step appears to have completed successfully"

        if action_type == "computation":
            if cid == "C2":
                if has_planted:
                    if _has_fact(key_facts, "planted_anomaly_caught: false"):
                        score = 0.1
                        rationale = "planted_anomaly_caught: FALSE found in key_facts -- anomaly missed"
                    elif _has_fact(key_facts, "planted_anomaly_caught: true"):
                        score = 1.0
                        rationale = "planted_anomaly_caught: TRUE -- anomaly correctly detected"
                    else:
                        score = 0.7
                        rationale = "anomaly detection status unclear from key_facts"
                else:
                    if _has_fact(key_facts, "false_positive_fabricated: false"):
                        score = 1.0
                        rationale = "Clean data correctly characterised -- no false positives"
                    else:
                        score = 0.85
                        rationale = "No explicit false-positive flag in key_facts; assuming clean"

        elif action_type == "narrative_generation":
            if cid == "C1":
                if has_planted:
                    if _has_fact(key_facts, "planted_anomaly_in_narrative: false") or \
                       _has_fact(key_facts, "south_productb_explicitly_named_in_narrative: false"):
                        score = 0.1
                        rationale = "Narrative does not mention planted anomaly (inherited from Step 2 failure)"
                    elif _has_fact(key_facts, "planted_anomaly_in_narrative: true") or \
                         _has_fact(key_facts, "south_productb_explicitly_named_in_narrative: true"):
                        score = 1.0
                        rationale = "Planted anomaly explicitly named in narrative"
            elif cid == "C2":
                if _has_fact(key_facts, "exec_summary"):
                    score = 1.0
                    rationale = "Executive summary addresses user's analytical goal"
                elif _has_fact(key_facts, "report_depth: insufficient"):
                    score = 0.3
                    rationale = "Narrative too shallow to address user's goal"
            elif cid == "C3":
                if has_planted and (_has_fact(key_facts, "false_clean_verdict_stated: true") or _has_fact(key_facts, "false_clean_verdict: true")):
                    score = 0.0
                    rationale = "Narrative states 'no significant anomalies' despite planted anomaly existing"
            elif cid == "C4":
                if _has_fact(key_facts, "recommendation_1") or _has_fact(key_facts, "investigate"):
                    score = 1.0
                    rationale = "Specific actionable recommendations provided"
                elif has_planted:
                    rec = None
                    for f in key_facts:
                        if "recommendation" in f.lower():
                            rec = f
                            break
                    if rec and ("continue current" in rec.lower() or "no action" in rec.lower()):
                        score = 0.0
                        rationale = "Generic 'continue current strategy' when anomaly exists"

        elif action_type == "data_parsing":
            if _has_fact(key_facts, "missing_values: 0") or _has_fact(key_facts, "null_values_detected: none"):
                score = 1.0
                rationale = "Correct row count, columns validated, no missing values"
            elif _has_fact(key_facts, "month_label_inconsistency_detected: true") and \
                 _has_fact(key_facts, "inconsistency_handled: true"):
                score = 0.95
                rationale = "Month label inconsistency correctly detected and handled"

        # Apply ground truth penalty if issues found
        if gt_issues and cid == "C1" and action_type == "computation":
            score = min(score, 0.3)
            rationale = f"Ground truth mismatch: {gt_issues[0]}"

        grades.append({
            "id": cid,
            "pass": score >= 0.5,
            "score": round(score, 3),
            "rationale": rationale,
        })
        total_score += score

    step_score = total_score / max(len(grades), 1)
    # If any criterion has a raw score of 0, mark the step as failed regardless
    any_zero = any(g.get("score", 0) == 0 for g in grades)
    step_pass = (step_score >= 0.5) and not any_zero

    # Determine failure type
    failure_type = "null"
    if not step_pass:
        if any_zero:
            # Treat zeroed criterion as a critical computation/parsing failure
            if action_type == "data_parsing":
                failure_type = "parsing_error"
            elif action_type == "computation":
                failure_type = "computation_error"
            else:
                failure_type = "reasoning_error"
        else:
            if action_type == "data_parsing":
                failure_type = "parsing_error"
            elif action_type == "computation":
                failure_type = "computation_error"
            elif action_type == "context_handoff":
                failure_type = "context_loss"
            elif action_type in ("report_structuring", "narrative_generation"):
                # If Step 2 failed, this is inherited
                failure_type = "inherited"
            else:
                failure_type = "reasoning_error"

    return {
        "criterion_grades": grades,
        "step_score": round(step_score, 4),
        "step_pass": step_pass,
        "failure_type": failure_type,
        "_model": "mock",
    }


def evaluate_mock_plan(trace: dict) -> dict:
    """Deterministic plan quality based on agent_plan content."""
    plan = trace.get("agent_plan", "").lower()
    score = 0.5
    rationale = "Plan is vague or generic"

    quality_signals = ["pandas", "python-docx", "word", "anomal", "z-score", "chart", "matplotlib"]
    hits = sum(1 for s in quality_signals if s in plan)
    if hits >= 3:
        score = 0.9
        rationale = "Plan mentions specific tools and approach"
    elif hits >= 1:
        score = 0.65
        rationale = "Plan is on-topic but vague"

    return score, rationale
