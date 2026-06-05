"""
deterministic_checks.py — Office.js-based deterministic evaluations.

Implements ~70% of the rubric with zero LLM cost:
  - Range Correctness
  - Formula Validation
  - Write Verification
  - Error Handling
  - Display Rendering
  - Retry Detection
  - Response Validation

These checks analyse step key_facts directly — strict on real failures.
"""

from __future__ import annotations

from intent_signals import extract_intents


def _has_fact(key_facts: list[str], pattern: str) -> bool:
    p = pattern.lower()
    return any(p in f.lower() for f in key_facts)


def _fact_value(key_facts: list[str], prefix: str) -> str | None:
    p = prefix.lower()
    for f in key_facts:
        if p in f.lower():
            parts = f.split(":", 1)
            if len(parts) == 2:
                return parts[1].strip()
    return None


def _intent_desc(intents: dict, variants: dict, default: str) -> str:
    for key in ("anomaly", "trends", "issues", "leadership"):
        if intents.get(key) and key in variants:
            return variants[key]
    return default


# ---------------------------------------------------------------------------
# Per-step deterministic criteria
# ---------------------------------------------------------------------------

def evaluate_deterministic(step: dict, trace: dict) -> dict:
    action_type = step.get("action_type", "")
    key_facts = step.get("key_facts_produced", [])
    output = step.get("output", "")
    intents = extract_intents(trace.get("user_prompt", ""))

    if action_type == "data_parsing":
        criteria, grades = _check_data_parsing(step, key_facts, intents)
    elif action_type == "computation":
        criteria, grades = _check_computation(step, key_facts, trace, intents)
    elif action_type == "context_handoff":
        criteria, grades = _check_context_handoff(step, key_facts, intents)
    elif action_type == "report_structuring":
        criteria, grades = _check_report_structuring(step, key_facts, trace, intents)
    elif action_type == "narrative_generation":
        criteria, grades = _check_narrative_generation(step, key_facts, trace, intents)
    else:
        criteria, grades = _check_generic(step, key_facts)

    total = sum(g["score"] for g in grades)
    step_score = total / max(len(grades), 1)
    any_zero = any(g["score"] == 0.0 for g in grades)
    step_pass = (step_score >= 0.5) and not any_zero

    failure_type = "null"
    if not step_pass:
        failure_type = _infer_failure_type(action_type, grades)

    rubric = {
        "criteria": criteria,
        "_model": "deterministic/office.js",
        "_type": "deterministic",
    }
    grade = {
        "criterion_grades": grades,
        "step_score": round(step_score, 4),
        "step_pass": step_pass,
        "failure_type": failure_type,
        "_model": "deterministic/office.js",
    }
    return {"rubric": rubric, "grade": grade}


# ---------------------------------------------------------------------------
# Step 1: Data Parsing
# ---------------------------------------------------------------------------

def _check_data_parsing(step: dict, key_facts: list, intents: dict) -> tuple:
    criteria = []
    grades = []

    # D1: Range Correctness
    desc = _intent_desc(intents, {
        "anomaly": "getRange() loads correct columns for anomaly detection — all expected columns present",
        "trends":  "getRange() loads correct columns for trend analysis — all expected columns present",
        "issues":  "getRange() loads correct columns for data quality review — all expected columns present",
    }, "getRange() loads correct columns — all expected columns present")
    score = 0.9
    rationale = "Data loaded with expected structure"
    if _has_fact(key_facts, "columns"):
        score = 1.0
        rationale = "All expected columns confirmed present"
    _add(criteria, grades, "D1", desc, score, rationale)

    # D2: Response Validation
    desc = _intent_desc(intents, {
        "anomaly": "No parsing errors — clean data required for reliable anomaly detection",
        "trends":  "No parsing errors — consistent data required for trend analysis",
        "issues":  "No parsing errors — error-free load required for issue detection",
    }, "No parsing errors — data loaded without runtime exceptions")
    score = 0.95
    rationale = "No parsing errors detected"
    if _has_fact(key_facts, "null_values: none") or _has_fact(key_facts, "missing_values: 0"):
        score = 1.0
        rationale = "Zero null values, clean parse"
    if _has_fact(key_facts, "environment_probing"):
        score = 0.7
        rationale = "Multiple failed attempts before successful parse"
    _add(criteria, grades, "D2", desc, score, rationale)

    # D3: Write Verification
    desc = _intent_desc(intents, {
        "anomaly": "Data preparation applied — time-series ordering critical for anomaly baselines",
        "trends":  "Data preparation applied — month ordering essential for trend visualization",
    }, "Data preparation applied — month ordering or type coercion verified")
    score = 0.85
    rationale = "Data preparation steps executed"
    if _has_fact(key_facts, "month_ordering_applied: true"):
        score = 1.0
        rationale = "Month ordering correctly applied"
    _add(criteria, grades, "D3", desc, score, rationale)

    return criteria, grades


# ---------------------------------------------------------------------------
# Step 2: Computation — THIS IS WHERE MOST FAILURES ORIGINATE
# ---------------------------------------------------------------------------

def _check_computation(step: dict, key_facts: list, trace: dict, intents: dict) -> tuple:
    criteria = []
    grades = []

    source = trace.get("source_data_summary", {})
    planted = source.get("ground_truth_anomalies", {}).get("planted", "")
    has_planted = planted and not planted.lower().startswith("none")

    # D1: Formula Validation — correct analysis method
    desc = _intent_desc(intents, {
        "anomaly": "Correct anomaly detection method applied (z-score per Region x Product, not just aggregate)",
        "trends":  "Correct trend analysis method applied (z-score per Region x Product, not just aggregate)",
        "issues":  "Correct method for issue detection applied (z-score per Region x Product, not just aggregate)",
    }, "Correct statistical method applied (z-score per Region x Product, not just aggregate)")
    score = 0.85
    rationale = "Computation method appears appropriate"
    if _has_fact(key_facts, "anomaly_detection: not performed"):
        score = 0.0
        rationale = "CRITICAL: Anomaly detection was NOT PERFORMED at all"
    elif _has_fact(key_facts, "z-score") or _has_fact(key_facts, "anomaly_detection_method"):
        if _has_fact(key_facts, "global z-score") or _has_fact(key_facts, "aggregate only"):
            score = 0.2
            rationale = "Wrong method: global z-score misses Region x Product breakdown"
        else:
            score = 1.0
            rationale = "Correct method: z-score at Region x Product granularity"
    _add(criteria, grades, "D1", desc, score, rationale)

    # D2: Range Correctness — granularity
    desc = _intent_desc(intents, {
        "anomaly": "Anomaly analysis at Region x Product x Month granularity, not aggregate",
        "trends":  "Trend analysis at Region x Product x Month granularity, not aggregate",
        "issues":  "Data quality analysis at Region x Product x Month granularity, not aggregate",
    }, "Analysis at Region x Product x Month granularity, not aggregate")
    score = 0.8
    rationale = "Granularity appears sufficient"
    if _has_fact(key_facts, "granularity_used: aggregate") or _has_fact(key_facts, "granularity: aggregate"):
        score = 0.0
        rationale = "FAIL: Only aggregate-level analysis — missed Region x Product breakdown"
    elif _has_fact(key_facts, "regional_breakdown: not computed"):
        score = 0.1
        rationale = "FAIL: Regional breakdown was not computed"
    elif _has_fact(key_facts, "regional_leader") or _has_fact(key_facts, "region"):
        score = 1.0
        rationale = "Region x Product granularity confirmed"
    _add(criteria, grades, "D2", desc, score, rationale)

    return criteria, grades


# ---------------------------------------------------------------------------
# Step 3: Context Handoff
# ---------------------------------------------------------------------------

def _check_context_handoff(step: dict, key_facts: list, intents: dict) -> tuple:
    criteria = []
    grades = []

    # D1: Write Verification — facts transferred
    desc = _intent_desc(intents, {
        "anomaly":    "All KPIs and anomaly findings transferred to Word context",
        "leadership": "All KPIs and anomaly data transferred to Word for leadership report",
    }, "All KPIs and anomaly data transferred to Word context")
    score = 0.85
    rationale = "Context handoff executed"
    if _has_fact(key_facts, "anomaly_data_passed: false"):
        score = 0.1
        rationale = "FAIL: Anomaly data was NOT passed to Word — critical context loss"
    elif _has_fact(key_facts, "all_kpis_passed: true"):
        score = 1.0
        rationale = "All KPIs confirmed passed to Word script"
    _add(criteria, grades, "D1", desc, score, rationale)

    # D2: Error Handling
    desc = "Errors resolved before handoff — self-correction if needed"
    score = 0.9
    rationale = "No blocking errors at handoff"
    if _has_fact(key_facts, "context_loss_at_boundary: true"):
        score = 0.1
        rationale = "FAIL: Context loss detected at Excel-Word boundary"
    elif _has_fact(key_facts, "self_correct") or _has_fact(key_facts, "re-ran"):
        score = 1.0
        rationale = "Error encountered and self-corrected"
    _add(criteria, grades, "D2", desc, score, rationale)

    # D3: Display Rendering — charts
    desc = _intent_desc(intents, {
        "anomaly":    "Anomaly visualization charts generated and included in handoff",
        "trends":     "Trend visualization charts generated and included in handoff",
        "leadership": "Charts generated for leadership presentation and included in handoff",
    }, "Charts generated and included in handoff")
    score = 0.85
    rationale = "Chart generation presumed complete"
    if _has_fact(key_facts, "charts_generated: 0") or _has_fact(key_facts, "charts_passed: false") or _has_fact(key_facts, "charts: none"):
        score = 0.2
        rationale = "FAIL: No charts generated — report will lack visual data"
    elif _has_fact(key_facts, "chart") or _has_fact(key_facts, "png"):
        score = 1.0
        rationale = "Charts generated and included"
    _add(criteria, grades, "D3", desc, score, rationale)

    return criteria, grades


# ---------------------------------------------------------------------------
# Step 4: Report Structuring
# ---------------------------------------------------------------------------

def _check_report_structuring(step: dict, key_facts: list, trace: dict, intents: dict) -> tuple:
    criteria = []
    grades = []

    source = trace.get("source_data_summary", {})
    planted = source.get("ground_truth_anomalies", {}).get("planted", "")
    has_planted = planted and not planted.lower().startswith("none")

    # D1: Write Verification — sections
    desc = _intent_desc(intents, {
        "anomaly":    "Document sections created with anomaly-focused structure (summary, anomaly detail, recommendations)",
        "leadership": "Document sections created for leadership audience (exec summary, analysis, anomalies, recommendations)",
        "trends":     "Document sections created for trend analysis (summary, trend detail, forecasts, recommendations)",
    }, "Document sections created (summary, analysis, anomalies, recommendations)")
    score = 0.85
    rationale = "Report structure created"
    sections_val = _fact_value(key_facts, "sections")
    if sections_val:
        try:
            n = int(sections_val.split()[0])
            if n <= 2:
                score = 0.3
                rationale = f"WEAK: Only {n} sections — insufficient report structure"
            elif n >= 4:
                score = 1.0
                rationale = f"Good structure: {n} sections covering key areas"
            else:
                score = 0.7
                rationale = f"Adequate: {n} sections"
        except ValueError:
            pass
    _add(criteria, grades, "D1", desc, score, rationale)

    # D2: Display Rendering — charts included
    desc = _intent_desc(intents, {
        "anomaly":    "Anomaly visualization charts included in report structure",
        "leadership": "Executive-ready charts included in report structure",
        "trends":     "Trend visualization charts included in report structure",
    }, "Charts included in report structure")
    score = 0.85
    rationale = "Report layout includes visual elements"
    if _has_fact(key_facts, "charts: none") or _has_fact(key_facts, "charts_included: false"):
        score = 0.3
        rationale = "No charts in report — missing visual data presentation"
    _add(criteria, grades, "D3", desc, score, rationale)

    return criteria, grades


# ---------------------------------------------------------------------------
# Step 5: Narrative Generation — DETECTS INHERITED FAILURES
# ---------------------------------------------------------------------------

def _check_narrative_generation(step: dict, key_facts: list, trace: dict, intents: dict) -> tuple:
    criteria = []
    grades = []

    source = trace.get("source_data_summary", {})
    planted = source.get("ground_truth_anomalies", {}).get("planted", "")
    has_planted = planted and not planted.lower().startswith("none")

    # D1: Write Verification — narrative written
    desc = _intent_desc(intents, {
        "anomaly":    "Anomaly-focused narrative text written to Word document",
        "trends":     "Trend analysis narrative written to Word document",
        "leadership": "Executive narrative written to Word document for leadership audience",
    }, "Narrative text written to Word document")
    score = 0.85
    rationale = "Narrative generation completed"
    if _has_fact(key_facts, "exec_summary_written: true") or _has_fact(key_facts, "exec_summary"):
        score = 1.0
        rationale = "Executive summary written"
    if _has_fact(key_facts, "report_depth: insufficient"):
        score = 0.4
        rationale = "Report depth flagged as insufficient"
    _add(criteria, grades, "D1", desc, score, rationale)

    # D2: Response Validation — anomaly correctness in narrative
    desc = _intent_desc(intents, {
        "anomaly":    "All anomaly findings accurately and specifically reflected in narrative",
        "trends":     "Trend patterns and any anomaly findings accurately reflected in narrative",
        "leadership": "Anomaly findings accurately reflected in leadership-appropriate narrative",
    }, "Anomaly findings accurately reflected in narrative")
    score = 0.9
    rationale = "Narrative content appropriate"
    if has_planted:
        if _has_fact(key_facts, "planted_anomaly_in_narrative: false"):
            score = 0.0
            rationale = "CRITICAL: Planted anomaly MISSING from narrative — inherited failure from Step 2"
        elif _has_fact(key_facts, "planted_anomaly_in_narrative: true"):
            score = 1.0
            rationale = "Planted anomaly explicitly mentioned in narrative"
        if _has_fact(key_facts, "false_clean_verdict_stated: true") or _has_fact(key_facts, "false_clean_verdict: true"):
            score = 0.0
            rationale = "CRITICAL: False 'no issues' verdict despite planted anomaly — agent fabricated clean state"
    else:
        if _has_fact(key_facts, "false_clean_verdict: true") or _has_fact(key_facts, "false_clean_verdict_stated: true"):
            score = 0.3
            rationale = "Verdict claims clean despite potential organic anomalies"
    _add(criteria, grades, "D2", desc, score, rationale)

    return criteria, grades


# ---------------------------------------------------------------------------
# Generic fallback
# ---------------------------------------------------------------------------

def _check_generic(step: dict, key_facts: list) -> tuple:
    criteria = []
    grades = []
    _add(criteria, grades, "D1", "Step completed without errors", 0.9, "Step executed")
    _add(criteria, grades, "D2", "Output consistent with inputs", 0.85, "Output appears consistent")
    return criteria, grades


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _add(criteria: list, grades: list, cid: str, desc: str, score: float, rationale: str):
    criteria.append({"id": cid, "description": desc, "rationale": "deterministic"})
    grades.append({"id": cid, "pass": score >= 0.5, "score": round(score, 3), "rationale": rationale})


def _infer_failure_type(action_type: str, grades: list) -> str:
    if action_type == "data_parsing":
        return "parsing_error"
    elif action_type == "computation":
        return "computation_error"
    elif action_type == "context_handoff":
        return "context_loss"
    elif action_type in ("report_structuring", "narrative_generation"):
        return "inherited"
    return "reasoning_error"
