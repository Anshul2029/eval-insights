"""
Evidence-based failure classifier for evalVNext agent trajectories.

Classification is FULLY dynamic -- no hardcoded categories or thresholds.
Gemini reads the grader's own explanations and clusters failures into
natural groups. Falls back to simple signal-based grouping if no API key.

The classifier NEVER infers root cause from the agent's Thoughts content.
"""
from __future__ import annotations

import json
import hashlib
import os
import re
import sys
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
load_dotenv()
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))

from trajectory_parser import (
    ParsedEvalCase,
    auto_discover_batches,
    parse_report_file,
)

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Two-axis category definitions
# ---------------------------------------------------------------------------

TRAJECTORY_CATEGORIES = {
    "Tool Misuse": "Agent calls the WRONG API method, function, or passes incorrect parameters (e.g., wrong property name, wrong range address). REQUIRES: at least one step where the agent invoked an API incorrectly.",
    "Context Loss": "Agent loses track of earlier context, forgets previous results, or ignores state it already retrieved. REQUIRES: evidence that the agent had information earlier in the trajectory but failed to use it later.",
    "Goal Drift": "Agent starts working on the correct task but drifts to solving a different problem. REQUIRES: a clear pivot point where the agent's actions diverge from the original query intent.",
    "Retry Loops": "Agent repeats the SAME or near-identical failing approach 2+ times without meaningful adaptation. REQUIRES: at least 2 consecutive similar attempts that fail the same way. A single error is NOT a retry loop.",
    "Silent Quality Degradation": "No script errors in trajectory but the final output is wrong or incomplete. REQUIRES: trajectory shows no errors yet the grader reports incorrect results.",
    "Cascading Errors": "One initial error triggers a chain of 2+ downstream failures. REQUIRES: a clear first error and subsequent errors that are caused by or related to it.",
}

OUTCOME_CATEGORIES = {
    "Instructions/refusal instead of action": "Agent gives a formula or explains how instead of computing the value",
    "Wrong intent disambiguation": "Agent picks the wrong interpretation of an ambiguous prompt",
    "Incomplete enumeration": "Right approach but drops 1-2 items in a multi-step task",
    "Column overflow (####)": "Correct values but column width too narrow, grader sees ####",
    "Missing data-quality caveat": "Agent knows about a limitation internally but doesn't surface it",
    "Claim doesn't match API contract": "Agent says it did X but Office.js values don't match",
    "Wrong metadata on correct row": "Numbers/data are right but labels or headers are wrong",
}


# ---------------------------------------------------------------------------
# Classification result -- two-axis (trajectory + outcome)
# ---------------------------------------------------------------------------

@dataclass
class ClassificationResult:
    query_index: str
    batch: str
    query_text: str
    primary_category: str
    confidence: str  # "high", "medium", "low"
    evidence: list[str]
    why: str = ""
    secondary_categories: list[str] = field(default_factory=list)
    suggested_fix: Optional[str] = None
    trajectory_category: str = ""
    trajectory_confidence: str = ""
    trajectory_categories: list[str] = field(default_factory=list)
    outcome_category: str = ""
    outcome_confidence: str = ""

    def __post_init__(self):
        flat = []
        for item in self.trajectory_categories:
            if isinstance(item, list):
                flat.extend(item)
            else:
                flat.append(item)
        self.trajectory_categories = flat

    def to_dict(self) -> dict:
        return {
            "query_index": self.query_index,
            "batch": self.batch,
            "query_text": self.query_text,
            "primary_category": self.primary_category,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "why": self.why,
            "secondary_categories": self.secondary_categories,
            "suggested_fix": self.suggested_fix,
            "trajectory_category": self.trajectory_category,
            "trajectory_confidence": self.trajectory_confidence,
            "trajectory_categories": self.trajectory_categories,
            "outcome_category": self.outcome_category,
            "outcome_confidence": self.outcome_confidence,
        }


# ---------------------------------------------------------------------------
# Evidence extraction from evaluationResults
# ---------------------------------------------------------------------------

@dataclass
class GraderEvidence:
    evaluator_type: str = "unknown"
    outcome: str = "unknown"
    grader_message: str = ""
    expected: str = ""
    received: str = ""
    evaluation_strategy: str = ""
    assertions: list[dict] = field(default_factory=list)
    assertion_scores: Optional[dict] = None
    total_correct: int = 0
    total_incorrect: int = 0
    total_missing: int = 0
    has_partial_scores: bool = False
    llm_metrics: Optional[dict] = None


def extract_grader_evidence(case: ParsedEvalCase) -> GraderEvidence:
    """Extract all grader-provided evidence from evaluation results."""
    ev = GraderEvidence()

    for er in case.evaluation_results:
        name = er.get("name", "")
        kind = er.get("kind", "")
        value = er.get("value")

        if name == "officejs_assertion_outcome" and kind == "Boolean":
            ev.outcome = "pass" if value else "fail"
            ev.evaluator_type = "Office.js (deterministic)"

        elif name == "grader" and kind == "Boolean":
            ev.outcome = "pass" if value else "fail"
            if ev.evaluator_type == "unknown":
                ev.evaluator_type = "LLM grader"

        elif name == "vllm_assertion_outcome" and kind == "Boolean":
            ev.outcome = "pass" if value else "fail"
            ev.evaluator_type = "Vision LLM"

        elif name == "officejs_assertion_message":
            if isinstance(value, str):
                try:
                    msgs = json.loads(value.replace("'", '"'))
                except (json.JSONDecodeError, ValueError):
                    msgs = [{"reason": value}]
            elif isinstance(value, list):
                msgs = value
            else:
                msgs = []
            for msg in msgs[:10]:
                if isinstance(msg, dict):
                    ev.assertions.append({
                        "type": msg.get("type", ""),
                        "reason": msg.get("reason", ""),
                        "expected": str(msg.get("expected", "")),
                        "actual": str(msg.get("actual", "")),
                    })

        elif name == "details":
            if isinstance(value, dict):
                for assert_name, assert_data in value.items():
                    if isinstance(assert_data, dict):
                        msg = assert_data.get("message", "")
                        exp = str(assert_data.get("expected", "") or "")
                        rcv = str(assert_data.get("received", "") or "")
                        strategy = assert_data.get("evaluation_strategy_used", "")
                        lm = assert_data.get("llm_metrics")

                        if msg and not ev.grader_message:
                            ev.grader_message = msg[:2000]
                        if exp and not ev.expected:
                            ev.expected = exp[:1000]
                        if rcv and not ev.received:
                            ev.received = rcv[:1000]
                        if strategy:
                            ev.evaluation_strategy = strategy
                        if lm and isinstance(lm, dict):
                            ev.llm_metrics = lm

                        ev.assertions.append({
                            "type": "assertion",
                            "reason": msg,
                            "expected": exp,
                            "actual": rcv,
                        })

        elif name == "assertion_scores":
            if isinstance(value, dict):
                ev.assertion_scores = value
                for assert_name, score_data in value.items():
                    if isinstance(score_data, dict):
                        prim = score_data.get("primitive", {})
                        ev.total_correct += prim.get("correct", 0)
                        ev.total_incorrect += prim.get("incorrect", 0)
                        ev.total_missing += prim.get("missing", 0)
                if ev.total_correct > 0 and ev.total_incorrect > 0:
                    ev.has_partial_scores = True

    if not ev.grader_message and ev.assertions:
        first = ev.assertions[0]
        if first.get("reason"):
            ev.grader_message = first["reason"]
        elif first.get("expected") and first.get("actual"):
            ev.grader_message = (
                f"Expected: {first['expected'][:200]} | "
                f"Got: {first['actual'][:200]}"
            )

    return ev


# ---------------------------------------------------------------------------
# LLM API helpers (Groq preferred, Gemini fallback)
# ---------------------------------------------------------------------------

def _call_groq(prompt: str, groq_key: str, max_tokens: int = 2000, temp: float = 0.0) -> str:
    """Call Groq API (Llama 3.3 70B). Returns empty string on failure."""
    if not groq_key:
        return ""
    try:
        import requests as _req
        resp = _req.post(
            "https://api.groq.com/openai/v1/chat/completions",
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": temp,
            },
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {groq_key}",
            },
            timeout=90,
        )
        if resp.status_code != 200:
            print(f"[classifier] Groq status {resp.status_code}: {resp.text[:200]}")
            return ""
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"[classifier] Groq error: {e}")
        return ""


def _call_gemini(prompt: str, gem_key: str, max_tokens: int = 2000, temp: float = 0.0) -> str:
    """Call Gemini and return text response. Returns empty string on failure."""
    if not gem_key:
        return ""
    try:
        body = json.dumps({
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": max_tokens, "temperature": temp},
        }).encode("utf-8")
        url = (
            "https://generativelanguage.googleapis.com/v1beta/"
            f"models/gemini-2.5-flash:generateContent?key={gem_key}"
        )
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=90) as r:
            data = json.loads(r.read())
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        text = re.sub(r"<think>.*?</think>\s*", "", text, flags=re.DOTALL)
        return text.strip()
    except Exception as e:
        print(f"[classifier] Gemini error: {e}")
        return ""


def _call_llm(prompt: str, max_tokens: int = 2000, temp: float = 0.0) -> tuple[str, str]:
    """Try Groq first, then Gemini. Returns (response_text, provider_name)."""
    groq_key = os.environ.get("GROQ_API_KEY", "")
    gem_key = os.environ.get("GEMINI_API_KEY", "") or os.environ.get("GOOGLE_GEMINI_API_KEY", "")

    if groq_key:
        resp = _call_groq(prompt, groq_key, max_tokens, temp)
        if resp:
            return resp, "Groq (Llama 3.3 70B)"

    if gem_key:
        resp = _call_gemini(prompt, gem_key, max_tokens, temp)
        if resp:
            return resp, "Gemini 2.5 Flash"

    return "", ""


# ---------------------------------------------------------------------------
# Dynamic LLM-powered classification (the core new approach)
# ---------------------------------------------------------------------------


def _has_failed_retry_loop(case: ParsedEvalCase) -> bool:
    """True only if similar scripts failed consecutively (actual retry behavior).
    Similar scripts that succeed are normal multi-step execution, not retries."""
    if not case.script_similarity_groups:
        return False
    error_step_indices = set()
    for s in case.steps:
        if s.step_type == "ScriptResponse" and s.error_type:
            error_step_indices.add(s.step_index)
    for group in case.script_similarity_groups:
        failed_in_group = sum(1 for idx in group if idx + 1 in error_step_indices)
        if failed_in_group >= 2:
            return True
    return False

def _build_failure_profiles(cases: list[ParsedEvalCase]) -> list[dict]:
    """Build structured evidence profiles for all failures."""
    profiles = []
    for case in cases:
        if case.passed:
            continue
        ev = extract_grader_evidence(case)

        # Build compact trajectory summary
        traj_lines = []
        for s in case.steps:
            if s.step_type == "UserQuery":
                traj_lines.append(f"  [{s.step_index}] UserQuery: \"{s.text[:100]}\"")
            elif s.step_type == "ScriptExecution":
                traj_lines.append(f"  [{s.step_index}] Script: {s.script_preview[:80]}")
            elif s.step_type == "ScriptResponse":
                if s.error_type:
                    traj_lines.append(f"  [{s.step_index}] ERROR ({s.error_type}): {s.result[:100]}")
                else:
                    traj_lines.append(f"  [{s.step_index}] OK: {s.result[:80]}")

        profile = {
            "batch": case.batch,
            "query_index": case.query_index,
            "query": case.query_text[:200],
            "grader_message": ev.grader_message[:400],
            "evaluator_type": ev.evaluator_type,
            "scores": f"correct={ev.total_correct}, incorrect={ev.total_incorrect}, missing={ev.total_missing}",
            "trajectory": "\n".join(traj_lines),
            "error_types": ", ".join(case.error_types[:5]) if case.error_types else "none",
        }
        profiles.append(profile)
    return profiles


def classify_all_with_llm(
    cases: list[ParsedEvalCase],
    gemini_key: str,
) -> list[ClassificationResult]:
    """Use Gemini to classify failures on two axes: trajectory + outcome."""
    failed = [c for c in cases if not c.passed]
    if not failed:
        return []

    profiles = _build_failure_profiles(cases)

    profiles_text = ""
    for i, p in enumerate(profiles):
        profiles_text += (
            f"\n--- Failure {i+1}: {p['batch']} Q{p['query_index']} ---\n"
            f"Query: \"{p['query']}\"\n"
            f"Grader: \"{p['grader_message']}\"\n"
            f"Evaluator: {p['evaluator_type']}\n"
            f"Scores: {p['scores']}\n"
            f"Error types: {p['error_types']}\n"
            f"Trajectory:\n{p['trajectory']}\n"
        )

    traj_defs = "\n".join(f"  - {name}: {desc}" for name, desc in TRAJECTORY_CATEGORIES.items())
    outc_defs = "\n".join(f"  - {name}: {desc}" for name, desc in OUTCOME_CATEGORIES.items())

    prompt = (
        "You are an eval analyst for an Excel Copilot AI agent. "
        "Below are failure profiles. Classify each on TWO independent axes.\n\n"
        "AXIS 1 -- TRAJECTORY (WHERE in the agent's process it went wrong):\n"
        f"{traj_defs}\n"
        "  - If none of the above fit, create a short descriptive name (2-4 words) that accurately "
        "describes what went wrong in the trajectory. Do NOT force-fit into a predefined category.\n\n"
        "AXIS 2 -- OUTCOME (WHAT is wrong with the final answer):\n"
        f"{outc_defs}\n"
        "  - If none of the above fit, create a short descriptive name (2-4 words) that accurately "
        "describes the outcome problem. Do NOT force-fit.\n\n"
        "RULES:\n"
        "- Assign ONE or MORE trajectory categories per failure (list ALL that genuinely apply). "
        "Assign exactly ONE outcome category.\n"
        "- ONLY assign a predefined category if the failure profile clearly matches its definition. "
        "If the match is weak or speculative, create a new descriptive name instead.\n"
        "- Use the GRADER'S explanation as primary evidence, not speculation.\n"
        "- Use trajectory signals (step count, errors, retry loops) for Axis 1.\n"
        "- Use the grader message (what went wrong with output) for Axis 2.\n"
        "- 'why' must explain the specific failure for THIS query (not generic).\n"
        "- 'fix' must be a concrete, actionable recommendation specific to this failure.\n"
        "- Do NOT use emojis.\n\n"
        "OUTPUT FORMAT (strict JSON):\n"
        "```json\n"
        "{\n"
        "  \"classifications\": [\n"
        "    {\n"
        "      \"batch\": \"...\", \"query_index\": \"...\",\n"
        "      \"trajectory_categories\": [\"...\", \"...\"], \"trajectory_confidence\": \"high\",\n"
        "      \"outcome_category\": \"...\", \"outcome_confidence\": \"high\",\n"
        "      \"why\": \"1-sentence reason\", \"fix\": \"specific fix\"\n"
        "    }\n"
        "  ]\n"
        "}\n"
        "```\n\n"
        f"FAILURES ({len(profiles)} total):\n{profiles_text}"
    )

    response, _provider = _call_llm(prompt, max_tokens=6000)
    if not response:
        return _classify_all_fallback(cases)

    try:
        json_match = re.search(r"```json\s*(.*?)\s*```", response, re.DOTALL)
        if json_match:
            result = json.loads(json_match.group(1))
        else:
            result = json.loads(response)
    except (json.JSONDecodeError, ValueError):
        try:
            start = response.index("{")
            end = response.rindex("}") + 1
            result = json.loads(response[start:end])
        except (ValueError, json.JSONDecodeError):
            return _classify_all_fallback(cases)

    classifications: list[ClassificationResult] = []
    case_map = {(c.batch, c.query_index): c for c in failed}

    llm_cls = result.get("classifications", [])
    if not llm_cls:
        print(f"[classifier] LLM returned no classifications. Keys in result: {list(result.keys())}")
        return _classify_all_fallback(cases)

    for cl in llm_cls:
        batch = cl.get("batch", "")
        qi = str(cl.get("query_index", "")).lstrip("Qq")
        case = case_map.get((batch, qi))
        if not case:
            print(f"[classifier] No match for batch='{batch}' qi='{qi}'. Available: {list(case_map.keys())[:3]}...")
            continue

        ev = extract_grader_evidence(case)
        raw_traj = cl.get("trajectory_categories") or cl.get("trajectory_category", "Unclassified")
        if isinstance(raw_traj, str):
            traj_cats = [raw_traj] if raw_traj else ["Unclassified"]
        else:
            traj_cats = raw_traj if raw_traj else ["Unclassified"]

        traj_cat = traj_cats[0]
        traj_conf = cl.get("trajectory_confidence", "medium")
        outc_cat = cl.get("outcome_category", "Unclassified")
        outc_conf = cl.get("outcome_confidence", "medium")

        evidence = []
        if ev.grader_message:
            evidence.append(f"Grader says: {ev.grader_message[:300]}")
        evidence.append(f"Trajectory: {case.step_count} steps, {case.error_count} errors")
        if ev.has_partial_scores:
            evidence.append(f"Partial scores: {ev.total_correct} correct, {ev.total_incorrect} incorrect")

        classifications.append(ClassificationResult(
            query_index=qi,
            batch=batch,
            query_text=case.query_text,
            primary_category=traj_cat,
            confidence=traj_conf,
            evidence=evidence,
            why=cl.get("why", ""),
            suggested_fix=cl.get("fix", ""),
            trajectory_category=traj_cat,
            trajectory_confidence=traj_conf,
            trajectory_categories=traj_cats,
            outcome_category=outc_cat,
            outcome_confidence=outc_conf,
        ))

    classified_keys = {(c.batch, c.query_index) for c in classifications}
    for case in failed:
        if (case.batch, case.query_index) not in classified_keys:
            ev = extract_grader_evidence(case)
            traj_cats, traj_conf = _classify_trajectory_fallback(case, ev)
            outc_cat, outc_conf = _classify_outcome_fallback(case, ev)
            evidence = []
            if ev.grader_message:
                evidence.append(f"Grader says: {ev.grader_message[:300]}")
            classifications.append(ClassificationResult(
                query_index=case.query_index,
                batch=case.batch,
                query_text=case.query_text,
                primary_category=traj_cats[0],
                confidence=traj_conf,
                evidence=evidence,
                why=ev.grader_message[:200] if ev.grader_message else "No grader explanation available",
                trajectory_category=traj_cats[0],
                trajectory_confidence=traj_conf,
                trajectory_categories=traj_cats,
                outcome_category=outc_cat,
                outcome_confidence=outc_conf,
            ))

    return classifications


# ---------------------------------------------------------------------------
# Signal-based fallback (no LLM) -- minimal, honest labeling only
# ---------------------------------------------------------------------------

def _classify_trajectory_fallback(
    case: ParsedEvalCase, ev: GraderEvidence,
) -> tuple[list[str], str]:
    """Assign trajectory categories using deterministic signals.
    Returns (list of categories, best confidence).
    A case can match multiple categories simultaneously."""

    cats: list[str] = []
    best_conf = "low"

    # Tool Misuse: detect from error type names OR error message content
    tool_misuse_errors = {"InvalidArgument", "PropertyNotFound", "RangeNotFound",
                          "TypeError", "InvalidReference", "MethodNotFound",
                          "InvalidOperation", "NotImplemented"}
    tool_misuse_msg_patterns = [
        "argument is invalid", "property", "not available",
        "cannot read properties of undefined", "doesn't exist",
        "is not a function", "incorrect format", "not permitted",
        "invalid or missing",
    ]
    misuse_hits = 0
    if case.error_types:
        misuse_hits += sum(1 for et in case.error_types if et in tool_misuse_errors)
    for s in case.steps:
        if s.step_type == "ScriptResponse" and s.error_type:
            msg = ((s.result or "") + " " + (s.console or "")).lower()
            if any(p in msg for p in tool_misuse_msg_patterns):
                misuse_hits += 1
                break
    if misuse_hits >= 1:
        cats.append("Tool Misuse")
        best_conf = "high" if misuse_hits >= 2 else "medium"

    # Retry Loops: similar scripts executed multiple times
    if case.script_similarity_groups:
        error_indices = {s.step_index for s in case.steps if s.step_type == "ScriptResponse" and s.error_type}
        for group in case.script_similarity_groups:
            if sum(1 for idx in group if idx + 1 in error_indices) >= 2:
                cats.append("Retry Loops")
                best_conf = "high"
                break

    # Cascading Errors: first error early, 2+ downstream errors follow
    if len(case.error_positions) >= 2:
        sorted_pos = sorted(case.error_positions)
        first_err = sorted_pos[0]
        downstream = [p for p in sorted_pos if p > first_err]
        if len(downstream) >= 1:
            cats.append("Cascading Errors")
            if len(downstream) >= 2:
                best_conf = "high"

    # Context Loss: agent succeeded in early steps but errors in later steps
    if case.error_count >= 1 and case.script_exec_count >= 3:
        early_ok = any(
            s.step_type == "ScriptResponse" and not s.error_type and s.step_index <= 4
            for s in case.steps
        )
        late_errors = any(
            s.step_type == "ScriptResponse" and s.error_type and s.step_index >= 6
            for s in case.steps
        )
        if early_ok and late_errors:
            msg_lower = (ev.grader_message or "").lower()
            if any(kw in msg_lower for kw in ["forgot", "lost", "ignored", "missing",
                                                "undefined", "not defined", "reference"]):
                cats.append("Context Loss")

    # Goal Drift: many steps, few errors, but still fails — AND agent tried
    # diverse operations (not just repeating the same thing)
    if case.step_count >= 14 and case.error_count <= 1 and not case.passed:
        script_ops = set()
        for s in case.steps:
            if s.step_type == "ScriptExecution" and s.script_preview:
                script_ops.add(s.script_preview[:40])
        if len(script_ops) >= 4:
            cats.append("Goal Drift")

    # Silent Quality Degradation: zero errors but failed
    if case.error_count == 0 and not case.passed:
        cats.append("Silent Quality Degradation")

    if not cats:
        return ["Unclassified"], "low"

    return cats, best_conf


def _classify_outcome_fallback(case: ParsedEvalCase, ev: GraderEvidence) -> tuple[str, str]:
    """Minimal outcome classification — only assign when keywords clearly match.
    Returns Unclassified rather than force-fitting."""
    msg = (ev.grader_message or "").lower()
    assistant_resp = (case.assistant_response or "").lower()

    if any(kw in msg for kw in ["####", "column width", "too narrow", "overflow"]):
        return "Column overflow (####)", "medium"

    if any(kw in msg for kw in ["formula", "explains how", "instruction", "refused",
                                 "didn't perform", "did not perform", "no action"]):
        return "Instructions/refusal instead of action", "medium"

    if any(kw in assistant_resp for kw in ["you can use", "try using", "here's how",
                                            "follow these steps"]):
        return "Instructions/refusal instead of action", "low"

    return "Unclassified", "low"


def _classify_all_fallback(cases: list[ParsedEvalCase]) -> list[ClassificationResult]:
    """Two-axis classification without LLM.
    Trajectory: signal-based detection into the 6 fixed categories.
    Outcome: rule-based keyword matching into the 7 fixed categories."""
    failed = [c for c in cases if not c.passed]
    if not failed:
        return []

    results = []
    for case in failed:
        ev = extract_grader_evidence(case)
        traj_cats, traj_conf = _classify_trajectory_fallback(case, ev)
        outc_cat, outc_conf = _classify_outcome_fallback(case, ev)

        evidence = []
        if ev.grader_message:
            evidence.append(f"Grader says: {ev.grader_message[:300]}")
        evidence.append(f"Trajectory: {case.step_count} steps, {case.error_count} errors")
        if ev.has_partial_scores:
            evidence.append(f"Partial scores: {ev.total_correct} correct, {ev.total_incorrect} incorrect")
        if case.script_similarity_groups:
            evidence.append(f"Repeated scripts: {len(case.script_similarity_groups)} groups")

        results.append(ClassificationResult(
            query_index=case.query_index,
            batch=case.batch,
            query_text=case.query_text,
            primary_category=traj_cats[0],
            confidence=traj_conf,
            evidence=evidence,
            why=ev.grader_message[:200] if ev.grader_message else "No grader explanation available",
            suggested_fix=None,
            trajectory_category=traj_cats[0],
            trajectory_confidence=traj_conf,
            trajectory_categories=traj_cats,
            outcome_category=outc_cat,
            outcome_confidence=outc_conf,
        ))
    return results


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def classify_all(
    cases: list[ParsedEvalCase],
    gemini_key: str = "",
) -> list[ClassificationResult]:
    """Classify all failures on two axes (trajectory + outcome).
    Uses disk cache to ensure consistent results across restarts."""
    failed = [c for c in cases if not c.passed]
    if not failed:
        return []

    # Build a stable hash from the failure data
    cache_key_data = "|".join(sorted(f"{c.batch}:{c.query_index}:{c.query_text[:50]}" for c in failed))
    cache_hash = hashlib.md5(cache_key_data.encode()).hexdigest()[:12]
    cache_dir = Path(".cache")
    cache_dir.mkdir(exist_ok=True)
    cache_file = cache_dir / f"classifications_{cache_hash}.json"

    # Try loading from cache
    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            results = []
            for d in cached:
                traj_cat = d.get("trajectory_category", "")
                traj_cats = d.get("trajectory_categories", [])
                if not traj_cats and traj_cat:
                    traj_cats = [traj_cat]
                results.append(ClassificationResult(
                    query_index=d["query_index"], batch=d["batch"],
                    query_text=d["query_text"], primary_category=d["primary_category"],
                    confidence=d["confidence"], evidence=d["evidence"],
                    why=d.get("why", ""), secondary_categories=d.get("secondary_categories", []),
                    suggested_fix=d.get("suggested_fix"),
                    trajectory_category=traj_cat,
                    trajectory_confidence=d.get("trajectory_confidence", ""),
                    trajectory_categories=traj_cats,
                    outcome_category=d.get("outcome_category", ""),
                    outcome_confidence=d.get("outcome_confidence", ""),
                ))
            if results:
                return results
        except (json.JSONDecodeError, KeyError):
            pass

    # Classify fresh
    groq_key = os.environ.get("GROQ_API_KEY", "")
    gem_key = gemini_key or os.environ.get("GEMINI_API_KEY", "") or os.environ.get("GOOGLE_GEMINI_API_KEY", "")
    if groq_key or gem_key:
        results = classify_all_with_llm(cases, gem_key)
    else:
        results = _classify_all_fallback(cases)

    # Save to cache
    try:
        cache_file.write_text(
            json.dumps([r.to_dict() for r in results], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        pass

    return results


def classify_recovery_cases(
    cases: list[ParsedEvalCase],
) -> list[ClassificationResult]:
    """Classify passed-with-errors cases on trajectory axis only.
    These queries ultimately passed but had errors during execution."""
    recovery = [c for c in cases if c.passed and c.error_count > 0]
    if not recovery:
        return []

    cache_key_data = "|".join(sorted(f"{c.batch}:{c.query_index}:recovery" for c in recovery))
    cache_hash = hashlib.md5(cache_key_data.encode()).hexdigest()[:12]
    cache_dir = Path(".cache")
    cache_dir.mkdir(exist_ok=True)
    cache_file = cache_dir / f"recovery_classifications_{cache_hash}.json"

    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            results = []
            for d in cached:
                traj_cat = d.get("trajectory_category", "")
                traj_cats = d.get("trajectory_categories", [])
                if not traj_cats and traj_cat:
                    traj_cats = [traj_cat]
                results.append(ClassificationResult(
                    query_index=d["query_index"], batch=d["batch"],
                    query_text=d["query_text"], primary_category=d["primary_category"],
                    confidence=d["confidence"], evidence=d["evidence"],
                    why=d.get("why", ""),
                    trajectory_category=traj_cat,
                    trajectory_confidence=d.get("trajectory_confidence", ""),
                    trajectory_categories=traj_cats,
                    outcome_category="Passed (recovered)",
                    outcome_confidence="high",
                ))
            if results:
                return results
        except (json.JSONDecodeError, KeyError):
            pass

    profiles_text = ""
    for case in recovery:
        ev = extract_grader_evidence(case)
        traj_lines = []
        for s in case.steps:
            if s.step_type == "UserQuery":
                traj_lines.append(f"  [{s.step_index}] UserQuery: \"{s.text[:100]}\"")
            elif s.step_type == "ScriptExecution":
                traj_lines.append(f"  [{s.step_index}] Script: {s.script_preview[:80]}")
            elif s.step_type == "ScriptResponse":
                if s.error_type:
                    traj_lines.append(f"  [{s.step_index}] ERROR ({s.error_type}): {s.result[:100]}")
                else:
                    traj_lines.append(f"  [{s.step_index}] OK: {s.result[:80]}")
        profiles_text += (
            f"\n--- Recovery {case.batch} Q{case.query_index} ---\n"
            f"Query: \"{case.query_text[:200]}\"\n"
            f"Result: PASSED (correct answer despite errors)\n"
            f"Error types: {', '.join(case.error_types[:5]) if case.error_types else 'none'}\n"
            f"Trajectory:\n" + "\n".join(traj_lines) + "\n"
        )

    traj_defs = "\n".join(f"  - {name}: {desc}" for name, desc in TRAJECTORY_CATEGORIES.items())

    prompt = (
        "You are an eval analyst for an Excel Copilot AI agent. "
        "Below are PASSED queries that encountered errors during execution but still "
        "produced the correct answer. For each, analyze the trajectory and explain:\n"
        "1. What specific error occurred and at which step\n"
        "2. How the agent recovered (what it did differently after the error)\n"
        "3. Assign trajectory categories if any fit — but only if they genuinely apply. "
        "If no predefined category fits, use a short descriptive name.\n\n"
        "TRAJECTORY CATEGORIES:\n"
        f"{traj_defs}\n\n"
        "RULES:\n"
        "- 'why' must be SPECIFIC to this query — mention the actual error, the step number, "
        "and how the agent adapted. NOT generic like 'encountered errors and recovered'.\n"
        "- Do NOT use emojis.\n\n"
        "OUTPUT FORMAT (strict JSON):\n"
        "```json\n"
        "{\n"
        "  \"classifications\": [\n"
        "    {\n"
        "      \"batch\": \"...\", \"query_index\": \"...\",\n"
        "      \"trajectory_categories\": [\"...\"], \"trajectory_confidence\": \"high\",\n"
        "      \"why\": \"specific explanation of error and recovery\"\n"
        "    }\n"
        "  ]\n"
        "}\n"
        "```\n\n"
        f"RECOVERY CASES ({len(recovery)} total):\n{profiles_text}"
    )

    response, _provider = _call_llm(prompt, max_tokens=4000)
    results: list[ClassificationResult] = []

    if response:
        try:
            json_match = re.search(r"```json\s*(.*?)\s*```", response, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group(1))
            else:
                result = json.loads(response)
        except (json.JSONDecodeError, ValueError):
            try:
                start = response.index("{")
                end = response.rindex("}") + 1
                result = json.loads(response[start:end])
            except (ValueError, json.JSONDecodeError):
                result = {"classifications": []}

        case_map = {(c.batch, c.query_index): c for c in recovery}
        for cl in result.get("classifications", []):
            batch = cl.get("batch", "")
            qi = str(cl.get("query_index", "")).lstrip("Qq")
            case = case_map.get((batch, qi))
            if not case:
                continue
            raw_traj = cl.get("trajectory_categories") or cl.get("trajectory_category", "Unclassified")
            if isinstance(raw_traj, str):
                traj_cats = [raw_traj] if raw_traj else ["Unclassified"]
            else:
                traj_cats = raw_traj if raw_traj else ["Unclassified"]
            traj_cat = traj_cats[0]

            results.append(ClassificationResult(
                query_index=qi, batch=batch, query_text=case.query_text,
                primary_category=traj_cat,
                confidence=cl.get("trajectory_confidence", "medium"),
                evidence=[f"Trajectory: {case.step_count} steps, {case.error_count} errors (recovered)"],
                why=cl.get("why", ""),
                trajectory_category=traj_cat,
                trajectory_confidence=cl.get("trajectory_confidence", "medium"),
                trajectory_categories=traj_cats,
                outcome_category="Passed (recovered)",
                outcome_confidence="high",
            ))

    # Fallback for unclassified
    classified_keys = {(c.batch, c.query_index) for c in results}
    for case in recovery:
        if (case.batch, case.query_index) not in classified_keys:
            traj_cat, traj_conf = _classify_trajectory_fallback(case, extract_grader_evidence(case))
            results.append(ClassificationResult(
                query_index=case.query_index, batch=case.batch,
                query_text=case.query_text, primary_category=traj_cat,
                confidence=traj_conf, evidence=[],
                why="Fallback classification",
                trajectory_category=traj_cat, trajectory_confidence=traj_conf,
                trajectory_categories=[traj_cat],
                outcome_category="Passed (recovered)", outcome_confidence="high",
            ))

    try:
        cache_file.write_text(
            json.dumps([r.to_dict() for r in results], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        pass

    return results
# ---------------------------------------------------------------------------

def generate_insights(classifications: list[ClassificationResult]) -> dict:
    if not classifications:
        return {"total_failures": 0, "message": "No failures to analyze"}

    primary_counts = Counter()
    for c in classifications:
        cats = c.trajectory_categories if c.trajectory_categories else [c.primary_category]
        for cat in cats:
            primary_counts[cat] += 1
    conf_counts = Counter(c.confidence for c in classifications)
    outcome_counts = Counter(
        c.outcome_category for c in classifications if c.outcome_category
    )

    batch_counts: dict[str, Counter] = {}
    for c in classifications:
        cats = c.trajectory_categories if c.trajectory_categories else [c.primary_category]
        batch_counts.setdefault(c.batch, Counter())
        for cat in cats:
            batch_counts[c.batch][cat] += 1

    fix_priority: list[dict] = []
    for cat_name, count in primary_counts.most_common():
        fixes = [c.suggested_fix for c in classifications
                 if cat_name in (c.trajectory_categories or [c.primary_category]) and c.suggested_fix]
        fix = fixes[0] if fixes else "Review failures in this category"
        whys = [c.why for c in classifications
                if cat_name in (c.trajectory_categories or [c.primary_category]) and c.why]
        why = whys[0] if whys else ""
        fix_priority.append({
            "category": cat_name,
            "fix": fix,
            "why": why,
            "failure_count": count,
        })

    return {
        "total_failures": len(classifications),
        "primary_category_distribution": dict(primary_counts.most_common()),
        "outcome_category_distribution": dict(outcome_counts.most_common()),
        "confidence_distribution": dict(conf_counts),
        "per_batch_breakdown": {
            batch: dict(counts.most_common())
            for batch, counts in batch_counts.items()
        },
        "prioritized_fixes": fix_priority,
    }


def _tag_value(case: ParsedEvalCase, prefix: str) -> str | None:
    for t in case.tags:
        if t.startswith(prefix + ":"):
            return t.split(":", 1)[-1]
    return None


def generate_full_insights(
    all_cases: list[ParsedEvalCase],
    classifications: list[ClassificationResult],
) -> dict:
    """Analyze both passed and failed cases."""
    passed = [c for c in all_cases if c.passed]
    failed = [c for c in all_cases if not c.passed]
    total = len(all_cases)

    # Pass/fail by specificity
    specificity_stats: dict[str, dict] = {}
    spec_order = ["Very Well-Specified", "Well-Specified", "Reasonably Specified", "Ambiguous", "Very Ambiguous"]
    for case in all_cases:
        spec = _tag_value(case, "taxonomy.query.specificity")
        if not spec:
            continue
        specificity_stats.setdefault(spec, {"pass": 0, "fail": 0})
        specificity_stats[spec]["pass" if case.passed else "fail"] += 1

    specificity_axis = []
    for spec in spec_order:
        if spec in specificity_stats:
            s = specificity_stats[spec]
            t = s["pass"] + s["fail"]
            specificity_axis.append({
                "level": spec, "total": t, "passed": s["pass"],
                "failed": s["fail"], "fail_rate": round(s["fail"] / t * 100),
            })

    # Pass/fail by complexity
    complexity_stats: dict[str, dict] = {}
    complexity_order = ["L1 Actions", "L2 Feature Usage", "L3 Multi-step Tasks", "L4 Workflows"]
    for case in all_cases:
        comp = _tag_value(case, "taxonomy.query.intent.complexity")
        if not comp:
            continue
        complexity_stats.setdefault(comp, {"pass": 0, "fail": 0})
        complexity_stats[comp]["pass" if case.passed else "fail"] += 1

    complexity_axis = []
    for comp in complexity_order:
        if comp in complexity_stats:
            s = complexity_stats[comp]
            t = s["pass"] + s["fail"]
            complexity_axis.append({
                "level": comp, "total": t, "passed": s["pass"],
                "failed": s["fail"], "fail_rate": round(s["fail"] / t * 100),
            })

    # Pass/fail by intent
    intent_stats: dict[str, dict] = {}
    for case in all_cases:
        intent = _tag_value(case, "taxonomy.query.intent.primary")
        if not intent:
            continue
        intent_stats.setdefault(intent, {"pass": 0, "fail": 0})
        intent_stats[intent]["pass" if case.passed else "fail"] += 1

    intent_breakdown = []
    for intent, s in sorted(intent_stats.items(), key=lambda x: x[1]["fail"] / max(x[1]["pass"] + x[1]["fail"], 1), reverse=True):
        t = s["pass"] + s["fail"]
        intent_breakdown.append({
            "intent": intent, "total": t, "passed": s["pass"],
            "failed": s["fail"], "fail_rate": round(s["fail"] / t * 100),
        })

    # Works well / poorly
    works_well: list[str] = []
    works_poorly: list[str] = []

    batch_map: dict[str, list] = {}
    for c in all_cases:
        batch_map.setdefault(c.batch, []).append(c)

    for batch_name in sorted(batch_map):
        bc = batch_map[batch_name]
        bf = sum(1 for c in bc if not c.passed)
        if bf == 0 and len(bc) >= 3:
            works_well.append(f"{batch_name} -- 100% pass rate ({len(bc)} queries).")

    qa_markers = ["how many", "average of", "highest", "lowest", "top-", "top ", "which "]
    qa_cases = [c for c in all_cases if any(m in c.query_text.lower() for m in qa_markers)]
    qa_pass = sum(1 for c in qa_cases if c.passed)
    if qa_cases and len(qa_cases) >= 3:
        rate = round(qa_pass / len(qa_cases) * 100, 1)
        if rate >= 85:
            works_well.append(f"Definite-answer Q&A -- ~{rate}% pass rate ({qa_pass}/{len(qa_cases)}).")

    open_markers = ["what's interesting", "improve", "dashboard", "insight", "recommend", "suggest"]
    open_failed = [c for c in failed if any(m in c.query_text.lower() for m in open_markers)]
    open_all = [c for c in all_cases if any(m in c.query_text.lower() for m in open_markers)]
    if open_failed:
        rate = round(len(open_failed) / max(len(open_all), 1) * 100)
        works_poorly.append(f"Open-ended prompts -- {len(open_failed)}/{len(open_all)} fail ({rate}%).")

    error_fails = [c for c in failed if c.error_count >= 1]
    if error_fails and len(error_fails) >= 2:
        works_poorly.append(f"Error recovery -- {len(error_fails)} failures hit script errors.")

    claim_fail = sum(1 for c in failed if c.has_success_claim)
    if claim_fail >= 3:
        works_poorly.append(f"Self-verification -- {claim_fail}/{len(failed)} failures claim success.")

    # Two-axis model
    two_axis_model = []
    if specificity_axis:
        ordered = [s for s in specificity_axis if s["total"] >= 2]
        if len(ordered) >= 2:
            parts = " > ".join(f"{s['level'].split('-')[0].strip()} ({s['fail_rate']}%)" for s in ordered)
            two_axis_model.append(f"Reasoning axis: {parts}.")

    # Success patterns
    success_patterns: list[dict] = []
    sa_cases = [
        c for c in all_cases
        if len(c.query_text.split()) <= 18
        and _tag_value(c, "taxonomy.query.specificity") in ("Very Well-Specified", "Well-Specified")
    ]
    sa_pass = sum(1 for c in sa_cases if c.passed)
    if sa_cases and sa_pass / max(len(sa_cases), 1) >= 0.85:
        success_patterns.append({
            "name": "Short well-specified prompts",
            "pass_rate": round(sa_pass / len(sa_cases) * 100),
            "count": len(sa_cases),
            "why": "No intent ambiguity, no multi-step state needed.",
        })

    # Deeper insights (LLM + rule-based combined)
    deeper_insights: list = _generate_llm_insights(passed, failed, classifications, _tag_value)

    # Always add rule-based insights too
    batch_fail_counts = Counter(c.batch for c in failed)
    if batch_fail_counts:
        top_batch, top_count = batch_fail_counts.most_common(1)[0]
        if top_count / max(len(failed), 1) >= 0.7:
            deeper_insights.append({"finding": f"Failures cluster heavily in {top_batch} ({top_count}/{len(failed)}).", "recommendation": f"Investigate {top_batch} — the batch may have systematically harder queries or a data issue."})
    if claim_fail == len(failed) and len(failed) >= 5:
        deeper_insights.append({"finding": f"All {len(failed)} failures claim success — agent never self-detects.", "recommendation": "The agent lacks self-evaluation. Consider adding confidence calibration or output verification steps."})

    # Delta table
    delta_table: list[dict] = []
    spec_pass = Counter(_tag_value(c, "taxonomy.query.specificity") for c in passed)
    spec_fail = Counter(_tag_value(c, "taxonomy.query.specificity") for c in failed)
    well_spec_pass = spec_pass.get("Very Well-Specified", 0) + spec_pass.get("Well-Specified", 0)
    ambig_fail = spec_fail.get("Ambiguous", 0) + spec_fail.get("Very Ambiguous", 0)
    delta_table.append({
        "axis": "Specificity",
        "success_side": f"Well-specified dominate ({well_spec_pass}/{len(passed)} successes)",
        "failure_side": f"Ambiguous concentrate in failures ({ambig_fail}/{len(failed)} failures)",
    })

    avg_steps_pass = sum(c.step_count for c in passed) / max(len(passed), 1)
    avg_steps_fail = sum(c.step_count for c in failed) / max(len(failed), 1)
    delta_table.append({
        "axis": "Trajectory length",
        "success_side": f"Avg {avg_steps_pass:.0f} steps",
        "failure_side": f"Avg {avg_steps_fail:.0f} steps",
    })

    return {
        "total_queries": total,
        "total_passed": len(passed),
        "total_failed": len(failed),
        "pass_rate": round(len(passed) / max(total, 1) * 100, 1),
        "specificity_axis": specificity_axis,
        "complexity_axis": complexity_axis,
        "intent_breakdown": intent_breakdown,
        "works_well": works_well,
        "works_poorly": works_poorly,
        "two_axis_model": two_axis_model,
        "success_patterns": success_patterns,
        "deeper_insights": deeper_insights,
        "delta_table": delta_table,
    }


# ---------------------------------------------------------------------------
# Backward-compatible wrapper
# ---------------------------------------------------------------------------

def extract_assertion_details(case: ParsedEvalCase) -> dict:
    ev = extract_grader_evidence(case)
    return {
        "outcome": ev.outcome,
        "evaluator_type": ev.evaluator_type,
        "assertions": ev.assertions,
        "grader_message": ev.grader_message,
        "expected": ev.expected,
        "received": ev.received,
        "scores": {
            "correct": ev.total_correct,
            "incorrect": ev.total_incorrect,
            "missing": ev.total_missing,
        } if ev.assertion_scores else None,
        "summary": ev.grader_message[:200] if ev.grader_message else "",
    }


# ---------------------------------------------------------------------------
# LLM-powered deeper insights
# ---------------------------------------------------------------------------

def _generate_llm_insights(
    passed: list[ParsedEvalCase],
    failed: list[ParsedEvalCase],
    classifications: list[ClassificationResult],
    tag_fn,
) -> list[str]:
    """Use LLM to discover non-obvious patterns and actionable recommendations."""
    if not passed and not failed:
        return []

    outcome_dist = Counter(cr.outcome_category for cr in classifications)
    traj_dist = Counter(cr.trajectory_category for cr in classifications)
    intent_pass: dict[str, dict] = {}
    spec_pass: dict[str, dict] = {}
    for c in passed + failed:
        intent = tag_fn(c, "taxonomy.query.intent.primary") or "Unknown"
        spec = tag_fn(c, "taxonomy.query.specificity") or "Unknown"
        intent_pass.setdefault(intent, {"p": 0, "f": 0})
        spec_pass.setdefault(spec, {"p": 0, "f": 0})
        if c.passed:
            intent_pass[intent]["p"] += 1
            spec_pass[spec]["p"] += 1
        else:
            intent_pass[intent]["f"] += 1
            spec_pass[spec]["f"] += 1

    # Cross-axis: intent × outcome correlation
    intent_outcome: dict[str, Counter] = {}
    intent_spec_fail: dict[str, Counter] = {}
    case_lookup = {(c.batch, c.query_index): c for c in passed + failed}
    for cr in classifications:
        c = case_lookup.get((cr.batch, cr.query_index))
        if not c:
            continue
        intent = tag_fn(c, "taxonomy.query.intent.primary") or "Unknown"
        spec = tag_fn(c, "taxonomy.query.specificity") or "Unknown"
        intent_outcome.setdefault(intent, Counter())[cr.outcome_category] += 1
        intent_spec_fail.setdefault(intent, Counter())[spec] += 1

    cross_axis_summary = []
    for intent, oc in intent_outcome.items():
        top = oc.most_common(1)[0]
        specs = intent_spec_fail.get(intent, Counter())
        top_spec = specs.most_common(1)[0] if specs else ("", 0)
        cross_axis_summary.append(f"{intent}: top outcome={top[0]} ({top[1]}), top failing specificity={top_spec[0]} ({top_spec[1]})")

    failed_queries = []
    for cr in classifications[:25]:
        c = case_lookup.get((cr.batch, cr.query_index))
        spec = tag_fn(c, "taxonomy.query.specificity") if c else "Unknown"
        cplx = tag_fn(c, "taxonomy.query.intent.complexity") if c else "Unknown"
        steps = c.step_count if c else 0
        errors = c.error_count if c else 0
        claimed_success = c.has_success_claim if c else False
        failed_queries.append(
            f"- Q{cr.query_index}: \"{cr.query_text[:120]}\" | outcome: {cr.outcome_category} | "
            f"trajectory: {cr.trajectory_category} | specificity: {spec} | complexity: {cplx} | "
            f"steps: {steps} | errors: {errors} | claimed_success: {claimed_success}"
        )

    claim_success_count = sum(1 for c in failed if c.has_success_claim)
    avg_steps_pass = sum(c.step_count for c in passed) / max(len(passed), 1)
    avg_steps_fail = sum(c.step_count for c in failed) / max(len(failed), 1)
    avg_errors_pass = sum(c.error_count for c in passed) / max(len(passed), 1)
    avg_errors_fail = sum(c.error_count for c in failed) / max(len(failed), 1)

    error_types_fail = Counter()
    error_types_pass = Counter()
    for c in failed:
        for et in c.error_types:
            error_types_fail[et] += 1
    for c in passed:
        for et in c.error_types:
            error_types_pass[et] += 1

    prompt = f"""You are a senior eval analyst presenting to PMs who run Excel Copilot evaluations. Your job is to find CROSS-CUTTING patterns and give ACTIONABLE recommendations they can act on.

Below is the complete data from this eval run. The PM can already see: pass/fail rates by intent, specificity, outcome category cards, and trajectory categories. DO NOT repeat those — they are already on the dashboard.

Instead, analyze the INTERSECTIONS and give insights + recommendations that are not visible from any single table.

== SUMMARY ==
Total: {len(passed) + len(failed)} queries | {len(passed)} passed | {len(failed)} failed | {round(len(failed) / max(len(passed) + len(failed), 1) * 100)}% fail rate
False success claims: {claim_success_count}/{len(failed)} failures ({round(claim_success_count / max(len(failed), 1) * 100)}%) falsely claim success
Avg steps: {avg_steps_pass:.1f} (pass) vs {avg_steps_fail:.1f} (fail)
Avg errors: {avg_errors_pass:.1f} (pass) vs {avg_errors_fail:.1f} (fail)

== OUTCOME CATEGORIES (what went wrong) ==
{dict(outcome_dist.most_common())}

== TRAJECTORY CATEGORIES (where in the process it went wrong) ==
{dict(traj_dist.most_common())}

== INTENT × OUTCOME CROSS-AXIS ==
{chr(10).join(cross_axis_summary)}

== ERROR TYPES IN FAILURES vs PASSES ==
Failures: {dict(error_types_fail.most_common(5))}
Passes: {dict(error_types_pass.most_common(5))}

== INTENT PASS/FAIL ==
{json.dumps(intent_pass, indent=0)}

== SPECIFICITY PASS/FAIL ==
{json.dumps(spec_pass, indent=0)}

== ALL FAILED QUERIES (with full context) ==
{chr(10).join(failed_queries)}

== YOUR TASK ==
Return a JSON array of objects. Each object has:
- "finding": A specific, data-backed observation about a CROSS-CUTTING pattern (e.g., intersection of intent + specificity + outcome, or error recovery patterns, or false success correlation). Use exact numbers.
- "recommendation": A concrete, actionable recommendation for PMs — what to change in the eval dataset, agent behavior, or grading criteria. Be specific.

Return 4-6 items. Focus on:
1. Which COMBINATIONS of intent + specificity are most dangerous (not just individual axes)
2. Whether the agent's self-detection (success claims) correlates with certain failure types
3. Error recovery patterns — do certain error types lead to recovery vs cascading failure
4. Gaps in the eval coverage — what's undertested or overtested
5. Grading blind spots — where the grader may be too lenient or strict

Return ONLY valid JSON: [{{"finding":"...","recommendation":"..."}}, ...]"""

    resp, _ = _call_llm(prompt, max_tokens=1500)
    if not resp:
        return []

    try:
        cleaned = resp.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```\w*\n?", "", cleaned)
            cleaned = re.sub(r"\n?```$", "", cleaned)
        items = json.loads(cleaned)
        if isinstance(items, list):
            results = []
            for item in items[:6]:
                if isinstance(item, dict) and "finding" in item and "recommendation" in item:
                    results.append(item)
                elif isinstance(item, str):
                    results.append({"finding": item, "recommendation": ""})
            return results
    except (json.JSONDecodeError, ValueError):
        pass
    return []


# ---------------------------------------------------------------------------
# Narrative generation
# ---------------------------------------------------------------------------

# Cache for category-level fixes (persists within session)
_category_fix_cache: dict[str, str] = {}


def generate_category_fix(
    category_name: str,
    category_cases: list[ClassificationResult],
    all_cases: list,
) -> str:
    """Generate a specific, actionable fix for a trajectory category using LLM."""
    cache_key = f"{category_name}:{len(category_cases)}"
    if cache_key in _category_fix_cache:
        return _category_fix_cache[cache_key]

    # Also check disk cache
    cache_dir = Path(".cache")
    cache_dir.mkdir(exist_ok=True)
    fix_cache_file = cache_dir / "category_fixes.json"
    disk_cache: dict = {}
    if fix_cache_file.exists():
        try:
            disk_cache = json.loads(fix_cache_file.read_text(encoding="utf-8"))
            if cache_key in disk_cache:
                _category_fix_cache[cache_key] = disk_cache[cache_key]
                return disk_cache[cache_key]
        except (json.JSONDecodeError, KeyError):
            pass

    query_summaries = []
    for cr in category_cases[:10]:
        line = f"- Q{cr.query_index}: \"{cr.query_text[:120]}\""
        if cr.why:
            line += f" | Why: {cr.why[:150]}"
        ev_items = [e for e in cr.evidence if e.startswith("Grader says:")]
        if ev_items:
            line += f" | Grader: {ev_items[0][13:150]}"
        query_summaries.append(line)

    cat_desc = TRAJECTORY_CATEGORIES.get(category_name, "")

    prompt = f"""You are an eval analyst for Excel Copilot. Given a trajectory failure category and the specific queries that fell into it, provide ONE concrete, actionable fix recommendation.

CATEGORY: {category_name}
DEFINITION: {cat_desc}
NUMBER OF FAILURES: {len(category_cases)}

FAILED QUERIES IN THIS CATEGORY:
{chr(10).join(query_summaries)}

RULES:
- Give ONE specific, actionable recommendation (2-3 sentences max)
- Be precise: name specific APIs, methods, patterns, or behaviors to change
- Address the ROOT CAUSE pattern across all these queries, not individual ones
- Example good fix: "The agent should use Excel.Range.getUsedRange() to detect data bounds before applying formulas, and validate that referenced columns exist via Range.getColumn() before computing correlations."
- Example bad fix: "Fix the API calls" or "Use the correct method"
- Return ONLY the fix text, no JSON, no labels"""

    resp, _ = _call_llm(prompt, max_tokens=400)
    fix = resp.strip() if resp else ""

    if fix:
        _category_fix_cache[cache_key] = fix
        disk_cache[cache_key] = fix
        try:
            fix_cache_file.write_text(json.dumps(disk_cache, indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    return fix


# ---------------------------------------------------------------------------
# Executive summary (cross-tab LLM synthesis)
# ---------------------------------------------------------------------------

_exec_summary_cache: dict[str, str] = {}


def generate_executive_summary(
    all_cases: list[ParsedEvalCase],
    classifications: list[ClassificationResult],
    recovery_classifications: list[ClassificationResult],
) -> str:
    """LLM-generated overall takeaway synthesizing failure, success, and recovery data."""
    failed = [c for c in all_cases if not c.passed]
    passed = [c for c in all_cases if c.passed]
    total = len(all_cases)

    cache_key = hashlib.md5(
        f"{total}_{len(failed)}_{len(classifications)}".encode()
    ).hexdigest()[:12]
    if cache_key in _exec_summary_cache:
        return _exec_summary_cache[cache_key]

    disk_path = Path(".cache") / f"exec_summary_{cache_key}.json"
    if disk_path.exists():
        try:
            cached = json.loads(disk_path.read_text(encoding="utf-8"))
            if cached.get("summary"):
                _exec_summary_cache[cache_key] = cached["summary"]
                return cached["summary"]
        except Exception:
            pass

    if total == 0:
        return "No queries loaded."

    pass_rate = len(passed) / total * 100

    # Failure analysis data
    traj_dist: dict[str, int] = Counter()
    outc_dist: dict[str, int] = Counter()
    for cr in classifications:
        for tc in cr.trajectory_categories:
            traj_dist[tc] += 1
        if cr.outcome_category:
            outc_dist[cr.outcome_category] += 1

    traj_lines = "\n".join(f"  - {cat}: {cnt}" for cat, cnt in traj_dist.most_common(8))
    outc_lines = "\n".join(f"  - {cat}: {cnt}" for cat, cnt in outc_dist.most_common(8))

    # Success analysis data
    recovery_cases = [c for c in passed if c.error_count > 0]
    golden = [c for c in passed if c.error_count == 0 and c.step_count < 8]
    recovery_rate = len(recovery_cases) / max(len(passed), 1) * 100

    # Intent breakdown
    intent_stats: dict[str, dict] = {}
    for c in all_cases:
        tag = _tag_value(c, "taxonomy.query.intent.primary")
        if tag:
            intent_stats.setdefault(tag, {"p": 0, "f": 0})
            intent_stats[tag]["p" if c.passed else "f"] += 1

    intent_lines = []
    for intent, s in sorted(intent_stats.items(), key=lambda x: -(x[1]["p"] + x[1]["f"])):
        t = s["p"] + s["f"]
        r = s["p"] / t * 100
        intent_lines.append(f"  - {intent}: {r:.0f}% pass ({s['p']}/{t})")

    # Specificity breakdown
    spec_stats: dict[str, dict] = {}
    for c in all_cases:
        tag = _tag_value(c, "taxonomy.query.specificity")
        if tag:
            spec_stats.setdefault(tag, {"p": 0, "f": 0})
            spec_stats[tag]["p" if c.passed else "f"] += 1

    spec_lines = []
    for spec, s in spec_stats.items():
        t = s["p"] + s["f"]
        r = s["p"] / t * 100
        spec_lines.append(f"  - {spec}: {r:.0f}% pass ({s['p']}/{t})")

    # Error types
    err_counter: Counter = Counter()
    for c in all_cases:
        for et in c.error_types:
            err_counter[et] += 1
    err_lines = "\n".join(f"  - {et}: {cnt}" for et, cnt in err_counter.most_common(6))

    # Recovery classifications
    rec_cats: Counter = Counter()
    for rc in recovery_classifications:
        for tc in rc.trajectory_categories:
            rec_cats[tc] += 1
    rec_lines = "\n".join(f"  - {cat}: {cnt}" for cat, cnt in rec_cats.most_common(5))

    # Avg metrics
    avg_steps_pass = sum(c.step_count for c in passed) / max(len(passed), 1)
    avg_steps_fail = sum(c.step_count for c in failed) / max(len(failed), 1)
    avg_errors_fail = sum(c.error_count for c in failed) / max(len(failed), 1)
    avg_time_pass = sum(c.execution_time_sec for c in passed) / max(len(passed), 1)
    avg_time_fail = sum(c.execution_time_sec for c in failed) / max(len(failed), 1)
    claim_but_fail = sum(1 for c in failed if c.has_success_claim)

    prompt = f"""You are analyzing the complete results of an Excel Copilot AI agent evaluation benchmark. Synthesize ALL the data below into a concise executive summary that a PM can read instead of going through individual analysis tabs.

OVERALL:
- {total} queries, {len(passed)} passed, {len(failed)} failed ({pass_rate:.1f}% pass rate)
- {len(golden)} golden trajectories (zero errors, <8 steps)
- {len(recovery_cases)} recovery cases ({recovery_rate:.1f}% of passes had errors but still succeeded)
- {claim_but_fail} of {len(failed)} failures had the agent claiming success (false confidence)

FAILURE TRAJECTORY PATTERNS (how the agent failed):
{traj_lines or '  None classified'}

FAILURE OUTCOME PATTERNS (what went wrong with the output):
{outc_lines or '  None classified'}

PERFORMANCE BY INTENT:
{chr(10).join(intent_lines[:8]) or '  No data'}

PERFORMANCE BY SPECIFICITY:
{chr(10).join(spec_lines) or '  No data'}

ERROR TYPE DISTRIBUTION:
{err_lines or '  No errors'}

RECOVERY PATTERNS (cases that hit errors but still passed):
{rec_lines or '  None classified'}

EXECUTION METRICS:
- Avg steps: {avg_steps_pass:.1f} (pass) vs {avg_steps_fail:.1f} (fail)
- Avg errors: 0 (pass, clean) vs {avg_errors_fail:.1f} (fail)
- Avg time: {avg_time_pass:.1f}s (pass) vs {avg_time_fail:.1f}s (fail)

YOUR TASK:
Write a 4-6 paragraph executive summary covering:
1. Overall health assessment of the agent on this benchmark
2. The biggest failure patterns and what they mean practically (not just category names — what is the agent actually doing wrong?)
3. Which query types (intent, specificity) are strongest vs weakest
4. Recovery capability — how well does the agent self-correct?
5. Top 2-3 specific, actionable recommendations for the team

Format as HTML paragraphs using <p> tags. Use <strong> for key numbers and terms. Be specific — cite numbers, categories, intent types. Do not be generic or vague. This summary should give a PM the full picture without needing to click into any other tab."""

    resp, provider = _call_llm(prompt, max_tokens=4000, temp=0.0)
    summary = resp.strip() if resp else ""

    if not summary:
        summary = (
            f"Overall: {total} queries evaluated, {pass_rate:.1f}% pass rate. "
            f"{len(failed)} failures across {len(traj_dist)} trajectory patterns. "
            f"{len(recovery_cases)} cases recovered from errors. "
            f"{len(golden)} golden trajectories. "
            "Enable GROQ_API_KEY or GEMINI_API_KEY for detailed LLM analysis."
        )

    _exec_summary_cache[cache_key] = summary
    try:
        os.makedirs(Path(".cache"), exist_ok=True)
        disk_path.write_text(
            json.dumps({"summary": summary}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        pass

    return summary


# ---------------------------------------------------------------------------
# Trajectory efficiency analysis — find wasted / skippable steps
# ---------------------------------------------------------------------------

_traj_efficiency_cache: dict[str, dict] = {}


def analyze_trajectory_efficiency(
    all_cases: list[ParsedEvalCase],
) -> dict:
    """Comprehensive cost & efficiency analysis across all trajectories.

    Covers: wasted steps, script bloat, thoughts overhead, early failure
    prediction, round-trip reduction, console bloat, and failure trajectory waste.
    """
    passed = [c for c in all_cases if c.passed]
    failed = [c for c in all_cases if not c.passed]
    recovery = [c for c in passed if c.error_count > 0]
    golden = [c for c in passed if c.error_count == 0 and c.step_count < 8]
    clean_pass = [c for c in passed if c.error_count == 0]

    cache_key = hashlib.md5(
        f"traj_eff_v2_{len(all_cases)}_{len(recovery)}_{len(failed)}".encode()
    ).hexdigest()[:12]

    if cache_key in _traj_efficiency_cache:
        return _traj_efficiency_cache[cache_key]

    disk_path = Path(".cache") / f"traj_efficiency_{cache_key}.json"
    if disk_path.exists():
        try:
            cached = json.loads(disk_path.read_text(encoding="utf-8"))
            _traj_efficiency_cache[cache_key] = cached
            return cached
        except Exception:
            pass

    # ── 1. WASTED STEPS (success recovery cases) ──────────────────────────
    wasteful_cases = []
    for c in recovery:
        error_rate = c.error_count / max(c.script_exec_count, 1)
        wasted_step_count = c.error_count * 2

        has_thrashing = False
        if c.script_similarity_groups:
            error_indices = {s.step_index for s in c.steps if s.step_type == "ScriptResponse" and s.error_type}
            for group in c.script_similarity_groups:
                if sum(1 for idx in group if idx + 1 in error_indices) >= 2:
                    has_thrashing = True
                    break

        err_types = [s.error_type for s in c.steps if s.step_type == "ScriptResponse" and s.error_type]
        repeated_errors = {et: cnt for et, cnt in Counter(err_types).items() if cnt >= 2}

        wasteful_cases.append({
            "batch": c.batch, "query_index": c.query_index,
            "query_text": c.query_text[:150],
            "step_count": c.step_count, "script_exec_count": c.script_exec_count,
            "error_count": c.error_count, "error_rate": round(error_rate * 100),
            "wasted_step_count": wasted_step_count,
            "savings_pct": round(wasted_step_count / max(c.step_count, 1) * 100),
            "has_thrashing": has_thrashing, "repeated_errors": repeated_errors,
            "error_types": err_types,
        })
    wasteful_cases.sort(key=lambda x: -x["wasted_step_count"])

    total_wasted_success = sum(w["wasted_step_count"] for w in wasteful_cases)
    all_wasted_errors: Counter = Counter()
    for w in wasteful_cases:
        for et in w["error_types"]:
            all_wasted_errors[et] += 1

    # ── 2. FAILURE TRAJECTORY WASTE ───────────────────────────────────────
    failure_waste = []
    for c in failed:
        err_types = [s.error_type for s in c.steps if s.step_type == "ScriptResponse" and s.error_type]
        failure_waste.append({
            "batch": c.batch, "query_index": c.query_index,
            "query_text": c.query_text[:150],
            "step_count": c.step_count, "script_exec_count": c.script_exec_count,
            "error_count": c.error_count,
            "time_sec": round(c.execution_time_sec, 1),
            "error_types": err_types,
        })
    failure_waste.sort(key=lambda x: -x["step_count"])
    total_failed_steps = sum(c.step_count for c in failed)
    total_failed_scripts = sum(c.script_exec_count for c in failed)
    total_failed_time = sum(c.execution_time_sec for c in failed)

    # ── 3. SCRIPT BLOAT ──────────────────────────────────────────────────
    def _script_sizes(cases):
        sizes = []
        for c in cases:
            for s in c.steps:
                if s.step_type == "ScriptExecution" and s.script_len > 0:
                    sizes.append(s.script_len)
        return sizes

    golden_scripts = _script_sizes(golden)
    recovery_scripts = _script_sizes(recovery)
    failed_scripts = _script_sizes(failed)
    clean_scripts = _script_sizes(clean_pass)

    def _stats(sizes):
        if not sizes:
            return {"count": 0, "avg": 0, "median": 0, "max": 0, "total_chars": 0}
        s = sorted(sizes)
        return {
            "count": len(s),
            "avg": round(sum(s) / len(s)),
            "median": s[len(s) // 2],
            "max": max(s),
            "total_chars": sum(s),
        }

    script_bloat = {
        "golden": _stats(golden_scripts),
        "clean_pass": _stats(clean_scripts),
        "recovery": _stats(recovery_scripts),
        "failed": _stats(failed_scripts),
        "all": _stats(golden_scripts + recovery_scripts + failed_scripts + clean_scripts),
    }

    # Oversized scripts (>3000 chars)
    oversized = []
    for c in all_cases:
        for s in c.steps:
            if s.step_type == "ScriptExecution" and s.script_len > 3000:
                oversized.append({
                    "batch": c.batch, "query_index": c.query_index,
                    "step_index": s.step_index, "script_len": s.script_len,
                    "passed": c.passed,
                    "preview": s.script_preview[:100],
                })
    oversized.sort(key=lambda x: -x["script_len"])

    # ── 4. THOUGHTS OVERHEAD ─────────────────────────────────────────────
    def _thoughts_stats(cases):
        total_chars = 0
        total_segments = 0
        for c in cases:
            total_chars += c.thoughts_total_chars
            total_segments += len(c.thoughts_segments)
        n = max(len(cases), 1)
        return {
            "avg_chars": round(total_chars / n),
            "total_chars": total_chars,
            "avg_segments": round(total_segments / n, 1),
        }

    thoughts_overhead = {
        "golden": _thoughts_stats(golden),
        "clean_pass": _thoughts_stats(clean_pass),
        "recovery": _thoughts_stats(recovery),
        "failed": _thoughts_stats(failed),
    }

    # ── 5. EARLY FAILURE PREDICTION ───────────────────────────────────────
    # Check if errors in first 3 steps predict failure
    early_error_passed = 0
    early_error_failed = 0
    for c in all_cases:
        early_errors = any(
            s.step_type == "ScriptResponse" and s.error_type
            for s in c.steps if s.step_index <= 4
        )
        if early_errors:
            if c.passed:
                early_error_passed += 1
            else:
                early_error_failed += 1

    # High-step failures: cases that burned many steps then failed
    doomed = [c for c in failed if c.step_count >= 15]
    doomed_data = []
    for c in sorted(doomed, key=lambda x: -x.step_count)[:10]:
        first_err_idx = None
        for s in c.steps:
            if s.step_type == "ScriptResponse" and s.error_type:
                first_err_idx = s.step_index
                break
        doomed_data.append({
            "batch": c.batch, "query_index": c.query_index,
            "query_text": c.query_text[:120],
            "step_count": c.step_count, "error_count": c.error_count,
            "time_sec": round(c.execution_time_sec, 1),
            "first_error_step": first_err_idx,
            "script_exec_count": c.script_exec_count,
        })

    early_failure = {
        "early_error_passed": early_error_passed,
        "early_error_failed": early_error_failed,
        "early_error_total": early_error_passed + early_error_failed,
        "early_error_fail_rate": round(
            early_error_failed / max(early_error_passed + early_error_failed, 1) * 100
        ),
        "doomed_trajectories": doomed_data,
        "doomed_count": len(doomed),
        "doomed_total_steps": sum(c.step_count for c in doomed),
        "doomed_total_time": round(sum(c.execution_time_sec for c in doomed), 1),
    }

    # ── 6. ROUND-TRIP ANALYSIS ────────────────────────────────────────────
    def _avg_scripts(cases):
        if not cases:
            return 0
        return round(sum(c.script_exec_count for c in cases) / len(cases), 1)

    round_trips = {
        "golden_avg": _avg_scripts(golden),
        "clean_avg": _avg_scripts(clean_pass),
        "recovery_avg": _avg_scripts(recovery),
        "failed_avg": _avg_scripts(failed),
        "total_scripts_all": sum(c.script_exec_count for c in all_cases),
        "total_scripts_golden": sum(c.script_exec_count for c in golden),
        "total_scripts_failed": sum(c.script_exec_count for c in failed),
    }

    # Cases with high script count but passed (potential consolidation)
    high_script_pass = [
        {"batch": c.batch, "query_index": c.query_index, "query_text": c.query_text[:120],
         "scripts": c.script_exec_count, "errors": c.error_count, "steps": c.step_count}
        for c in passed if c.script_exec_count >= 6
    ]
    high_script_pass.sort(key=lambda x: -x["scripts"])
    round_trips["high_script_cases"] = high_script_pass[:10]

    # ── 7. CONSOLE OUTPUT BLOAT ───────────────────────────────────────────
    console_bloat_cases = []
    total_console_chars = 0
    for c in all_cases:
        case_console = 0
        bloat_steps = []
        for s in c.steps:
            if s.step_type == "ScriptResponse" and s.console:
                clen = len(s.console)
                case_console += clen
                total_console_chars += clen
                if clen > 500:
                    # Check for repeated lines
                    lines = s.console.strip().split("\n")
                    unique = set(lines)
                    dup_ratio = 1 - (len(unique) / max(len(lines), 1))
                    if dup_ratio > 0.3 or clen > 2000:
                        bloat_steps.append({
                            "step_index": s.step_index,
                            "console_len": clen,
                            "lines": len(lines),
                            "unique_lines": len(unique),
                            "dup_ratio": round(dup_ratio * 100),
                        })
        if bloat_steps:
            console_bloat_cases.append({
                "batch": c.batch, "query_index": c.query_index,
                "total_console_chars": case_console,
                "bloat_steps": bloat_steps,
                "passed": c.passed,
            })
    console_bloat_cases.sort(key=lambda x: -x["total_console_chars"])

    console_bloat = {
        "total_console_chars": total_console_chars,
        "bloat_cases": console_bloat_cases[:15],
        "bloat_case_count": len(console_bloat_cases),
    }

    # ── AGGREGATE ─────────────────────────────────────────────────────────
    avg_golden_steps = sum(c.step_count for c in golden) / max(len(golden), 1)
    avg_recovery_steps = sum(c.step_count for c in recovery) / max(len(recovery), 1)
    avg_failed_steps = sum(c.step_count for c in failed) / max(len(failed), 1)

    aggregate = {
        "total_cases": len(all_cases),
        "total_passed": len(passed),
        "total_failed": len(failed),
        "total_recovery": len(recovery),
        "total_golden": len(golden),
        "avg_golden_steps": round(avg_golden_steps, 1),
        "avg_recovery_steps": round(avg_recovery_steps, 1),
        "avg_failed_steps": round(avg_failed_steps, 1),
        "total_wasted_success": total_wasted_success,
        "overhead_pct": round(total_wasted_success / max(sum(c.step_count for c in recovery), 1) * 100),
        "thrashing_cases": sum(1 for w in wasteful_cases if w["has_thrashing"]),
        "common_wasted_errors": dict(all_wasted_errors.most_common(6)),
        "total_failed_steps": total_failed_steps,
        "total_failed_scripts": total_failed_scripts,
        "total_failed_time_sec": round(total_failed_time, 1),
    }

    # ── LLM SYNTHESIS ─────────────────────────────────────────────────────
    # Build detailed per-case profiles for step-level skip recommendations
    def _build_traj_profile(c, outcome_label):
        traj = []
        for s in c.steps:
            if s.step_type == "ScriptExecution":
                traj.append(f"  [{s.step_index}] SCRIPT ({s.script_len} chars): {s.script_preview[:100]}")
            elif s.step_type == "ScriptResponse":
                lbl = f"ERROR ({s.error_type})" if s.error_type else "OK"
                traj.append(f"  [{s.step_index}] {lbl}: {s.result[:100]}")
            elif s.step_type == "Thoughts":
                traj.append(f"  [{s.step_index}] THINKING ({len(s.content) if s.content else 0} chars)")
            elif s.step_type == "Assistant":
                traj.append(f"  [{s.step_index}] ASSISTANT: {(s.content or '')[:60]}")
        return (
            f"[{c.batch}] Q{c.query_index} ({outcome_label}, {c.step_count} steps, "
            f"{c.script_exec_count} scripts, {c.error_count} errors, {c.execution_time_sec:.1f}s):\n"
            f"  Query: {c.query_text[:120]}\n"
            + "\n".join(traj[:25])
        )

    all_profiles = []
    for w in wasteful_cases[:10]:
        c = next((c for c in recovery if c.batch == w["batch"] and c.query_index == w["query_index"]), None)
        if c:
            all_profiles.append(_build_traj_profile(c, "PASSED with errors — recovery"))
    for d in doomed_data[:8]:
        c = next((c for c in failed if c.batch == d["batch"] and c.query_index == d["query_index"]), None)
        if c:
            all_profiles.append(_build_traj_profile(c, "FAILED — doomed"))
    for h in high_script_pass[:5]:
        c = next((c for c in passed if c.batch == h["batch"] and c.query_index == h["query_index"]), None)
        if c:
            all_profiles.append(_build_traj_profile(c, "PASSED — high round-trips"))

    prompt = f"""You are an expert analyzing Excel Copilot agent trajectories to find SPECIFIC steps that were unnecessary and could be skipped to reduce token cost and execution time.

CONTEXT:
- {len(all_cases)} total queries: {len(passed)} passed, {len(failed)} failed
- Golden trajectories (0 errors, <8 steps): {len(golden)}, avg {avg_golden_steps:.1f} steps
- Recovery (passed with errors): {len(recovery)}, avg {avg_recovery_steps:.1f} steps, {total_wasted_success} wasted steps ({aggregate['overhead_pct']}% overhead)
- Failed: {len(failed)}, avg {avg_failed_steps:.1f} steps, {total_failed_steps} total steps burned
- {aggregate['thrashing_cases']} cases with thrashing (agent retries near-identical failing scripts)
- Common error types in wasted steps: {dict(all_wasted_errors.most_common(5))}
- Script sizes — golden avg: {script_bloat['golden']['avg']} chars, failed avg: {script_bloat['failed']['avg']} chars
- Thinking tokens — golden avg: {thoughts_overhead['golden']['avg_chars']} chars, failed avg: {thoughts_overhead['failed']['avg_chars']} chars
- Early failure signal: {early_failure['early_error_fail_rate']}% of cases with errors in first 3 steps eventually fail
- {len(oversized)} oversized scripts (>3000 chars), {len(console_bloat_cases)} cases with bloated console output

HERE ARE THE TRAJECTORIES TO ANALYZE:
{chr(10).join(all_profiles) or 'No trajectories available'}

YOUR TASK: For each trajectory above, identify the SPECIFIC steps that were unnecessary and should be skipped. Write your analysis as:

For each case, produce a block like:
**[batch] Q{{index}}: {{query summary}}**
Steps {{X}}-{{Y}} were unnecessary: {{what the agent did at those steps and why it added no value}}. The agent should have {{what it should have done instead}}. Skipping saves ~{{N}} steps.
(Include all skippable step ranges for that case.)

After all per-case analyses, write:

**CROSS-CUTTING PATTERNS** — identify 3-5 recurring mistakes the agent makes across multiple trajectories (e.g., "retries the same column name without checking available columns first", "generates 3000-char scripts when 500 chars would suffice"). For each pattern, state how many of the analyzed cases exhibit it and the estimated total step savings.

**BOTTOM LINE** — one paragraph: total estimated savings across all analyzed cases (steps, approximate token reduction, time), and the single highest-impact change that would prevent the most waste.

Be specific — reference actual step numbers, error types, and script content from the trajectories. No generic advice."""

    resp, _ = _call_llm(prompt, max_tokens=6000, temp=0.0)
    llm_insights = resp.strip() if resp else ""

    result = {
        "wasteful_cases": wasteful_cases,
        "failure_waste": failure_waste[:15],
        "script_bloat": script_bloat,
        "oversized_scripts": oversized[:15],
        "thoughts_overhead": thoughts_overhead,
        "early_failure": early_failure,
        "round_trips": round_trips,
        "console_bloat": console_bloat,
        "aggregate": aggregate,
        "llm_insights": llm_insights,
    }

    _traj_efficiency_cache[cache_key] = result
    try:
        os.makedirs(Path(".cache"), exist_ok=True)
        disk_path.write_text(
            json.dumps(result, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        pass

    return result

def generate_narrative(
    all_cases: list[ParsedEvalCase],
    classifications: list[ClassificationResult],
    insights: dict,
    full_insights: dict,
) -> list[str]:
    paragraphs: list[str] = []
    failed = [c for c in all_cases if not c.passed]
    total = len(all_cases)
    total_f = len(failed)

    if total_f == 0:
        return ["All queries passed. No failure patterns to report."]

    pass_rate = (total - total_f) / total * 100
    cat_dist = insights.get("primary_category_distribution", {})
    top_cat = max(cat_dist, key=cat_dist.get) if cat_dist else None
    top_count = cat_dist.get(top_cat, 0) if top_cat else 0

    paragraphs.append(
        f"{total_f} of {total} queries failed ({100 - pass_rate:.0f}% failure rate). "
        f"The biggest trajectory pattern is \"{top_cat}\" ({top_count} cases, "
        f"{top_count / max(total_f, 1) * 100:.0f}% of all failures)."
    )

    outc_dist = insights.get("outcome_category_distribution", {})
    if outc_dist:
        top_outc = max(outc_dist, key=outc_dist.get)
        top_outc_count = outc_dist[top_outc]
        paragraphs.append(
            f"The most common outcome issue is \"{top_outc}\" ({top_outc_count} cases)."
        )

    spec_axis = full_insights.get("specificity_axis", [])
    ambig_data = [s for s in spec_axis if "Ambiguous" in s["level"]]
    well_data = [s for s in spec_axis if "Well" in s["level"]]
    if ambig_data and well_data:
        ambig_fail = sum(s["fail_rate"] for s in ambig_data) / len(ambig_data)
        well_fail = sum(s["fail_rate"] for s in well_data) / len(well_data)
        if ambig_fail > well_fail * 2:
            paragraphs.append(
                f"Ambiguous prompts fail at {ambig_fail:.0f}% vs {well_fail:.0f}% for well-specified."
            )

    claim_but_fail = sum(1 for c in failed if c.has_success_claim)
    if claim_but_fail == total_f and total_f >= 3:
        paragraphs.append(
            f"Every failure ({total_f}/{total_f}) includes the agent claiming success."
        )

    return paragraphs


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Classify evalVNext failures")
    parser.add_argument("path", help="Report JSON, batch folder, or base dir")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    p = Path(args.path)
    if p.is_file():
        cases = parse_report_file(p, failures_only=True)
    elif p.is_dir():
        has_regression = any(d.name.startswith("RegressionBench_") for d in p.iterdir() if d.is_dir())
        if has_regression:
            batches = auto_discover_batches(p, failures_only=True)
            cases = [c for batch_cases in batches.values() for c in batch_cases]
        else:
            from trajectory_parser import parse_batch_folder
            cases = parse_batch_folder(p, failures_only=True)
    else:
        print(f"Path not found: {p}")
        sys.exit(1)

    classifications = classify_all(cases)
    insights = generate_insights(classifications)

    if args.json:
        output = {
            "classifications": [c.to_dict() for c in classifications],
            "insights": insights,
        }
        print(json.dumps(output, indent=2, ensure_ascii=False))
    else:
        total = insights["total_failures"]
        print(f"Total failures: {total}")
        print(f"\nCategories (discovered dynamically):")
        for cat, count in insights["primary_category_distribution"].items():
            pct = count / total * 100
            print(f"  {cat:30s}  {count:3d}  ({pct:.0f}%)")
        print(f"\nPer-case:")
        for c in classifications:
            print(f"  {c.batch} Q{c.query_index}: {c.primary_category} [{c.confidence}]")
            print(f"    Why: {c.why[:120]}")
