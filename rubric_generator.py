"""
rubric_generator.py — Generates 3 evaluation criteria via Ollama (local).
"""

import json
import os
import re

from llm_provider import call_llm, get_active_provider, is_llm_available


_SYSTEM_PROMPT = """You are an expert evaluation rubric designer for AI agent workflows.
Your task: given a single step that an AI agent performed, produce exactly 3 evaluation criteria.

Rules:
1. Each criterion must be SPECIFIC to this exact dataset, task, and step — never generic.
2. Each criterion must be FALSIFIABLE: answerable as pass/fail by comparing agent output against the ground truth values provided.
3. At least one criterion must test OUTPUT ACCURACY: does the agent's result match the ground truth numbers/entities?
4. At least one criterion must test PROCESS QUALITY: did the agent use the right method/approach?
5. For computation/narrative steps: if ground_truth planted="None" or "No planted anomaly", criteria must reward reporting organic/statistical outliers and penalise fabricating planted anomalies that do not exist.
6. Return valid JSON only — no prose, no markdown fences.
7. You MUST return exactly this structure: {"criteria": [{"id":"C1",...},{"id":"C2",...},{"id":"C3",...}]}"""

_USER_TEMPLATE = """Evaluate this step and generate 3 specific rubric criteria.

## Trace Context
User Prompt: {user_prompt}
Dataset File: {dataset_file}
Ground Truth Anomalies: {ground_truth_anomalies}
Planted anomaly exists: {has_planted}

## Ground Truth for this step (verified values — criteria should test against these)
{step_ground_truth}

## Step Being Evaluated
Step Number: {step_number}
App: {app}
Action Type: {action_type}
What Agent Did: {what_agent_did}
Step Output: {output}
Key Facts Produced:
{key_facts_produced}

Generate exactly 3 criteria that test BOTH output accuracy (does it match ground truth?) AND process quality.
Return JSON only:
{{
  "criteria": [
    {{"id": "C1", "description": "...", "rationale": "..."}},
    {{"id": "C2", "description": "...", "rationale": "..."}},
    {{"id": "C3", "description": "...", "rationale": "..."}}
  ]
}}"""


def _parse_rubric(raw: str) -> dict:
    text = raw.strip()
    text = text.encode("ascii", errors="ignore").decode("ascii")
    if "```" in text:
        parts = text.split("```")
        for part in parts:
            part = part.strip().lstrip("json").strip()
            if part.startswith("{"):
                text = part
                break
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end])
        except json.JSONDecodeError:
            pass
    raise ValueError(f"Could not parse rubric JSON from: {raw[:120]}")


def _call_llm(system: str, user: str) -> str:
    if not is_llm_available():
        return ""
    return call_llm(system, user)


def generate_rubric(
    step: dict,
    user_prompt: str,
    source_data_summary: dict,
    dataset_file: str = "",
) -> dict:
    """Return a rubric dict with a 'criteria' list of 3 items. Uses Ollama; falls back to mock."""
    ground_truth = source_data_summary.get("ground_truth_anomalies", {})
    planted = ground_truth.get("planted", "")
    has_planted = "NO — do not require planted anomaly in output" if (
        not planted or planted.lower() in ("none", "no planted anomaly", "")
    ) else f"YES — {planted}"
    key_facts_str = "\n".join(f"  - {f}" for f in step.get("key_facts_produced", []))

    gt = step.get("ground_truth", {})
    if gt:
        gt_lines = []
        for k, v in gt.items():
            if isinstance(v, list):
                gt_lines.append(f"  {k}: {', '.join(str(x) for x in v)}")
            else:
                gt_lines.append(f"  {k}: {v}")
        step_gt_text = "\n".join(gt_lines)
    else:
        step_gt_text = "  No ground truth available for this step."

    user_content = _USER_TEMPLATE.format(
        user_prompt=user_prompt,
        dataset_file=dataset_file,
        ground_truth_anomalies=json.dumps(ground_truth),
        has_planted=has_planted,
        step_number=step.get("step_number"),
        app=step.get("app"),
        action_type=step.get("action_type"),
        what_agent_did=step.get("what_agent_did", ""),
        output=(step.get("output", ""))[:400],
        key_facts_produced=key_facts_str,
        step_ground_truth=step_gt_text,
    )

    for attempt in range(3):
        try:
            raw = _call_llm(_SYSTEM_PROMPT, user_content)
            if not raw or not raw.strip():
                break
            rubric = _parse_rubric(raw)
            criteria = rubric.get("criteria", [])
            if not isinstance(criteria, list) or len(criteria) == 0:
                raise ValueError(f"Rubric has no valid criteria list: {rubric}")
            rubric["_model"] = get_active_provider()
            return rubric
        except Exception:
            pass

    import mock_llm
    return mock_llm.generate_mock_rubric(step, source_data_summary, user_prompt=user_prompt)
