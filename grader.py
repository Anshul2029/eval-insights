"""
grader.py — Grades each step via Ollama (local); falls back to mock.

failure_type options:
  computation_error | context_loss | reasoning_error | parsing_error | inherited | null
"""

import json
import os
import re

from llm_provider import call_llm, get_active_provider, is_llm_available


_SYSTEM_PROMPT = (
    "You are an expert AI agent evaluator. "
    "Grade each rubric criterion by comparing the agent's output against the provided ground truth values. "
    "Check factual accuracy (numbers, entities, counts) AND process quality (method, granularity). "
    "If the agent's numbers or findings contradict ground truth, FAIL that criterion. "
    "Be specific: quote mismatches. "
    "Return valid JSON only — no prose, no markdown fences."
)

_USER_TEMPLATE = """Grade this agent step.

Step {step_number} | {app} | {action_type}
Did: {what_agent_did}
Output: {output}
Key facts: {key_facts_produced}
Prior steps: {prior_context}

## Ground Truth (verified values — compare agent output against these)
{ground_truth}

Rubric: {criteria_text}

Grade each criterion. If the agent's numbers or findings contradict ground truth, FAIL that criterion.
step_score=mean, step_pass=(score>=0.5).
failure_type if failed: parsing_error|computation_error|context_loss|reasoning_error|inherited|null

Return JSON only:
{{"criterion_grades":[{{"id":"C1","pass":true,"score":0.9,"rationale":"..."}}],"step_score":0.0,"step_pass":false,"failure_type":"null"}}"""


def _call_llm(system: str, user: str) -> str:
    if not is_llm_available():
        return ""
    return call_llm(system, user)


def _grade_with_quota_fallback(
    step: dict, rubric: dict, prior_ctx: str, source_data_summary: dict | None
) -> dict:
    """Grade via Ollama. Falls back to mock."""
    criteria_text = "\n".join(f"{c['id']}: {c['description']}" for c in rubric.get("criteria", []))
    key_facts_str = ", ".join(step.get("key_facts_produced", []))

    gt = step.get("ground_truth", {})
    if gt:
        gt_lines = []
        for k, v in gt.items():
            if isinstance(v, list):
                gt_lines.append(f"  {k}: {', '.join(str(x) for x in v)}")
            else:
                gt_lines.append(f"  {k}: {v}")
        ground_truth_text = "\n".join(gt_lines)
    else:
        ground_truth_text = "  No ground truth available for this step."

    user_content = _USER_TEMPLATE.format(
        step_number=step.get("step_number"),
        app=step.get("app"),
        action_type=step.get("action_type"),
        what_agent_did=step.get("what_agent_did", ""),
        output=(step.get("output", ""))[:400],
        key_facts_produced=key_facts_str,
        prior_context=prior_ctx[:300],
        criteria_text=criteria_text,
        ground_truth=ground_truth_text,
    )

    for attempt in range(2):
        try:
            raw = _call_llm(_SYSTEM_PROMPT, user_content)
            if not raw or not raw.strip():
                break
            raw = raw.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1].lstrip("json").strip()
            start, end = raw.find("{"), raw.rfind("}") + 1
            if start >= 0 and end > start:
                raw = raw[start:end]
            grade = json.loads(raw)
            grade["step_score"] = float(grade.get("step_score", 0.0))
            grade["step_pass"]  = bool(grade.get("step_pass", False))
            grade["_model"]     = get_active_provider()
            return grade
        except Exception:
            pass

    import mock_llm
    return mock_llm.grade_mock_step(step, rubric, source_data_summary or {})


def build_prior_context(step_results: list) -> str:
    """Summarise what prior steps produced, for inherited-failure detection."""
    if not step_results:
        return "No prior steps."

    lines = []
    for r in step_results:
        step_num = r["step"]["step_number"]
        passed   = r["grade"].get("step_pass", True)
        score    = r["grade"].get("step_score", 1.0)
        key_facts = r["step"].get("key_facts_produced", [])
        status = "PASSED" if passed else f"FAILED (score={score:.2f})"
        lines.append(f"Step {step_num} ({r['step']['action_type']}): {status}")
        for f in key_facts[:6]:
            lines.append(f"  - {f}")
        if not passed:
            ftype = r["grade"].get("failure_type", "unknown")
            lines.append(f"  *** failure_type: {ftype} ***")

    return "\n".join(lines)
