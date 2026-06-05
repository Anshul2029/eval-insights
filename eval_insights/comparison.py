"""Run Comparison module for Eval Insights Platform.

Compares two eval runs side-by-side: regressions, improvements,
persistent failures, metric deltas, and LLM-generated insights.
"""
from __future__ import annotations

import hashlib
import html
import json
import os
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import streamlit as st

from trajectory_parser import auto_discover_batches, ParsedEvalCase
from failure_classifier import (
    classify_all,
    ClassificationResult,
    extract_grader_evidence,
    _call_llm,
    _tag_value,
)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class QueryMatch:
    query_index: str
    batch: str
    query_text: str
    case_a: ParsedEvalCase
    case_b: ParsedEvalCase
    cls_a: Optional[ClassificationResult] = None
    cls_b: Optional[ClassificationResult] = None
    transition: str = ""  # pass_pass | pass_fail | fail_pass | fail_fail


@dataclass
class ComparisonSummary:
    a_total: int = 0
    a_passed: int = 0
    a_failed: int = 0
    a_pass_rate: float = 0.0
    b_total: int = 0
    b_passed: int = 0
    b_failed: int = 0
    b_pass_rate: float = 0.0
    pass_pass: int = 0
    fail_fail: int = 0
    pass_fail: int = 0
    fail_pass: int = 0
    pass_rate_delta: float = 0.0
    avg_time_delta: float = 0.0
    avg_errors_delta: float = 0.0
    avg_steps_delta: float = 0.0


@dataclass
class ComparisonResult:
    run_a_path: str = ""
    run_b_path: str = ""
    pm_context: str = ""
    matches: list[QueryMatch] = field(default_factory=list)
    a_only: list[ParsedEvalCase] = field(default_factory=list)
    b_only: list[ParsedEvalCase] = field(default_factory=list)
    summary: ComparisonSummary = field(default_factory=ComparisonSummary)


def _h(text):
    return html.escape(str(text))


# ---------------------------------------------------------------------------
# Data loading for comparison
# ---------------------------------------------------------------------------

@st.cache_resource(show_spinner="Parsing run...", ttl=300)
def _load_run(base_path: str, gem_key: str = "", groq_key: str = ""):
    batches = auto_discover_batches(base_path)
    all_cases = [c for cases in batches.values() for c in cases]
    classifications = classify_all(all_cases, gemini_key=gem_key)
    return batches, all_cases, classifications


# ---------------------------------------------------------------------------
# Query matching
# ---------------------------------------------------------------------------

def _match_queries(
    cases_a: list[ParsedEvalCase],
    cases_b: list[ParsedEvalCase],
    cls_a: list[ClassificationResult],
    cls_b: list[ClassificationResult],
    path_a: str,
    path_b: str,
    pm_context: str,
) -> ComparisonResult:
    lookup_a = {(c.batch, c.query_index): c for c in cases_a}
    lookup_b = {(c.batch, c.query_index): c for c in cases_b}
    cls_lookup_a = {(c.batch, c.query_index): c for c in cls_a}
    cls_lookup_b = {(c.batch, c.query_index): c for c in cls_b}

    all_keys = set(lookup_a.keys()) | set(lookup_b.keys())
    matched_keys = set(lookup_a.keys()) & set(lookup_b.keys())

    matches = []
    for key in sorted(matched_keys):
        ca, cb = lookup_a[key], lookup_b[key]
        transition = (
            "pass_pass" if ca.passed and cb.passed else
            "pass_fail" if ca.passed and not cb.passed else
            "fail_pass" if not ca.passed and cb.passed else
            "fail_fail"
        )
        matches.append(QueryMatch(
            query_index=key[1],
            batch=key[0],
            query_text=ca.query_text,
            case_a=ca,
            case_b=cb,
            cls_a=cls_lookup_a.get(key),
            cls_b=cls_lookup_b.get(key),
            transition=transition,
        ))

    a_only = [lookup_a[k] for k in sorted(set(lookup_a.keys()) - matched_keys)]
    b_only = [lookup_b[k] for k in sorted(set(lookup_b.keys()) - matched_keys)]

    # Compute summary
    a_passed = sum(1 for c in cases_a if c.passed)
    b_passed = sum(1 for c in cases_b if c.passed)
    a_total, b_total = len(cases_a), len(cases_b)
    a_rate = a_passed / max(a_total, 1) * 100
    b_rate = b_passed / max(b_total, 1) * 100

    avg_time_a = sum(c.execution_time_sec for c in cases_a) / max(a_total, 1)
    avg_time_b = sum(c.execution_time_sec for c in cases_b) / max(b_total, 1)
    avg_err_a = sum(c.error_count for c in cases_a) / max(a_total, 1)
    avg_err_b = sum(c.error_count for c in cases_b) / max(b_total, 1)
    avg_steps_a = sum(c.step_count for c in cases_a) / max(a_total, 1)
    avg_steps_b = sum(c.step_count for c in cases_b) / max(b_total, 1)

    transition_counts = Counter(m.transition for m in matches)

    summary = ComparisonSummary(
        a_total=a_total, a_passed=a_passed, a_failed=a_total - a_passed, a_pass_rate=a_rate,
        b_total=b_total, b_passed=b_passed, b_failed=b_total - b_passed, b_pass_rate=b_rate,
        pass_pass=transition_counts.get("pass_pass", 0),
        fail_fail=transition_counts.get("fail_fail", 0),
        pass_fail=transition_counts.get("pass_fail", 0),
        fail_pass=transition_counts.get("fail_pass", 0),
        pass_rate_delta=b_rate - a_rate,
        avg_time_delta=avg_time_b - avg_time_a,
        avg_errors_delta=avg_err_b - avg_err_a,
        avg_steps_delta=avg_steps_b - avg_steps_a,
    )

    return ComparisonResult(
        run_a_path=path_a,
        run_b_path=path_b,
        pm_context=pm_context,
        matches=matches,
        a_only=a_only,
        b_only=b_only,
        summary=summary,
    )


# ---------------------------------------------------------------------------
# LLM analysis
# ---------------------------------------------------------------------------

_llm_cache: dict[str, str] = {}
_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache")


def _llm_cache_path(path_a: str, path_b: str) -> str:
    key = f"{os.path.normpath(path_a)}|{os.path.normpath(path_b)}"
    h = hashlib.md5(key.encode()).hexdigest()[:12]
    return os.path.join(_CACHE_DIR, f"comparison_insights_{h}.json")


def _load_llm_cache(path_a: str, path_b: str) -> dict:
    p = _llm_cache_path(path_a, path_b)
    if os.path.exists(p):
        try:
            with open(p) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_llm_cache(path_a: str, path_b: str, cache: dict):
    os.makedirs(_CACHE_DIR, exist_ok=True)
    with open(_llm_cache_path(path_a, path_b), "w") as f:
        json.dump(cache, f, indent=2)


def _analyze_transitions(
    matches: list[QueryMatch],
    transition_type: str,
    pm_context: str,
    path_a: str,
    path_b: str,
) -> list[dict]:
    cache = _load_llm_cache(path_a, path_b)
    cache_key = f"transitions_{transition_type}"
    if cache_key in cache:
        return cache[cache_key]

    cases = [m for m in matches if m.transition == transition_type]
    if not cases:
        return []

    profiles = []
    for m in cases[:20]:
        traj_a_lines = []
        for s in m.case_a.steps:
            if s.step_type == "ScriptResponse":
                status = f"ERROR ({s.error_type})" if s.error_type else "OK"
                traj_a_lines.append(f"  [{s.step_index}] {status}: {s.result[:100]}")
            elif s.step_type == "ScriptExecution":
                traj_a_lines.append(f"  [{s.step_index}] Script: {s.script_preview[:80]}")

        traj_b_lines = []
        for s in m.case_b.steps:
            if s.step_type == "ScriptResponse":
                status = f"ERROR ({s.error_type})" if s.error_type else "OK"
                traj_b_lines.append(f"  [{s.step_index}] {status}: {s.result[:100]}")
            elif s.step_type == "ScriptExecution":
                traj_b_lines.append(f"  [{s.step_index}] Script: {s.script_preview[:80]}")

        ev_a = extract_grader_evidence(m.case_a)
        ev_b = extract_grader_evidence(m.case_b)

        profile = {
            "batch": m.batch,
            "query_index": m.query_index,
            "query": m.query_text[:200],
            "run_a": {
                "passed": m.case_a.passed,
                "steps": m.case_a.step_count,
                "errors": m.case_a.error_count,
                "error_types": m.case_a.error_types[:5],
                "grader": ev_a.grader_message[:300],
                "trajectory": "\n".join(traj_a_lines[:15]),
            },
            "run_b": {
                "passed": m.case_b.passed,
                "steps": m.case_b.step_count,
                "errors": m.case_b.error_count,
                "error_types": m.case_b.error_types[:5],
                "grader": ev_b.grader_message[:300],
                "trajectory": "\n".join(traj_b_lines[:15]),
            },
        }
        if m.cls_a:
            profile["run_a"]["classification"] = {
                "trajectory": m.cls_a.trajectory_categories,
                "outcome": m.cls_a.outcome_category,
                "why": m.cls_a.why,
            }
        if m.cls_b:
            profile["run_b"]["classification"] = {
                "trajectory": m.cls_b.trajectory_categories,
                "outcome": m.cls_b.outcome_category,
                "why": m.cls_b.why,
            }
        profiles.append(profile)

    if transition_type == "pass_fail":
        label = "REGRESSIONS (passed in Run A, failed in Run B)"
        task = "For each regression, explain: (1) what specifically broke, (2) likely cause given the PM's changes"
    elif transition_type == "fail_pass":
        label = "IMPROVEMENTS (failed in Run A, passed in Run B)"
        task = "For each improvement, explain: (1) what specifically got fixed, (2) how it correlates with the PM's changes"
    else:
        label = "PERSISTENT FAILURES (failed in both runs)"
        task = "For each persistent failure, explain: (1) did the failure mode change between runs, (2) what would need to change to fix it"

    prompt = f"""You are analyzing {label} between two eval runs of an Excel Copilot AI agent.

PM CONTEXT (what changed between runs): "{pm_context or 'Not provided'}"

CASES:
{json.dumps(profiles, indent=2)}

YOUR TASK: {task}

Return a JSON array with one object per case:
[
  {{
    "batch": "...",
    "query_index": "...",
    "analysis": "2-3 sentences of specific, data-backed analysis",
    "likely_cause": "1 sentence on probable cause"
  }}
]

Be specific — reference actual error types, step counts, grader messages. Do not be generic."""

    resp, _ = _call_llm(prompt, max_tokens=4000, temp=0.0)
    results = []
    if resp:
        try:
            import re
            match = re.search(r'\[.*\]', resp, re.DOTALL)
            if match:
                results = json.loads(match.group())
        except Exception:
            pass

    cache[cache_key] = results
    _save_llm_cache(path_a, path_b, cache)
    return results


def _build_comparison_data_block(comp: ComparisonResult, targets: dict[str, float]) -> str:
    """Build the shared data block used by both executive summary and deep-dive insights."""
    s = comp.summary

    batch_deltas = {}
    for m in comp.matches:
        b = m.batch
        if b not in batch_deltas:
            batch_deltas[b] = {"a_pass": 0, "a_total": 0, "b_pass": 0, "b_total": 0}
        batch_deltas[b]["a_total"] += 1
        batch_deltas[b]["b_total"] += 1
        if m.case_a.passed:
            batch_deltas[b]["a_pass"] += 1
        if m.case_b.passed:
            batch_deltas[b]["b_pass"] += 1

    batch_lines = []
    for b, d in sorted(batch_deltas.items()):
        a_r = d["a_pass"] / max(d["a_total"], 1) * 100
        b_r = d["b_pass"] / max(d["b_total"], 1) * 100
        target_str = f", target: {targets[b]:.0f}%" if b in targets else ""
        batch_lines.append(f"  {b}: {a_r:.1f}% -> {b_r:.1f}% ({b_r - a_r:+.1f}pp){target_str}")

    regressions = [m for m in comp.matches if m.transition == "pass_fail"]
    improvements = [m for m in comp.matches if m.transition == "fail_pass"]
    persistent = [m for m in comp.matches if m.transition == "fail_fail"]

    reg_lines = "\n".join(f"  [{m.batch}] Q{m.query_index}: {m.query_text[:80]}" for m in regressions[:10])
    imp_lines = "\n".join(f"  [{m.batch}] Q{m.query_index}: {m.query_text[:80]}" for m in improvements[:10])

    # Trajectory category shifts
    traj_a: Counter = Counter()
    traj_b: Counter = Counter()
    for m in comp.matches:
        if m.cls_a:
            for tc in m.cls_a.trajectory_categories:
                traj_a[tc] += 1
        if m.cls_b:
            for tc in m.cls_b.trajectory_categories:
                traj_b[tc] += 1
    all_traj_cats = sorted(set(traj_a.keys()) | set(traj_b.keys()))
    traj_shift_lines = "\n".join(
        f"  {cat}: {traj_a.get(cat, 0)} -> {traj_b.get(cat, 0)} ({traj_b.get(cat, 0) - traj_a.get(cat, 0):+d})"
        for cat in all_traj_cats
    )

    # Error type shifts
    err_a: Counter = Counter()
    err_b: Counter = Counter()
    for m in comp.matches:
        for et in m.case_a.error_types:
            err_a[et] += 1
        for et in m.case_b.error_types:
            err_b[et] += 1
    all_err = sorted(set(err_a.keys()) | set(err_b.keys()))
    err_shift_lines = "\n".join(
        f"  {et}: {err_a.get(et, 0)} -> {err_b.get(et, 0)} ({err_b.get(et, 0) - err_a.get(et, 0):+d})"
        for et in all_err
    )

    # Trajectory metric shifts
    matched = comp.matches
    if matched:
        avg_steps_a = sum(m.case_a.step_count for m in matched) / len(matched)
        avg_steps_b = sum(m.case_b.step_count for m in matched) / len(matched)
        avg_err_a = sum(m.case_a.error_count for m in matched) / len(matched)
        avg_err_b = sum(m.case_b.error_count for m in matched) / len(matched)
        avg_time_a = sum(m.case_a.execution_time_sec for m in matched) / len(matched)
        avg_time_b = sum(m.case_b.execution_time_sec for m in matched) / len(matched)
        total_scripts_a = sum(m.case_a.script_exec_count for m in matched)
        total_scripts_b = sum(m.case_b.script_exec_count for m in matched)
        total_errors_a = sum(m.case_a.error_count for m in matched)
        total_errors_b = sum(m.case_b.error_count for m in matched)
    else:
        avg_steps_a = avg_steps_b = avg_err_a = avg_err_b = 0
        avg_time_a = avg_time_b = total_scripts_a = total_scripts_b = 0
        total_errors_a = total_errors_b = 0

    # Persistent failure pattern changes
    pattern_changed = []
    pattern_same = []
    for m in persistent:
        cats_a = set(m.cls_a.trajectory_categories) if m.cls_a else set()
        cats_b = set(m.cls_b.trajectory_categories) if m.cls_b else set()
        if cats_a != cats_b:
            pattern_changed.append(f"  [{m.batch}] Q{m.query_index}: {', '.join(cats_a) or 'Unclassified'} -> {', '.join(cats_b) or 'Unclassified'}")
        else:
            pattern_same.append(f"  [{m.batch}] Q{m.query_index}: {', '.join(cats_a) or 'Unclassified'}")

    # Additional queries
    b_only_pass = [c for c in comp.b_only if c.passed]
    b_only_fail = [c for c in comp.b_only if not c.passed]
    a_only_lines = "\n".join(
        f"  [{c.batch}] Q{c.query_index} ({'PASS' if c.passed else 'FAIL'}): {c.query_text[:80]}"
        for c in comp.a_only[:10]
    )
    b_only_lines = "\n".join(
        f"  [{c.batch}] Q{c.query_index} ({'PASS' if c.passed else 'FAIL'}): {c.query_text[:80]}"
        for c in comp.b_only[:10]
    )

    return f"""PM CONTEXT (what changed): "{comp.pm_context or 'Not provided'}"

OVERALL:
- Run A: {s.a_pass_rate:.1f}% pass rate ({s.a_passed}/{s.a_total} queries)
- Run B: {s.b_pass_rate:.1f}% pass rate ({s.b_passed}/{s.b_total} queries)
- Delta: {s.pass_rate_delta:+.1f}pp
- Regressions (pass->fail): {s.pass_fail}
- Improvements (fail->pass): {s.fail_pass}
- Persistent failures (fail->fail): {s.fail_fail} ({len(pattern_changed)} changed pattern, {len(pattern_same)} same pattern)
- Stable passes (pass->pass): {s.pass_pass}

BATCH-LEVEL DELTAS:
{chr(10).join(batch_lines)}

TRAJECTORY EXECUTION SHIFTS:
- Avg steps: {avg_steps_a:.1f} -> {avg_steps_b:.1f} ({avg_steps_b - avg_steps_a:+.1f})
- Avg errors: {avg_err_a:.1f} -> {avg_err_b:.1f} ({avg_err_b - avg_err_a:+.1f})
- Avg time: {avg_time_a:.1f}s -> {avg_time_b:.1f}s ({avg_time_b - avg_time_a:+.1f}s)
- Total scripts: {total_scripts_a} -> {total_scripts_b} ({total_scripts_b - total_scripts_a:+d})
- Total errors: {total_errors_a} -> {total_errors_b} ({total_errors_b - total_errors_a:+d})

FAILURE CATEGORY SHIFTS (trajectory patterns):
{traj_shift_lines or '  No failure classifications available'}

ERROR TYPE SHIFTS:
{err_shift_lines or '  No errors in either run'}

PERSISTENT FAILURE PATTERN CHANGES:
{chr(10).join(pattern_changed[:8]) or '  None'}
PERSISTENT FAILURES WITH SAME PATTERN:
{chr(10).join(pattern_same[:8]) or '  None'}

REGRESSIONS:
{reg_lines or '  None'}

IMPROVEMENTS:
{imp_lines or '  None'}

ADDITIONAL QUERIES (only in Run B, {len(comp.b_only)} total, {len(b_only_pass)} pass, {len(b_only_fail)} fail):
{b_only_lines or '  None'}

REMOVED QUERIES (only in Run A, {len(comp.a_only)} total):
{a_only_lines or '  None'}"""


def _generate_executive_summary(
    comp: ComparisonResult,
    targets: dict[str, float],
) -> str:
    cache = _load_llm_cache(comp.run_a_path, comp.run_b_path)
    if "exec_summary" in cache:
        return cache["exec_summary"]

    data_block = _build_comparison_data_block(comp, targets)

    prompt = f"""You are analyzing two eval runs of an Excel Copilot AI agent. Write a concise executive summary (3-5 paragraphs) that gives a PM the complete picture without needing to look at any other tab.

{data_block}

Cover in your summary:
1. Net impact: did the changes help or hurt? By how much?
2. The most significant shifts — what improved, what regressed, what stayed broken
3. Trajectory behavior changes — are agents taking more/fewer steps, hitting different errors, changing patterns?
4. If there are additional/removed queries, comment on their results
5. One clear recommendation for what to do next

Write in clear prose. Be specific — cite numbers, batch names, query IDs. No generic statements."""

    resp, _ = _call_llm(prompt, max_tokens=3000, temp=0.0)
    summary = resp.strip() if resp else ""

    if not summary:
        s = comp.summary
        summary = (
            f"Run B shows a {s.pass_rate_delta:+.1f}pp change in pass rate "
            f"({s.a_pass_rate:.1f}% -> {s.b_pass_rate:.1f}%). "
            f"{s.fail_pass} queries improved, {s.pass_fail} regressed. "
            f"Enable GROQ_API_KEY or GEMINI_API_KEY for detailed analysis."
        )

    cache["exec_summary"] = summary
    _save_llm_cache(comp.run_a_path, comp.run_b_path, cache)
    return summary


def _generate_comparison_narrative(
    comp: ComparisonResult,
    targets: dict[str, float],
) -> str:
    cache = _load_llm_cache(comp.run_a_path, comp.run_b_path)
    if "narrative" in cache:
        return cache["narrative"]

    data_block = _build_comparison_data_block(comp, targets)

    prompt = f"""You are doing a DEEP-DIVE analysis of two eval runs of an Excel Copilot AI agent. This is the detailed insights tab — go beyond the executive summary. Analyze patterns, correlations, and non-obvious findings.

{data_block}

Provide a thorough analysis covering ALL of these:

1. TRAJECTORY EVOLUTION: How did the agent's behavior change? Are trajectories getting shorter/longer? Are error patterns shifting (e.g., fewer InvalidArgument but more ItemNotFound)? What does this mean about how the agent's approach changed?

2. FAILURE PATTERN SHIFTS: Which failure categories grew or shrank? For persistent failures that changed pattern — what does the shift tell us about the effect of the PM's changes?

3. ERROR EVOLUTION: Are new error types appearing? Are old ones disappearing? Is the agent making different kinds of mistakes now?

4. ADDITIONAL QUERIES: If new queries were added, how are they performing? Are they testing areas that were previously weak? Any concerning patterns in the new queries?

5. WHAT THE DATA SAYS ABOUT THE CHANGES: Based on the trajectory and error shifts, what specifically did the PM's changes likely affect? Did they fix what they intended? Any side effects?

6. REMAINING GAPS & NEXT STEPS: What's still broken? What should the PM change next? Be specific — name error types, query categories, or trajectory patterns to target.

Write 5-8 paragraphs of specific, data-backed analysis. Reference actual numbers, error types, category names. This is the detailed analysis tab — be thorough."""

    resp, _ = _call_llm(prompt, max_tokens=5000, temp=0.0)
    narrative = resp or "LLM analysis unavailable. Check API keys."

    cache["narrative"] = narrative
    _save_llm_cache(comp.run_a_path, comp.run_b_path, cache)
    return narrative


# ---------------------------------------------------------------------------
# CSS additions for comparison mode
# ---------------------------------------------------------------------------

_COMPARISON_CSS = """
<style>
.cmp-hero {
    background: linear-gradient(135deg, #0f172a 0%, #312e81 50%, #4c1d95 100%);
    color: white; padding: 2.2rem 2.5rem; border-radius: 14px; margin-bottom: 1.5rem;
}
.cmp-hero h1 { font-size: 1.8rem; font-weight: 800; margin: 0 0 0.3rem 0; color: white; }
.cmp-hero p { font-size: 0.92rem; color: #c4b5fd; margin: 0; }
.cmp-kpi-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 0.8rem; margin-bottom: 1.2rem; }
.cmp-kpi {
    background: white; border: 1px solid #e2e8f0; border-radius: 12px;
    padding: 0.9rem 1.1rem; text-align: center;
}
.cmp-kpi .label { font-size: 0.7rem; font-weight: 600; color: #64748b; text-transform: uppercase; letter-spacing: 0.08em; }
.cmp-kpi .value { font-size: 1.6rem; font-weight: 700; margin-top: 0.2rem; }
.cmp-kpi .delta { font-size: 0.82rem; font-weight: 600; margin-top: 0.1rem; }
.cmp-kpi .delta.pos { color: #059669; }
.cmp-kpi .delta.neg { color: #dc2626; }
.cmp-kpi .delta.neutral { color: #64748b; }
.transition-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0.8rem; margin-bottom: 1.2rem; }
.transition-cell {
    border-radius: 12px; padding: 1.1rem 1.3rem; text-align: center;
    border: 1px solid #e2e8f0;
}
.transition-cell .count { font-size: 2rem; font-weight: 800; }
.transition-cell .label { font-size: 0.78rem; font-weight: 600; margin-top: 0.2rem; }
.transition-cell.stable { background: #f0fdf4; border-color: #86efac; }
.transition-cell.stable .count { color: #059669; }
.transition-cell.stable .label { color: #065f46; }
.transition-cell.regression { background: #fef2f2; border-color: #fca5a5; }
.transition-cell.regression .count { color: #dc2626; }
.transition-cell.regression .label { color: #991b1b; }
.transition-cell.improvement { background: #eff6ff; border-color: #93c5fd; }
.transition-cell.improvement .count { color: #2563eb; }
.transition-cell.improvement .label { color: #1e40af; }
.transition-cell.persistent { background: #fefce8; border-color: #fde68a; }
.transition-cell.persistent .count { color: #d97706; }
.transition-cell.persistent .label { color: #92400e; }
.pm-context-card {
    background: linear-gradient(135deg, #faf5ff 0%, #f3e8ff 100%);
    border: 1px solid #d8b4fe; border-left: 4px solid #7c3aed;
    border-radius: 10px; padding: 1rem 1.3rem; margin-bottom: 1.2rem;
    font-size: 0.9rem; color: #4c1d95; line-height: 1.6;
}
.pm-context-card strong { color: #6d28d9; font-size: 0.76rem; text-transform: uppercase; letter-spacing: 0.05em; }
.cmp-card {
    background: white; border: 1px solid #e2e8f0; border-radius: 12px;
    padding: 1.2rem 1.4rem; margin-bottom: 0.8rem;
}
.cmp-card.regression { border-left: 4px solid #dc2626; }
.cmp-card.improvement { border-left: 4px solid #2563eb; }
.cmp-card.persistent { border-left: 4px solid #d97706; }
.cmp-side { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; margin: 0.8rem 0; }
.cmp-side-col {
    border-radius: 8px; padding: 0.8rem 1rem; font-size: 0.82rem; line-height: 1.6;
}
.cmp-side-col.run-a { background: #f0f9ff; border: 1px solid #bae6fd; }
.cmp-side-col.run-b { background: #fef2f2; border: 1px solid #fecaca; }
.cmp-side-col.run-b.passed { background: #f0fdf4; border: 1px solid #bbf7d0; }
.cmp-side-label {
    font-size: 0.68rem; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.06em; margin-bottom: 0.4rem;
}
.cmp-analysis {
    background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px;
    padding: 0.8rem 1rem; font-size: 0.85rem; line-height: 1.6;
    color: #334155; margin-top: 0.6rem;
}
.cmp-section-strip {
    background: linear-gradient(90deg, #312e81 0%, #4c1d95 100%);
    color: white; padding: 0.7rem 1.1rem; border-radius: 8px;
    font-weight: 600; font-size: 0.92rem; margin: 1.2rem 0 1rem 0;
}
.cmp-narrative {
    background: white; border: 1px solid #e2e8f0; border-radius: 12px;
    padding: 1.5rem 1.8rem; font-size: 0.9rem; line-height: 1.8;
    color: #1e293b; margin-bottom: 1rem;
}
.badge-reg {
    display: inline-block; background: #dc2626; color: white;
    font-size: 0.7rem; font-weight: 600; padding: 0.15rem 0.5rem;
    border-radius: 20px; margin-right: 0.3rem;
}
.badge-imp {
    display: inline-block; background: #2563eb; color: white;
    font-size: 0.7rem; font-weight: 600; padding: 0.15rem 0.5rem;
    border-radius: 20px; margin-right: 0.3rem;
}
.badge-stable {
    display: inline-block; background: #059669; color: white;
    font-size: 0.7rem; font-weight: 600; padding: 0.15rem 0.5rem;
    border-radius: 20px; margin-right: 0.3rem;
}
.badge-persist {
    display: inline-block; background: #d97706; color: white;
    font-size: 0.7rem; font-weight: 600; padding: 0.15rem 0.5rem;
    border-radius: 20px; margin-right: 0.3rem;
}
</style>
"""

_COMPARISON_DARK_CSS = """
<style>
.cmp-hero {
    background: linear-gradient(135deg, #0d1117 0%, #1a1a2e 50%, #2d1b69 100%);
    color: #e6edf3; padding: 2.2rem 2.5rem; border-radius: 14px; margin-bottom: 1.5rem;
    border: 1px solid #30363d;
}
.cmp-hero h1 { font-size: 1.8rem; font-weight: 800; margin: 0 0 0.3rem 0; color: #e6edf3; }
.cmp-hero p { font-size: 0.92rem; color: #bc8cff; margin: 0; }
.cmp-kpi-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 0.8rem; margin-bottom: 1.2rem; }
.cmp-kpi {
    background: #161b22; border: 1px solid #30363d; border-radius: 12px;
    padding: 0.9rem 1.1rem; text-align: center;
}
.cmp-kpi .label { font-size: 0.7rem; font-weight: 600; color: #8b949e; text-transform: uppercase; letter-spacing: 0.08em; }
.cmp-kpi .value { font-size: 1.6rem; font-weight: 700; margin-top: 0.2rem; }
.cmp-kpi .delta { font-size: 0.82rem; font-weight: 600; margin-top: 0.1rem; }
.cmp-kpi .delta.pos { color: #3fb950; }
.cmp-kpi .delta.neg { color: #f85149; }
.cmp-kpi .delta.neutral { color: #8b949e; }
.transition-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0.8rem; margin-bottom: 1.2rem; }
.transition-cell {
    border-radius: 12px; padding: 1.1rem 1.3rem; text-align: center;
    border: 1px solid #30363d;
}
.transition-cell .count { font-size: 2rem; font-weight: 800; }
.transition-cell .label { font-size: 0.78rem; font-weight: 600; margin-top: 0.2rem; }
.transition-cell.stable { background: #0d2818; border-color: #238636; }
.transition-cell.stable .count { color: #3fb950; }
.transition-cell.stable .label { color: #7ee787; }
.transition-cell.regression { background: #2d1215; border-color: #da3633; }
.transition-cell.regression .count { color: #f85149; }
.transition-cell.regression .label { color: #ffa198; }
.transition-cell.improvement { background: #0d1b2e; border-color: #1f6feb; }
.transition-cell.improvement .count { color: #58a6ff; }
.transition-cell.improvement .label { color: #79c0ff; }
.transition-cell.persistent { background: #2d2000; border-color: #9e6a03; }
.transition-cell.persistent .count { color: #d29922; }
.transition-cell.persistent .label { color: #e3b341; }
.pm-context-card {
    background: linear-gradient(135deg, #1a1a2e 0%, #2d1b69 100%);
    border: 1px solid #6e40c9; border-left: 4px solid #bc8cff;
    border-radius: 10px; padding: 1rem 1.3rem; margin-bottom: 1.2rem;
    font-size: 0.9rem; color: #d2a8ff; line-height: 1.6;
}
.pm-context-card strong { color: #bc8cff; font-size: 0.76rem; text-transform: uppercase; letter-spacing: 0.05em; }
.cmp-card {
    background: #161b22; border: 1px solid #30363d; border-radius: 12px;
    padding: 1.2rem 1.4rem; margin-bottom: 0.8rem;
}
.cmp-card.regression { border-left: 4px solid #f85149; }
.cmp-card.improvement { border-left: 4px solid #58a6ff; }
.cmp-card.persistent { border-left: 4px solid #d29922; }
.cmp-side { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; margin: 0.8rem 0; }
.cmp-side-col {
    border-radius: 8px; padding: 0.8rem 1rem; font-size: 0.82rem; line-height: 1.6;
}
.cmp-side-col.run-a { background: #0d1b2e; border: 1px solid #1f6feb; }
.cmp-side-col.run-b { background: #2d1215; border: 1px solid #da3633; }
.cmp-side-col.run-b.passed { background: #0d2818; border: 1px solid #238636; }
.cmp-side-label {
    font-size: 0.68rem; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.06em; margin-bottom: 0.4rem; color: #8b949e;
}
.cmp-analysis {
    background: #0d1117; border: 1px solid #30363d; border-radius: 8px;
    padding: 0.8rem 1rem; font-size: 0.85rem; line-height: 1.6;
    color: #c9d1d9; margin-top: 0.6rem;
}
.cmp-section-strip {
    background: linear-gradient(90deg, #1a1a2e 0%, #2d1b69 100%);
    color: #e6edf3; padding: 0.7rem 1.1rem; border-radius: 8px;
    font-weight: 600; font-size: 0.92rem; margin: 1.2rem 0 1rem 0;
    border: 1px solid #30363d;
}
.cmp-narrative {
    background: #161b22; border: 1px solid #30363d; border-radius: 12px;
    padding: 1.5rem 1.8rem; font-size: 0.9rem; line-height: 1.8;
    color: #c9d1d9; margin-bottom: 1rem;
}
.badge-reg {
    display: inline-block; background: #f85149; color: #0d1117;
    font-size: 0.7rem; font-weight: 600; padding: 0.15rem 0.5rem;
    border-radius: 20px; margin-right: 0.3rem;
}
.badge-imp {
    display: inline-block; background: #58a6ff; color: #0d1117;
    font-size: 0.7rem; font-weight: 600; padding: 0.15rem 0.5rem;
    border-radius: 20px; margin-right: 0.3rem;
}
.badge-stable {
    display: inline-block; background: #3fb950; color: #0d1117;
    font-size: 0.7rem; font-weight: 600; padding: 0.15rem 0.5rem;
    border-radius: 20px; margin-right: 0.3rem;
}
.badge-persist {
    display: inline-block; background: #d29922; color: #0d1117;
    font-size: 0.7rem; font-weight: 600; padding: 0.15rem 0.5rem;
    border-radius: 20px; margin-right: 0.3rem;
}
</style>
"""


# ---------------------------------------------------------------------------
# UI helper: delta formatting
# ---------------------------------------------------------------------------

def _delta_html(value: float, suffix: str = "", invert: bool = False) -> str:
    if abs(value) < 0.01:
        return '<span class="delta neutral">--</span>'
    sign = "+" if value > 0 else ""
    is_good = value > 0 if not invert else value < 0
    cls = "pos" if is_good else "neg"
    return f'<span class="delta {cls}">{sign}{value:.1f}{suffix}</span>'


def _transition_badge(transition: str) -> str:
    if transition == "pass_fail":
        return '<span class="badge-reg">REGRESSION</span>'
    elif transition == "fail_pass":
        return '<span class="badge-imp">IMPROVEMENT</span>'
    elif transition == "pass_pass":
        return '<span class="badge-stable">STABLE PASS</span>'
    else:
        return '<span class="badge-persist">PERSISTENT FAIL</span>'


# ---------------------------------------------------------------------------
# Render: Input page
# ---------------------------------------------------------------------------

def _render_input_page():
    st.markdown("""
    <div class="cmp-hero">
        <h1>Run Comparison</h1>
        <p>Compare two eval runs to see what improved, what regressed, and why</p>
    </div>
    """, unsafe_allow_html=True)

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("##### Run A (Baseline)")
        path_a = st.text_input(
            "Folder path",
            value=st.session_state.get("cmp_path_a", ""),
            key="input_path_a",
            placeholder="Paste full path to baseline run folder...",
        )
    with col2:
        st.markdown("##### Run B (After Changes)")
        path_b = st.text_input(
            "Folder path",
            value=st.session_state.get("cmp_path_b", ""),
            key="input_path_b",
            placeholder="Paste full path to updated run folder...",
        )

    st.markdown("##### What changed between runs?")
    pm_context = st.text_area(
        "Describe changes (system prompt, assertions, model, etc.)",
        value=st.session_state.get("cmp_context", ""),
        key="input_context",
        placeholder="e.g., Updated system prompt to handle edge cases, tightened assertion for data analysis queries...",
        height=100,
    )

    with st.expander("Target Benchmarks (optional)", expanded=False):
        st.caption("Set target pass rates per batch. Leave empty to skip benchmark comparison.")
        target_text = st.text_area(
            "One per line: batch_name: target_percentage",
            value=st.session_state.get("cmp_targets_text", ""),
            key="input_targets",
            placeholder="collection-coverage: 85\nmoonshot-basic: 70",
            height=80,
        )

    if st.button("Compare Runs", type="primary", use_container_width=True):
        if not path_a or not path_b:
            st.error("Please provide paths for both runs.")
            return
        if not os.path.isdir(path_a):
            st.error(f"Run A path does not exist: {path_a}")
            return
        if not os.path.isdir(path_b):
            st.error(f"Run B path does not exist: {path_b}")
            return
        if os.path.normpath(path_a) == os.path.normpath(path_b):
            st.warning("Both paths point to the same folder. Comparison will show no differences.")

        targets = {}
        for line in target_text.strip().splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                try:
                    targets[k.strip()] = float(v.strip())
                except ValueError:
                    pass

        st.session_state["cmp_path_a"] = path_a
        st.session_state["cmp_path_b"] = path_b
        st.session_state["cmp_context"] = pm_context
        st.session_state["cmp_targets"] = targets
        st.session_state["cmp_targets_text"] = target_text
        st.session_state["cmp_ready"] = True
        st.rerun()


# ---------------------------------------------------------------------------
# Render: Comparison overview
# ---------------------------------------------------------------------------

def _render_overview(comp: ComparisonResult, targets: dict[str, float]):
    s = comp.summary

    # Executive summary at the top
    with st.spinner("Generating executive summary..."):
        exec_summary = _generate_executive_summary(comp, targets)
    if exec_summary:
        paras = exec_summary.split("\n\n")
        summary_html = "".join(f"<p>{_h(p.strip())}</p>" for p in paras if p.strip())
        st.markdown(
            f'<div style="background:linear-gradient(135deg,#faf5ff 0%,#f5f3ff 100%);'
            f'border:1px solid #ddd6fe;border-left:5px solid #7c3aed;border-radius:12px;'
            f'padding:1.5rem 1.8rem;margin-bottom:1.5rem;font-size:0.9rem;line-height:1.8;'
            f'color:#1e293b">'
            f'<div style="font-size:0.72rem;font-weight:700;color:#6d28d9;text-transform:uppercase;'
            f'letter-spacing:0.08em;margin-bottom:0.6rem">Executive Summary</div>'
            f'{summary_html}</div>',
            unsafe_allow_html=True,
        )

    if comp.pm_context:
        st.markdown(f"""
        <div class="pm-context-card">
            <strong>Changes Made (PM Context)</strong><br>
            {_h(comp.pm_context)}
        </div>
        """, unsafe_allow_html=True)

    # KPI strip
    st.markdown(f"""
    <div class="cmp-kpi-grid">
        <div class="cmp-kpi">
            <div class="label">Matched Queries</div>
            <div class="value" style="color: #1e293b;">{len(comp.matches)}</div>
        </div>
        <div class="cmp-kpi">
            <div class="label">Run A Pass Rate</div>
            <div class="value" style="color: #64748b;">{s.a_pass_rate:.1f}%</div>
        </div>
        <div class="cmp-kpi">
            <div class="label">Run B Pass Rate</div>
            <div class="value" style="color: #1e293b;">{s.b_pass_rate:.1f}%</div>
        </div>
        <div class="cmp-kpi">
            <div class="label">Pass Rate Delta</div>
            <div class="value">{_delta_html(s.pass_rate_delta, "pp")}</div>
        </div>
        <div class="cmp-kpi">
            <div class="label">Net Change</div>
            <div class="value">{_delta_html(s.fail_pass - s.pass_fail, " queries")}</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # Transition matrix
    st.markdown('<div class="cmp-section-strip">Transition Matrix</div>', unsafe_allow_html=True)
    st.markdown(f"""
    <div class="transition-grid">
        <div class="transition-cell stable">
            <div class="count">{s.pass_pass}</div>
            <div class="label">Stable Passes (Pass → Pass)</div>
        </div>
        <div class="transition-cell regression">
            <div class="count">{s.pass_fail}</div>
            <div class="label">Regressions (Pass → Fail)</div>
        </div>
        <div class="transition-cell improvement">
            <div class="count">{s.fail_pass}</div>
            <div class="label">Improvements (Fail → Pass)</div>
        </div>
        <div class="transition-cell persistent">
            <div class="count">{s.fail_fail}</div>
            <div class="label">Persistent Failures (Fail → Fail)</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # Additional / Removed queries with insights
    if comp.a_only or comp.b_only:
        st.markdown('<div class="cmp-section-strip">Additional & Removed Queries</div>', unsafe_allow_html=True)

        if comp.b_only:
            b_pass = [c for c in comp.b_only if c.passed]
            b_fail = [c for c in comp.b_only if not c.passed]
            b_rate = len(b_pass) / max(len(comp.b_only), 1) * 100
            st.markdown(
                f'<div style="background:#eff6ff;border:1px solid #93c5fd;border-left:4px solid #2563eb;'
                f'border-radius:10px;padding:1rem 1.3rem;margin-bottom:0.8rem;font-size:0.88rem;line-height:1.6">'
                f'<strong style="color:#1e40af">{len(comp.b_only)} new queries in Run B</strong> — '
                f'{len(b_pass)} pass, {len(b_fail)} fail ({b_rate:.0f}% pass rate)'
                f'</div>',
                unsafe_allow_html=True,
            )

            import pandas as pd
            b_intent: dict[str, dict] = {}
            for c in comp.b_only:
                intent = _tag_value(c, "taxonomy.query.intent.primary") or "Unknown"
                b_intent.setdefault(intent, {"p": 0, "f": 0})
                b_intent[intent]["p" if c.passed else "f"] += 1

            if b_intent:
                rows = []
                for intent, d in sorted(b_intent.items(), key=lambda x: -(x[1]["p"] + x[1]["f"])):
                    t = d["p"] + d["f"]
                    rows.append({"Intent": intent, "Total": t, "Passed": d["p"], "Failed": d["f"], "Pass Rate": f"{d['p'] / t * 100:.0f}%"})
                with st.expander(f"New queries breakdown ({len(comp.b_only)} queries)"):
                    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
                    st.markdown("---")
                    for c in comp.b_only:
                        status = "PASS" if c.passed else "FAIL"
                        err_info = f" | Errors: {', '.join(c.error_types[:3])}" if c.error_types else ""
                        st.markdown(f"- `[{c.batch}] Q{c.query_index}` **{status}** ({c.step_count} steps, {c.error_count} errors{err_info}): {c.query_text[:100]}")

        if comp.a_only:
            a_pass = [c for c in comp.a_only if c.passed]
            a_fail = [c for c in comp.a_only if not c.passed]
            st.markdown(
                f'<div style="background:#fef2f2;border:1px solid #fca5a5;border-left:4px solid #dc2626;'
                f'border-radius:10px;padding:1rem 1.3rem;margin-bottom:0.8rem;font-size:0.88rem;line-height:1.6">'
                f'<strong style="color:#991b1b">{len(comp.a_only)} queries removed from Run B</strong> — '
                f'{len(a_pass)} were passing, {len(a_fail)} were failing in Run A'
                f'</div>',
                unsafe_allow_html=True,
            )
            with st.expander(f"Removed queries ({len(comp.a_only)} queries)"):
                for c in comp.a_only:
                    status = "PASS" if c.passed else "FAIL"
                    st.markdown(f"- `[{c.batch}] Q{c.query_index}` **{status}**: {c.query_text[:100]}")

    # Per-batch breakdown
    st.markdown('<div class="cmp-section-strip">Per-Batch Breakdown</div>', unsafe_allow_html=True)
    batch_stats: dict[str, dict] = {}
    for m in comp.matches:
        b = m.batch
        if b not in batch_stats:
            batch_stats[b] = {"a_pass": 0, "a_total": 0, "b_pass": 0, "b_total": 0, "reg": 0, "imp": 0}
        batch_stats[b]["a_total"] += 1
        batch_stats[b]["b_total"] += 1
        if m.case_a.passed:
            batch_stats[b]["a_pass"] += 1
        if m.case_b.passed:
            batch_stats[b]["b_pass"] += 1
        if m.transition == "pass_fail":
            batch_stats[b]["reg"] += 1
        elif m.transition == "fail_pass":
            batch_stats[b]["imp"] += 1

    rows = []
    for b in sorted(batch_stats):
        d = batch_stats[b]
        a_r = d["a_pass"] / max(d["a_total"], 1) * 100
        b_r = d["b_pass"] / max(d["b_total"], 1) * 100
        delta = b_r - a_r
        target = targets.get(b)
        gap = b_r - target if target else None
        rows.append({
            "Batch": b,
            "Queries": d["a_total"],
            "A Pass Rate": f"{a_r:.1f}%",
            "B Pass Rate": f"{b_r:.1f}%",
            "Delta": f"{delta:+.1f}pp",
            "Regressions": d["reg"],
            "Improvements": d["imp"],
            "Target": f"{target:.0f}%" if target else "--",
            "Gap": f"{gap:+.1f}pp" if gap is not None else "--",
        })

    if rows:
        import pandas as pd
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    # --- Trajectory & Error Evolution ---
    st.markdown('<div class="cmp-section-strip">Trajectory & Error Evolution</div>', unsafe_allow_html=True)

    # Failure pattern shifts
    traj_a_ov: Counter = Counter()
    traj_b_ov: Counter = Counter()
    for m in comp.matches:
        if m.cls_a:
            for tc in m.cls_a.trajectory_categories:
                traj_a_ov[tc] += 1
        if m.cls_b:
            for tc in m.cls_b.trajectory_categories:
                traj_b_ov[tc] += 1

    all_traj_cats = sorted(set(traj_a_ov.keys()) | set(traj_b_ov.keys()))
    if all_traj_cats:
        st.markdown("##### Failure Pattern Shifts")
        import pandas as pd
        traj_rows = []
        for cat in all_traj_cats:
            a_c = traj_a_ov.get(cat, 0)
            b_c = traj_b_ov.get(cat, 0)
            traj_rows.append({"Category": cat, "Run A": a_c, "Run B": b_c, "Delta": f"{b_c - a_c:+d}"})
        st.dataframe(pd.DataFrame(traj_rows), use_container_width=True, hide_index=True)

    # Error type evolution
    err_a_ov: Counter = Counter()
    err_b_ov: Counter = Counter()
    for m in comp.matches:
        for et in m.case_a.error_types:
            err_a_ov[et] += 1
        for et in m.case_b.error_types:
            err_b_ov[et] += 1

    all_errs = sorted(set(err_a_ov.keys()) | set(err_b_ov.keys()))
    if all_errs:
        st.markdown("##### Error Type Evolution")
        new_errs = [et for et in all_errs if err_a_ov.get(et, 0) == 0 and err_b_ov.get(et, 0) > 0]
        gone_errs = [et for et in all_errs if err_b_ov.get(et, 0) == 0 and err_a_ov.get(et, 0) > 0]

        if new_errs:
            st.markdown(
                f'<div style="background:linear-gradient(135deg,#fef2f2,#fee2e2);border-left:4px solid #dc2626;'
                f'border-radius:10px;padding:0.8rem 1.1rem;margin-bottom:0.6rem;font-size:0.88rem;color:#7f1d1d">'
                f'New error types in Run B: <strong>{", ".join(new_errs)}</strong></div>',
                unsafe_allow_html=True,
            )
        if gone_errs:
            st.markdown(
                f'<div style="background:linear-gradient(135deg,#ecfdf5,#d1fae5);border-left:4px solid #059669;'
                f'border-radius:10px;padding:0.8rem 1.1rem;margin-bottom:0.6rem;font-size:0.88rem;color:#064e3b">'
                f'Error types eliminated in Run B: <strong>{", ".join(gone_errs)}</strong></div>',
                unsafe_allow_html=True,
            )

        import pandas as pd
        err_rows = []
        for et in all_errs:
            a_c = err_a_ov.get(et, 0)
            b_c = err_b_ov.get(et, 0)
            err_rows.append({"Error Type": et, "Run A": a_c, "Run B": b_c, "Delta": f"{b_c - a_c:+d}"})
        st.dataframe(pd.DataFrame(err_rows), use_container_width=True, hide_index=True)

    # Execution metrics
    st.markdown("##### Execution Metrics")
    matched = comp.matches
    if matched:
        import pandas as pd
        avg_steps_a = sum(m.case_a.step_count for m in matched) / len(matched)
        avg_steps_b = sum(m.case_b.step_count for m in matched) / len(matched)
        avg_err_a = sum(m.case_a.error_count for m in matched) / len(matched)
        avg_err_b = sum(m.case_b.error_count for m in matched) / len(matched)
        avg_time_a = sum(m.case_a.execution_time_sec for m in matched) / len(matched)
        avg_time_b = sum(m.case_b.execution_time_sec for m in matched) / len(matched)
        avg_scripts_a = sum(m.case_a.script_exec_count for m in matched) / len(matched)
        avg_scripts_b = sum(m.case_b.script_exec_count for m in matched) / len(matched)
        total_errs_a = sum(m.case_a.error_count for m in matched)
        total_errs_b = sum(m.case_b.error_count for m in matched)

        met_rows = [
            {"Metric": "Avg Steps", "Run A": f"{avg_steps_a:.1f}", "Run B": f"{avg_steps_b:.1f}", "Delta": f"{avg_steps_b - avg_steps_a:+.1f}"},
            {"Metric": "Avg Scripts", "Run A": f"{avg_scripts_a:.1f}", "Run B": f"{avg_scripts_b:.1f}", "Delta": f"{avg_scripts_b - avg_scripts_a:+.1f}"},
            {"Metric": "Avg Errors", "Run A": f"{avg_err_a:.1f}", "Run B": f"{avg_err_b:.1f}", "Delta": f"{avg_err_b - avg_err_a:+.1f}"},
            {"Metric": "Total Errors", "Run A": str(total_errs_a), "Run B": str(total_errs_b), "Delta": f"{total_errs_b - total_errs_a:+d}"},
            {"Metric": "Avg Time (s)", "Run A": f"{avg_time_a:.1f}", "Run B": f"{avg_time_b:.1f}", "Delta": f"{avg_time_b - avg_time_a:+.1f}"},
        ]
        st.dataframe(pd.DataFrame(met_rows), use_container_width=True, hide_index=True)

    # Persistent failure pattern changes
    persistent = [m for m in comp.matches if m.transition == "fail_fail"]
    if persistent:
        pattern_changed = []
        for m in persistent:
            cats_a = set(m.cls_a.trajectory_categories) if m.cls_a else set()
            cats_b = set(m.cls_b.trajectory_categories) if m.cls_b else set()
            if cats_a != cats_b:
                pattern_changed.append(m)

        if pattern_changed:
            st.markdown("##### Persistent Failures — Changed Pattern")
            st.caption(f"{len(pattern_changed)} of {len(persistent)} persistent failures shifted failure mode")
            for m in pattern_changed:
                cats_a_str = ", ".join(m.cls_a.trajectory_categories) if m.cls_a else "Unclassified"
                cats_b_str = ", ".join(m.cls_b.trajectory_categories) if m.cls_b else "Unclassified"
                st.markdown(
                    f'<div style="background:#fefce8;border:1px solid #fde68a;border-radius:8px;'
                    f'padding:0.6rem 0.9rem;margin-bottom:0.4rem;font-size:0.85rem">'
                    f'<strong>[{_h(m.batch)}] Q{_h(m.query_index)}</strong>: '
                    f'{_h(cats_a_str)} → {_h(cats_b_str)}'
                    f'</div>',
                    unsafe_allow_html=True,
                )


# ---------------------------------------------------------------------------
# Render: Transition cards (regressions, improvements, persistent)
# ---------------------------------------------------------------------------

def _render_transition_section(
    comp: ComparisonResult,
    transition_type: str,
    title: str,
    css_class: str,
    targets: dict[str, float],
):
    cases = [m for m in comp.matches if m.transition == transition_type]
    if not cases:
        st.info(f"No {title.lower()} found.")
        return

    st.markdown(f'<div class="cmp-section-strip">{title} ({len(cases)})</div>', unsafe_allow_html=True)

    analyses = _analyze_transitions(
        comp.matches, transition_type, comp.pm_context, comp.run_a_path, comp.run_b_path
    )
    analysis_lookup = {(a.get("batch", ""), a.get("query_index", "")): a for a in analyses}

    for m in cases:
        ev_a = extract_grader_evidence(m.case_a)
        ev_b = extract_grader_evidence(m.case_b)
        analysis = analysis_lookup.get((m.batch, m.query_index), {})

        with st.expander(f"[{m.batch}] Q{m.query_index}: {m.query_text[:100]}"):
            st.markdown(f"""
            <div class="cmp-card {css_class}">
                <div style="margin-bottom: 0.5rem;">
                    {_transition_badge(m.transition)}
                    <span style="font-size: 0.8rem; color: #64748b; margin-left: 0.5rem;">[{_h(m.batch)}] Query {_h(m.query_index)}</span>
                </div>
                <div style="font-size: 0.92rem; color: #1e293b; margin-bottom: 0.8rem; font-weight: 500;">
                    {_h(m.query_text)}
                </div>
                <div class="cmp-side">
                    <div class="cmp-side-col run-a">
                        <div class="cmp-side-label" style="color: #0369a1;">Run A {'(PASSED)' if m.case_a.passed else '(FAILED)'}</div>
                        <b>Steps:</b> {m.case_a.step_count} | <b>Errors:</b> {m.case_a.error_count} | <b>Time:</b> {m.case_a.execution_time_sec:.1f}s<br>
                        {'<b>Error types:</b> ' + _h(', '.join(m.case_a.error_types[:5])) + '<br>' if m.case_a.error_types else ''}
                        {'<b>Grader:</b> ' + _h(ev_a.grader_message[:200]) if ev_a.grader_message else ''}
                    </div>
                    <div class="cmp-side-col run-b {'passed' if m.case_b.passed else ''}">
                        <div class="cmp-side-label" style="color: {'#166534' if m.case_b.passed else '#991b1b'};">Run B {'(PASSED)' if m.case_b.passed else '(FAILED)'}</div>
                        <b>Steps:</b> {m.case_b.step_count} | <b>Errors:</b> {m.case_b.error_count} | <b>Time:</b> {m.case_b.execution_time_sec:.1f}s<br>
                        {'<b>Error types:</b> ' + _h(', '.join(m.case_b.error_types[:5])) + '<br>' if m.case_b.error_types else ''}
                        {'<b>Grader:</b> ' + _h(ev_b.grader_message[:200]) if ev_b.grader_message else ''}
                    </div>
                </div>
            """, unsafe_allow_html=True)

            # Classification badges
            badges = ""
            if m.cls_a:
                for tc in m.cls_a.trajectory_categories:
                    badges += f'<span style="display:inline-block;background:#64748b;color:white;font-size:0.68rem;padding:0.12rem 0.5rem;border-radius:20px;margin:0.1rem;">A: {_h(tc)}</span> '
            if m.cls_b:
                for tc in m.cls_b.trajectory_categories:
                    badges += f'<span style="display:inline-block;background:#1e293b;color:white;font-size:0.68rem;padding:0.12rem 0.5rem;border-radius:20px;margin:0.1rem;">B: {_h(tc)}</span> '
            if badges:
                st.markdown(badges, unsafe_allow_html=True)

            # LLM analysis
            if analysis.get("analysis"):
                st.markdown(f"""
                <div class="cmp-analysis">
                    <strong style="font-size: 0.75rem; color: #64748b; text-transform: uppercase;">Analysis</strong><br>
                    {_h(analysis['analysis'])}
                    {'<br><strong>Likely cause:</strong> ' + _h(analysis.get('likely_cause', '')) if analysis.get('likely_cause') else ''}
                </div>
                """, unsafe_allow_html=True)

            st.markdown("</div>", unsafe_allow_html=True)

            # Trajectory timelines
            col1, col2 = st.columns(2)
            with col1:
                st.markdown("**Run A Trajectory**")
                _render_mini_timeline(m.case_a)
            with col2:
                st.markdown("**Run B Trajectory**")
                _render_mini_timeline(m.case_b)


def _render_mini_timeline(case: ParsedEvalCase):
    lines = []
    for s in case.steps:
        if s.step_type == "ScriptExecution":
            lines.append(f'<div class="step-item script">[{s.step_index}] Script: {_h(s.script_preview[:60])}</div>')
        elif s.step_type == "ScriptResponse":
            if s.error_type:
                lines.append(f'<div class="step-item error">[{s.step_index}] ERROR ({_h(s.error_type)}): {_h(s.result[:80])}</div>')
            else:
                lines.append(f'<div class="step-item">[{s.step_index}] OK: {_h(s.result[:80])}</div>')
        elif s.step_type == "Assistant":
            lines.append(f'<div class="step-item assistant">[{s.step_index}] Assistant: {_h(s.text[:60])}</div>')
    if lines:
        st.markdown(f'<div class="step-timeline">{"".join(lines)}</div>', unsafe_allow_html=True)
    else:
        st.caption("No trajectory steps")


# ---------------------------------------------------------------------------
# Render: Metric deltas
# ---------------------------------------------------------------------------

def _render_metric_deltas(comp: ComparisonResult, targets: dict[str, float]):
    import pandas as pd

    st.markdown('<div class="cmp-section-strip">Metric Deltas by Taxonomy</div>', unsafe_allow_html=True)

    # By Intent
    st.markdown("##### By Intent")
    intent_stats: dict[str, dict] = {}
    for m in comp.matches:
        intent = _tag_value(m.case_a, "taxonomy.query.intent.primary") or "Unknown"
        if intent not in intent_stats:
            intent_stats[intent] = {"a_pass": 0, "a_total": 0, "b_pass": 0, "b_total": 0, "reg": 0, "imp": 0}
        intent_stats[intent]["a_total"] += 1
        intent_stats[intent]["b_total"] += 1
        if m.case_a.passed:
            intent_stats[intent]["a_pass"] += 1
        if m.case_b.passed:
            intent_stats[intent]["b_pass"] += 1
        if m.transition == "pass_fail":
            intent_stats[intent]["reg"] += 1
        elif m.transition == "fail_pass":
            intent_stats[intent]["imp"] += 1

    if intent_stats:
        rows = []
        for intent in sorted(intent_stats, key=lambda x: -(intent_stats[x]["a_total"])):
            d = intent_stats[intent]
            a_r = d["a_pass"] / max(d["a_total"], 1) * 100
            b_r = d["b_pass"] / max(d["b_total"], 1) * 100
            rows.append({
                "Intent": intent, "Queries": d["a_total"],
                "A Pass Rate": f"{a_r:.1f}%", "B Pass Rate": f"{b_r:.1f}%",
                "Delta": f"{b_r - a_r:+.1f}pp",
                "Regressions": d["reg"], "Improvements": d["imp"],
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    # By Specificity
    st.markdown("##### By Specificity")
    spec_stats: dict[str, dict] = {}
    for m in comp.matches:
        spec = _tag_value(m.case_a, "taxonomy.query.specificity") or "Unknown"
        if spec not in spec_stats:
            spec_stats[spec] = {"a_pass": 0, "a_total": 0, "b_pass": 0, "b_total": 0, "reg": 0, "imp": 0}
        spec_stats[spec]["a_total"] += 1
        spec_stats[spec]["b_total"] += 1
        if m.case_a.passed:
            spec_stats[spec]["a_pass"] += 1
        if m.case_b.passed:
            spec_stats[spec]["b_pass"] += 1
        if m.transition == "pass_fail":
            spec_stats[spec]["reg"] += 1
        elif m.transition == "fail_pass":
            spec_stats[spec]["imp"] += 1

    if spec_stats:
        rows = []
        spec_order = ["Very Well-Specified", "Well-Specified", "Reasonably Specified", "Ambiguous", "Very Ambiguous"]
        ordered = [s for s in spec_order if s in spec_stats] + [s for s in sorted(spec_stats) if s not in spec_order]
        for spec in ordered:
            d = spec_stats[spec]
            a_r = d["a_pass"] / max(d["a_total"], 1) * 100
            b_r = d["b_pass"] / max(d["b_total"], 1) * 100
            rows.append({
                "Specificity": spec, "Queries": d["a_total"],
                "A Pass Rate": f"{a_r:.1f}%", "B Pass Rate": f"{b_r:.1f}%",
                "Delta": f"{b_r - a_r:+.1f}pp",
                "Regressions": d["reg"], "Improvements": d["imp"],
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    # By Complexity
    st.markdown("##### By Complexity")
    cplx_stats: dict[str, dict] = {}
    for m in comp.matches:
        cplx = _tag_value(m.case_a, "taxonomy.query.intent.complexity") or "Unknown"
        if cplx not in cplx_stats:
            cplx_stats[cplx] = {"a_pass": 0, "a_total": 0, "b_pass": 0, "b_total": 0, "reg": 0, "imp": 0}
        cplx_stats[cplx]["a_total"] += 1
        cplx_stats[cplx]["b_total"] += 1
        if m.case_a.passed:
            cplx_stats[cplx]["a_pass"] += 1
        if m.case_b.passed:
            cplx_stats[cplx]["b_pass"] += 1
        if m.transition == "pass_fail":
            cplx_stats[cplx]["reg"] += 1
        elif m.transition == "fail_pass":
            cplx_stats[cplx]["imp"] += 1

    if cplx_stats:
        rows = []
        cplx_order = ["L1 Actions", "L2 Feature Usage", "L3 Multi-step Tasks", "L4 Workflows"]
        ordered = [c for c in cplx_order if c in cplx_stats] + [c for c in sorted(cplx_stats) if c not in cplx_order]
        for cplx in ordered:
            d = cplx_stats[cplx]
            a_r = d["a_pass"] / max(d["a_total"], 1) * 100
            b_r = d["b_pass"] / max(d["b_total"], 1) * 100
            rows.append({
                "Complexity": cplx, "Queries": d["a_total"],
                "A Pass Rate": f"{a_r:.1f}%", "B Pass Rate": f"{b_r:.1f}%",
                "Delta": f"{b_r - a_r:+.1f}pp",
                "Regressions": d["reg"], "Improvements": d["imp"],
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    # Outcome category shifts
    st.markdown("##### Outcome Category Shifts")
    outc_a: Counter = Counter()
    outc_b: Counter = Counter()
    for m in comp.matches:
        if m.cls_a and m.cls_a.outcome_category:
            outc_a[m.cls_a.outcome_category] += 1
        if m.cls_b and m.cls_b.outcome_category:
            outc_b[m.cls_b.outcome_category] += 1

    all_outc = sorted(set(outc_a.keys()) | set(outc_b.keys()))
    if all_outc:
        rows = []
        for cat in all_outc:
            a_c = outc_a.get(cat, 0)
            b_c = outc_b.get(cat, 0)
            rows.append({"Outcome Category": cat, "Run A": a_c, "Run B": b_c, "Delta": f"{b_c - a_c:+d}"})
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# Render: LLM narrative
# ---------------------------------------------------------------------------

def _render_narrative(comp: ComparisonResult, targets: dict[str, float]):
    st.markdown('<div class="cmp-section-strip">LLM Comparison Insights</div>', unsafe_allow_html=True)

    narrative = _generate_comparison_narrative(comp, targets)
    st.markdown(f'<div class="cmp-narrative">{_h(narrative)}</div>', unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

CMP_PAGES = ["Load Runs", "Overview", "Regressions", "Improvements", "Persistent Failures", "Metric Deltas", "Insights"]


def render_comparison_mode():
    _cmp_css = _COMPARISON_DARK_CSS if st.session_state.get("dark_mode", False) else _COMPARISON_CSS
    st.markdown(_cmp_css, unsafe_allow_html=True)

    if not st.session_state.get("cmp_ready"):
        _render_input_page()
        return

    # Navigation
    for p in CMP_PAGES:
        if p == "Load Runs":
            continue
        is_active = p == st.session_state.get("cmp_page", "Overview")
        btn_type = "primary" if is_active else "secondary"
        if st.sidebar.button(p, key=f"cmp_nav_{p}", use_container_width=True, type=btn_type):
            st.session_state["cmp_page"] = p
            st.rerun()

    st.sidebar.markdown("---")
    if st.sidebar.button("Change Runs", use_container_width=True):
        st.session_state["cmp_ready"] = False
        st.rerun()

    if st.sidebar.button("Clear Cache & Reload", use_container_width=True):
        _load_run.clear()
        cache_path = _llm_cache_path(
            st.session_state.get("cmp_path_a", ""),
            st.session_state.get("cmp_path_b", ""),
        )
        if os.path.exists(cache_path):
            os.remove(cache_path)
        st.rerun()

    # Load data
    path_a = st.session_state["cmp_path_a"]
    path_b = st.session_state["cmp_path_b"]
    pm_context = st.session_state.get("cmp_context", "")
    targets = st.session_state.get("cmp_targets", {})

    gem_key = os.environ.get("GEMINI_API_KEY", "")
    groq_key = os.environ.get("GROQ_API_KEY", "")

    try:
        batches_a, cases_a, cls_a = _load_run(path_a, gem_key, groq_key)
        batches_b, cases_b, cls_b = _load_run(path_b, gem_key, groq_key)
    except Exception as e:
        st.error(f"Failed to load runs: {e}")
        if st.button("Go back to input"):
            st.session_state["cmp_ready"] = False
            st.rerun()
        return

    comp = _match_queries(cases_a, cases_b, cls_a, cls_b, path_a, path_b, pm_context)

    # Hero banner
    s = comp.summary
    st.markdown(f"""
    <div class="cmp-hero">
        <h1>Run Comparison</h1>
        <p>{len(comp.matches)} matched queries | {s.pass_fail} regressions | {s.fail_pass} improvements | {s.pass_rate_delta:+.1f}pp pass rate change</p>
    </div>
    """, unsafe_allow_html=True)

    page = st.session_state.get("cmp_page", "Overview")

    if page == "Overview":
        _render_overview(comp, targets)
    elif page == "Regressions":
        _render_transition_section(comp, "pass_fail", "Regressions (Pass → Fail)", "regression", targets)
    elif page == "Improvements":
        _render_transition_section(comp, "fail_pass", "Improvements (Fail → Pass)", "improvement", targets)
    elif page == "Persistent Failures":
        _render_transition_section(comp, "fail_fail", "Persistent Failures (Fail → Fail)", "persistent", targets)
    elif page == "Metric Deltas":
        _render_metric_deltas(comp, targets)
    elif page == "Insights":
        _render_narrative(comp, targets)
