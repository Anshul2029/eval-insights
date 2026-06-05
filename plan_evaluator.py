"""
plan_evaluator.py — Mix of deterministic Python + one LLM call (Claude preferred,
falls back to gpt-4o-mini when Claude credits are exhausted).

Metrics computed:
  1. app_coverage       — did Excel and Word both appear in steps?
  2. sequence_validity  — parsing -> computation -> handoff in correct order?
  3. anomaly_recall     — did planted anomaly appear in Step 5 narrative?
  4. false_positive     — did agent fabricate findings on clean/no-anomaly data?
  5. plan_quality       — LLM-assessed quality of the agent_plan

Output: plan_score (0-1) + issues list + metric_breakdown dict
"""

import json
import os
import re

from llm_provider import call_llm, get_active_provider, is_llm_available


def _call_llm_plan(system: str, user: str) -> str:
    if not is_llm_available():
        return ""
    return call_llm(system, user)

ACTION_ORDER = {
    "data_parsing": 1,
    "computation": 2,
    "context_handoff": 3,
    "report_structuring": 4,
    "narrative_generation": 5,
}




# ─── deterministic checks ────────────────────────────────────────────────────

def _check_app_coverage(steps: list) -> tuple:
    apps = {s.get("app", "").lower() for s in steps}
    has_excel = any("excel" in a for a in apps)
    has_word = any("word" in a for a in apps)
    if has_excel and has_word:
        return 1.0, None
    missing = []
    if not has_excel:
        missing.append("Excel")
    if not has_word:
        missing.append("Word")
    return 0.5, f"App coverage missing: {', '.join(missing)}"


def _check_sequence_validity(steps: list) -> tuple:
    ordered = sorted(steps, key=lambda s: s.get("step_number", 99))
    prev_order = 0
    for step in ordered:
        action = step.get("action_type", "")
        current_order = ACTION_ORDER.get(action, 0)
        if current_order and current_order < prev_order:
            return 0.0, (
                f"Sequence invalid: '{action}' (step {step['step_number']}) "
                f"appears after a later-stage action"
            )
        if current_order:
            prev_order = current_order
    return 1.0, None


def _check_anomaly_recall(trace: dict) -> tuple:
    planted = (
        trace.get("source_data_summary", {})
        .get("ground_truth_anomalies", {})
        .get("planted", "")
    )

    if not planted or planted.lower().startswith("none"):
        return 1.0, None

    # Extract specific product and month tokens from the planted anomaly description
    specific_tokens = re.findall(r"Product_[A-Za-z0-9]+", planted)
    month_tokens = re.findall(
        r"\b(?:March|Mar|August|Aug|January|Jan|Feb|February|"
        r"April|Apr|May|Jun|June|Jul|July|Sep|Oct|Nov|Dec)\b",
        planted,
    )

    word_output = trace.get("word_output_actual_text", {})
    flat = " ".join(str(v) for v in word_output.values() if isinstance(v, (str, list))).lower()
    for v in word_output.values():
        if isinstance(v, list):
            flat += " " + " ".join(str(i) for i in v).lower()

    # Hard fail: if the report's primary verdict is "no anomalies found",
    # the planted anomaly was not recalled even if the product name appears elsewhere.
    negative_verdict_phrases = [
        "no significant anomal",
        "no anomal",
        "no significant outlier",
        "no outlier",
        "dataset appears consistent with no",
        "data looks clean",
        "no unusual",
    ]
    if any(phrase in flat for phrase in negative_verdict_phrases):
        return 0.0, (
            "Word output states a negative verdict ('no significant anomalies' or similar) "
            f"despite planted anomaly: '{planted[:100]}'"
        )

    # Require: specific product token near an anomaly context word (within 300 chars)
    anomaly_context_words = ["anomal", "outlier", "drop", "decline", "unusual",
                              "investigate", "fell", "dropped", "spike", "deviation"]
    matched_products = [t for t in specific_tokens if t.lower() in flat]
    matched_months = [t for t in month_tokens if t.lower() in flat]

    # Proximity check: product token must appear within 300 chars of an anomaly context word
    def near_anomaly_context(product_token: str) -> bool:
        token = product_token.lower()
        idx = flat.find(token)
        while idx != -1:
            window = flat[max(0, idx - 300): idx + 300]
            if any(w in window for w in anomaly_context_words):
                return True
            idx = flat.find(token, idx + 1)
        return False

    products_near_anomaly = [t for t in matched_products if near_anomaly_context(t)]

    if products_near_anomaly and matched_months:
        return 1.0, None

    return 0.0, (
        f"Planted anomaly not properly reported in Word output. "
        f"Products near anomaly context: {products_near_anomaly}, "
        f"matched months: {matched_months}. Planted: '{planted[:100]}'"
    )


def _check_false_positive(trace: dict) -> tuple:
    planted = (
        trace.get("source_data_summary", {})
        .get("ground_truth_anomalies", {})
        .get("planted", "")
    )
    variant = trace.get("dataset_variant", "")
    is_clean = (
        planted.lower().startswith("none")
        or "no_anomaly" in variant
        or "clean_baseline" in variant
    )
    if not is_clean:
        return 1.0, None

    word_output = trace.get("word_output_actual_text", {})
    combined = " ".join(
        str(v) for v in word_output.values() if isinstance(v, (str, list))
    ).lower()

    fabrication_signals = [
        "critical anomaly", "major anomaly", "anomaly detected",
        "significant outlier found", "severe drop",
    ]
    hit = next((s for s in fabrication_signals if s in combined), None)
    if hit:
        return 0.0, f"False positive: narrative contains '{hit}' on clean dataset"
    return 1.0, None


# ─── LLM check ───────────────────────────────────────────────────────────────

_PLAN_SYSTEM = (
    "You are evaluating whether an AI agent's high-level plan is appropriate "
    "for the user's request. Return valid JSON only."
)

_PLAN_USER = """Rate the quality of this agent plan.

User Prompt: {user_prompt}
Agent Plan: {agent_plan}
Dataset: {dataset_file}
Ground Truth (for context): {ground_truth}

Score from 0.0 to 1.0:
- 1.0: Plan is specific, mentions right tools/approach, addresses the user's actual question
- 0.5: Plan is vague but on-topic
- 0.0: Plan is irrelevant, generic boilerplate, or wildly wrong approach

Return JSON only:
{{"plan_quality_score": 0.0, "plan_quality_rationale": "one sentence"}}"""


def _check_plan_quality(trace: dict) -> tuple:
    ground_truth = json.dumps(
        trace.get("source_data_summary", {}).get("ground_truth_anomalies", {})
    )
    content = _PLAN_USER.format(
        user_prompt=trace.get("user_prompt", ""),
        agent_plan=trace.get("agent_plan", ""),
        dataset_file=trace.get("dataset_file", ""),
        ground_truth=ground_truth,
    )
    for attempt in range(2):
        try:
            raw = _call_llm_plan(_PLAN_SYSTEM, content)
            if not raw or not raw.strip():
                break
            raw = raw.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1].lstrip("json").strip()
            start, end = raw.find("{"), raw.rfind("}") + 1
            if start >= 0 and end > start:
                raw = raw[start:end]
            result = json.loads(raw)
            return float(result.get("plan_quality_score", 0.5)), result.get(
                "plan_quality_rationale", ""
            )
        except Exception:
            pass
    import mock_llm as _mock
    return _mock.evaluate_mock_plan(trace)


# ─── main entry ──────────────────────────────────────────────────────────────

def evaluate_plan(trace: dict) -> dict:
    steps = trace.get("steps", [])
    issues = []

    app_score, app_issue = _check_app_coverage(steps)
    if app_issue:
        issues.append(app_issue)

    seq_score, seq_issue = _check_sequence_validity(steps)
    if seq_issue:
        issues.append(seq_issue)

    recall_score, recall_issue = _check_anomaly_recall(trace)
    if recall_issue:
        issues.append(recall_issue)

    fp_score, fp_issue = _check_false_positive(trace)
    if fp_issue:
        issues.append(fp_issue)

    plan_quality_score, plan_quality_rationale = _check_plan_quality(trace)

    metric_breakdown = {
        "app_coverage": round(app_score, 4),
        "sequence_validity": round(seq_score, 4),
        "anomaly_recall": round(recall_score, 4),
        "false_positive_check": round(fp_score, 4),
        "plan_quality": round(plan_quality_score, 4),
        "plan_quality_rationale": plan_quality_rationale,
    }

    plan_score = (
        app_score * 0.20
        + seq_score * 0.20
        + recall_score * 0.30
        + fp_score * 0.15
        + plan_quality_score * 0.15
    )

    return {
        "plan_score": round(plan_score, 4),
        "issues": issues,
        "metric_breakdown": metric_breakdown,
    }
