"""Run Evolution module for Eval Insights Platform.

Tracks how eval results change across 2+ runs uploaded incrementally.
Identifies stable passes/failures, improvements, regressions, and flaky queries.
"""
from __future__ import annotations

import hashlib
import html
import io
import json
import os
import tempfile
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import streamlit as st

from trajectory_parser import auto_discover_batches, ParsedEvalCase, describe_script
from failure_classifier import (
    classify_all,
    ClassificationResult,
    _call_llm,
    extract_grader_evidence,
    TRAJECTORY_CATEGORIES,
    OUTCOME_CATEGORIES,
)
from comparison import _load_run


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class EvolutionRun:
    run_index: int
    label: str
    description: str
    path: str
    batches: dict[str, list[ParsedEvalCase]]
    all_cases: list[ParsedEvalCase]
    classifications: list[ClassificationResult]
    case_lookup: dict[tuple[str, str], ParsedEvalCase] = field(default_factory=dict)
    cls_lookup: dict[tuple[str, str], ClassificationResult] = field(default_factory=dict)

    def __post_init__(self):
        if not self.case_lookup:
            self.case_lookup = {(c.batch, c.query_index): c for c in self.all_cases}
        if not self.cls_lookup:
            self.cls_lookup = {(c.batch, c.query_index): c for c in self.classifications}


@dataclass
class QueryEvolution:
    batch: str
    query_index: str
    query_text: str
    cases: list[Optional[ParsedEvalCase]]
    outcomes: list[Optional[bool]]
    transition_chain: str
    category: str


@dataclass
class EvolutionSummary:
    total_queries: int
    run_labels: list[str]
    pass_rates: list[float]
    pass_counts: list[int]
    total_counts: list[int]
    stable_passes: int
    stable_failures: int
    improved: int
    regressed: int
    flaky: int
    not_comparable: int
    batch_pass_rates: dict[str, list[Optional[float]]]


def _h(text):
    return html.escape(str(text))


# ---------------------------------------------------------------------------
# Theme system
# ---------------------------------------------------------------------------

def _is_dark() -> bool:
    return st.session_state.get("dark_mode", False)


_LIGHT_CSS = """
<style>
.evo-hero {
    background: linear-gradient(135deg, #0f172a 0%, #1e3a5f 50%, #1a365d 100%);
    color: white; padding: 2.2rem 2.5rem; border-radius: 14px; margin-bottom: 1.5rem;
}
.evo-hero h1 { font-size: 1.8rem; font-weight: 800; margin: 0 0 0.3rem 0; color: white; }
.evo-hero p { font-size: 0.92rem; color: #93c5fd; margin: 0; }
.evo-kpi-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 0.8rem; margin-bottom: 1.2rem; }
.evo-kpi {
    background: white; border: 1px solid #e2e8f0; border-radius: 12px;
    padding: 0.9rem 1.1rem; text-align: center;
}
.evo-kpi .label { font-size: 0.7rem; font-weight: 600; color: #64748b; text-transform: uppercase; letter-spacing: 0.08em; }
.evo-kpi .value { font-size: 1.6rem; font-weight: 700; margin-top: 0.2rem; }
.evo-kpi .value.red { color: #dc2626; }
.evo-kpi .value.green { color: #059669; }
.evo-kpi .value.blue { color: #2563eb; }
.evo-kpi .value.amber { color: #d97706; }
.evo-kpi .value.slate { color: #334155; }
.evo-section-strip {
    background: linear-gradient(90deg, #1e3a5f 0%, #1a365d 100%);
    color: white; padding: 0.7rem 1.1rem; border-radius: 8px;
    font-weight: 600; font-size: 0.92rem; margin: 1.2rem 0 1rem 0;
}
.evo-card {
    background: white; border: 1px solid #e2e8f0; border-radius: 12px;
    padding: 1.2rem 1.4rem; margin-bottom: 0.8rem;
}
.evo-card.improved { border-left: 4px solid #059669; }
.evo-card.regressed { border-left: 4px solid #dc2626; }
.evo-card.stable-pass { border-left: 4px solid #059669; }
.evo-card.stable-fail { border-left: 4px solid #d97706; }
.evo-card.flaky { border-left: 4px solid #7c3aed; }
.evo-card .q-id { color: #1e293b; }
.evo-card .q-text { color: #64748b; }
.evo-chain {
    display: inline-flex; gap: 2px; font-family: monospace; font-size: 0.85rem; font-weight: 700;
}
.evo-chain .p { color: #059669; }
.evo-chain .f { color: #dc2626; }
.evo-chain .q { color: #94a3b8; }
.evo-chain .sep { color: #cbd5e1; }
.evo-run-card {
    background: white; border: 1px solid #e2e8f0; border-radius: 10px;
    padding: 1rem 1.2rem; margin-bottom: 0.6rem;
    display: flex; align-items: center; gap: 1rem;
}
.evo-run-card .run-num {
    background: #1e3a5f; color: white; font-weight: 700;
    width: 32px; height: 32px; border-radius: 50%; display: flex;
    align-items: center; justify-content: center; font-size: 0.85rem; flex-shrink: 0;
}
.evo-run-card .run-info { flex: 1; }
.evo-run-card .run-label { font-weight: 700; color: #1e293b; font-size: 0.92rem; }
.evo-run-card .run-desc { font-size: 0.8rem; color: #64748b; margin-top: 0.15rem; }
.evo-run-card .run-stats { font-size: 0.78rem; color: #94a3b8; margin-top: 0.15rem; }
.evo-narrative {
    background: white; border: 1px solid #e2e8f0; border-radius: 12px;
    padding: 1.5rem 1.8rem; font-size: 0.9rem; line-height: 1.8; color: #334155;
}
.evo-insight-card {
    background: linear-gradient(135deg, #f0f9ff 0%, #e0f2fe 100%);
    border: 1px solid #7dd3fc; border-left: 4px solid #0284c7;
    border-radius: 10px; padding: 1.1rem 1.3rem; margin: 0.8rem 0;
    font-size: 0.88rem; line-height: 1.7; color: #0c4a6e;
}
.evo-insight-card strong { color: #0369a1; }
.evo-flip-indicator { font-size: 0.78rem; color: #64748b; margin-top: 0.3rem; }
.evo-flip-indicator strong { color: #2563eb; }
.evo-error-info { font-size: 0.78rem; color: #94a3b8; margin-top: 0.3rem; }
.evo-detail-col {
    border-radius: 8px; padding: 0.8rem 1rem; font-size: 0.82rem; line-height: 1.6; margin-bottom: 0.5rem;
}
.evo-detail-col.run-pass { background: #f0fdf4; border: 1px solid #bbf7d0; }
.evo-detail-col.run-fail { background: #fef2f2; border: 1px solid #fecaca; }
.evo-detail-col.run-absent { background: #f8fafc; border: 1px dashed #cbd5e1; color: #94a3b8; text-align: center; padding: 2rem 1rem; }
.evo-detail-label {
    font-size: 0.68rem; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.06em; margin-bottom: 0.4rem;
}
.evo-detail-label.pass { color: #059669; }
.evo-detail-label.fail { color: #dc2626; }
.evo-traj-bar {
    display: flex; gap: 1rem; font-size: 0.78rem; color: #64748b;
    background: white; border: 1px solid #e2e8f0; border-radius: 6px;
    padding: 0.4rem 0.7rem; margin: 0.4rem 0;
}
.evo-traj-bar .num { font-weight: 700; color: #1e293b; }
.evo-pattern-box {
    border-radius: 8px; padding: 0.7rem 0.9rem; margin: 0.6rem 0; font-size: 0.82rem; line-height: 1.6;
}
.evo-pattern-box.same { background: #f0fdf4; border: 1px solid #bbf7d0; border-left: 3px solid #059669; color: #065f46; }
.evo-pattern-box.diff { background: #fffbeb; border: 1px solid #fcd34d; border-left: 3px solid #d97706; color: #92400e; }
.evo-pattern-box.absent { background: #f8fafc; border: 1px solid #e2e8f0; border-left: 3px solid #94a3b8; color: #64748b; }
.evo-grader {
    background: white; border: 1px solid #e2e8f0; border-radius: 6px;
    padding: 0.5rem 0.7rem; margin: 0.4rem 0; font-size: 0.78rem;
}
.evo-grader .expected { color: #059669; }
.evo-grader .actual { color: #dc2626; }
.evo-grader-label { font-size: 0.65rem; font-weight: 700; text-transform: uppercase; color: #64748b; letter-spacing: 0.05em; }
.evo-cat-badge {
    display: inline-block; background: #f1f5f9; color: #334155;
    font-size: 0.7rem; font-weight: 600; padding: 0.15rem 0.5rem;
    border-radius: 20px; margin-right: 0.2rem; margin-top: 0.3rem;
}
.evo-why { font-size: 0.78rem; color: #64748b; margin-top: 0.3rem; line-height: 1.5; }
.evo-step-timeline { border-left: 3px solid #e2e8f0; padding-left: 0.8rem; margin: 0.4rem 0; }
.evo-step { padding: 0.2rem 0; font-size: 0.75rem; color: #64748b; position: relative; }
.evo-step::before {
    content: ''; position: absolute; left: -1.15rem; top: 0.45rem;
    width: 6px; height: 6px; border-radius: 50%; background: #94a3b8;
}
.evo-step.error::before { background: #dc2626; }
.evo-step.script::before { background: #2563eb; }
.evo-step.assistant::before { background: #7c3aed; }
.evo-err-badge {
    display: inline-block; background: #fef2f2; color: #dc2626;
    font-size: 0.68rem; font-weight: 600; padding: 0.1rem 0.4rem;
    border-radius: 10px; margin-right: 0.2rem; margin-top: 0.2rem;
}
.agg-table { width:100%; border-collapse:collapse; font-size:0.88rem; }
.agg-table th { background:#1f4e79; color:#fff; padding:8px 12px; text-align:left; font-weight:600; position:sticky; top:0; z-index:1; }
.agg-table td { padding:7px 12px; border-bottom:1px solid #dee2e6; }
.agg-table tr:hover td { background:#f0f4f8; }
.stat-card-wrap { overflow:visible; position:relative; margin-bottom:16px; }
.evo-row-detail { background:#f8fafc; }
.evo-row-detail summary { user-select:none; }
.evo-row-detail[open] { background:#f0f4f8; }
</style>
"""

_DARK_CSS = """
<style>
/* Override Streamlit native elements for dark mode */
.stApp, [data-testid="stAppViewContainer"] { background-color: #0d1117 !important; color: #e6edf3 !important; }
section[data-testid="stSidebar"] { background: #161b22 !important; }
section[data-testid="stSidebar"] * { color: #e6edf3 !important; }
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] { color: #e6edf3 !important; }
.stTextInput > div > div > input { background: #0d1117 !important; color: #e6edf3 !important; border-color: #30363d !important; }
.stTextArea > div > div > textarea { background: #0d1117 !important; color: #e6edf3 !important; border-color: #30363d !important; }
.stSelectbox > div > div { background: #0d1117 !important; color: #e6edf3 !important; }
[data-testid="stFileUploader"] { background: #161b22 !important; border-color: #30363d !important; }
[data-testid="stFileUploader"] * { color: #e6edf3 !important; }
.stDataFrame { background: #161b22 !important; }
[data-testid="stExpander"] { background: #161b22 !important; border-color: #30363d !important; }
[data-testid="stExpander"] * { color: #e6edf3 !important; }
.stMarkdown, .stMarkdown p, h1, h2, h3 { color: #e6edf3 !important; }
.stSpinner > div { color: #8b949e !important; }
hr { border-color: #30363d !important; }
.stAlert { background: #161b22 !important; color: #e6edf3 !important; }
.evo-hero {
    background: linear-gradient(135deg, #0d1117 0%, #161b22 50%, #1a1a2e 100%);
    color: #e6edf3; padding: 2.2rem 2.5rem; border-radius: 14px; margin-bottom: 1.5rem;
    border: 1px solid #30363d;
}
.evo-hero h1 { font-size: 1.8rem; font-weight: 800; margin: 0 0 0.3rem 0; color: #e6edf3; }
.evo-hero p { font-size: 0.92rem; color: #8b949e; margin: 0; }
.evo-kpi-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 0.8rem; margin-bottom: 1.2rem; }
.evo-kpi {
    background: #161b22; border: 1px solid #30363d; border-radius: 12px;
    padding: 0.9rem 1.1rem; text-align: center;
}
.evo-kpi .label { font-size: 0.7rem; font-weight: 600; color: #8b949e; text-transform: uppercase; letter-spacing: 0.08em; }
.evo-kpi .value { font-size: 1.6rem; font-weight: 700; margin-top: 0.2rem; }
.evo-kpi .value.red { color: #f85149; }
.evo-kpi .value.green { color: #3fb950; }
.evo-kpi .value.blue { color: #58a6ff; }
.evo-kpi .value.amber { color: #d29922; }
.evo-kpi .value.slate { color: #e6edf3; }
.evo-section-strip {
    background: linear-gradient(90deg, #161b22 0%, #21262d 100%);
    color: #e6edf3; padding: 0.7rem 1.1rem; border-radius: 8px;
    font-weight: 600; font-size: 0.92rem; margin: 1.2rem 0 1rem 0;
    border: 1px solid #30363d;
}
.evo-card {
    background: #161b22; border: 1px solid #30363d; border-radius: 12px;
    padding: 1.2rem 1.4rem; margin-bottom: 0.8rem;
}
.evo-card.improved { border-left: 4px solid #3fb950; }
.evo-card.regressed { border-left: 4px solid #f85149; }
.evo-card.stable-pass { border-left: 4px solid #3fb950; }
.evo-card.stable-fail { border-left: 4px solid #d29922; }
.evo-card.flaky { border-left: 4px solid #bc8cff; }
.evo-card .q-id { color: #e6edf3; }
.evo-card .q-text { color: #8b949e; }
.evo-chain {
    display: inline-flex; gap: 2px; font-family: monospace; font-size: 0.85rem; font-weight: 700;
}
.evo-chain .p { color: #3fb950; }
.evo-chain .f { color: #f85149; }
.evo-chain .q { color: #484f58; }
.evo-chain .sep { color: #30363d; }
.evo-run-card {
    background: #161b22; border: 1px solid #30363d; border-radius: 10px;
    padding: 1rem 1.2rem; margin-bottom: 0.6rem;
    display: flex; align-items: center; gap: 1rem;
}
.evo-run-card .run-num {
    background: #58a6ff; color: #0d1117; font-weight: 700;
    width: 32px; height: 32px; border-radius: 50%; display: flex;
    align-items: center; justify-content: center; font-size: 0.85rem; flex-shrink: 0;
}
.evo-run-card .run-info { flex: 1; }
.evo-run-card .run-label { font-weight: 700; color: #e6edf3; font-size: 0.92rem; }
.evo-run-card .run-desc { font-size: 0.8rem; color: #8b949e; margin-top: 0.15rem; }
.evo-run-card .run-stats { font-size: 0.78rem; color: #484f58; margin-top: 0.15rem; }
.evo-narrative {
    background: #161b22; border: 1px solid #30363d; border-radius: 12px;
    padding: 1.5rem 1.8rem; font-size: 0.9rem; line-height: 1.8; color: #c9d1d9;
}
.evo-insight-card {
    background: linear-gradient(135deg, #0d1117 0%, #161b22 100%);
    border: 1px solid #58a6ff; border-left: 4px solid #58a6ff;
    border-radius: 10px; padding: 1.1rem 1.3rem; margin: 0.8rem 0;
    font-size: 0.88rem; line-height: 1.7; color: #c9d1d9;
}
.evo-insight-card strong { color: #58a6ff; }
.evo-flip-indicator { font-size: 0.78rem; color: #8b949e; margin-top: 0.3rem; }
.evo-flip-indicator strong { color: #58a6ff; }
.evo-error-info { font-size: 0.78rem; color: #484f58; margin-top: 0.3rem; }
.evo-detail-col {
    border-radius: 8px; padding: 0.8rem 1rem; font-size: 0.82rem; line-height: 1.6; margin-bottom: 0.5rem;
}
.evo-detail-col.run-pass { background: #0d2818; border: 1px solid #238636; }
.evo-detail-col.run-fail { background: #2d1215; border: 1px solid #da3633; }
.evo-detail-col.run-absent { background: #161b22; border: 1px dashed #30363d; color: #484f58; text-align: center; padding: 2rem 1rem; }
.evo-detail-label {
    font-size: 0.68rem; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.06em; margin-bottom: 0.4rem;
}
.evo-detail-label.pass { color: #3fb950; }
.evo-detail-label.fail { color: #f85149; }
.evo-traj-bar {
    display: flex; gap: 1rem; font-size: 0.78rem; color: #8b949e;
    background: #0d1117; border: 1px solid #30363d; border-radius: 6px;
    padding: 0.4rem 0.7rem; margin: 0.4rem 0;
}
.evo-traj-bar .num { font-weight: 700; color: #e6edf3; }
.evo-pattern-box {
    border-radius: 8px; padding: 0.7rem 0.9rem; margin: 0.6rem 0; font-size: 0.82rem; line-height: 1.6;
}
.evo-pattern-box.same { background: #0d2818; border: 1px solid #238636; border-left: 3px solid #3fb950; color: #7ee787; }
.evo-pattern-box.diff { background: #2d2000; border: 1px solid #9e6a03; border-left: 3px solid #d29922; color: #e3b341; }
.evo-pattern-box.absent { background: #161b22; border: 1px solid #30363d; border-left: 3px solid #484f58; color: #8b949e; }
.evo-grader {
    background: #0d1117; border: 1px solid #30363d; border-radius: 6px;
    padding: 0.5rem 0.7rem; margin: 0.4rem 0; font-size: 0.78rem;
}
.evo-grader .expected { color: #3fb950; }
.evo-grader .actual { color: #f85149; }
.evo-grader-label { font-size: 0.65rem; font-weight: 700; text-transform: uppercase; color: #8b949e; letter-spacing: 0.05em; }
.evo-cat-badge {
    display: inline-block; background: #21262d; color: #e6edf3;
    font-size: 0.7rem; font-weight: 600; padding: 0.15rem 0.5rem;
    border-radius: 20px; margin-right: 0.2rem; margin-top: 0.3rem;
}
.evo-why { font-size: 0.78rem; color: #8b949e; margin-top: 0.3rem; line-height: 1.5; }
.evo-step-timeline { border-left: 3px solid #30363d; padding-left: 0.8rem; margin: 0.4rem 0; }
.evo-step { padding: 0.2rem 0; font-size: 0.75rem; color: #8b949e; position: relative; }
.evo-step::before {
    content: ''; position: absolute; left: -1.15rem; top: 0.45rem;
    width: 6px; height: 6px; border-radius: 50%; background: #484f58;
}
.evo-step.error::before { background: #f85149; }
.evo-step.script::before { background: #58a6ff; }
.evo-step.assistant::before { background: #bc8cff; }
.evo-err-badge {
    display: inline-block; background: #2d1215; color: #f85149;
    font-size: 0.68rem; font-weight: 600; padding: 0.1rem 0.4rem;
    border-radius: 10px; margin-right: 0.2rem; margin-top: 0.2rem;
}
.agg-table { width:100%; border-collapse:collapse; font-size:0.88rem; }
.agg-table th { background:#1a3a5c; color:#fff; padding:8px 12px; text-align:left; font-weight:600; position:sticky; top:0; z-index:1; }
.agg-table td { padding:7px 12px; border-bottom:1px solid #30363d; color:#e6edf3; }
.agg-table tr:hover td { background:#161b22; }
.stat-card-wrap { overflow:visible; position:relative; margin-bottom:16px; }
.evo-row-detail { background:#161b22; }
.evo-row-detail summary { user-select:none; }
.evo-row-detail[open] { background:#0d1117; }
</style>
"""


# ---------------------------------------------------------------------------
# Persistent storage for run metadata (survives server restarts)
# ---------------------------------------------------------------------------

_EVO_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".streamlit", "evo_state.json")


def _save_evo_state(runs_meta: list[dict], ready: bool = False, page: str = "Timeline"):
    os.makedirs(os.path.dirname(_EVO_STATE_FILE), exist_ok=True)
    with open(_EVO_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"runs": runs_meta, "ready": ready, "page": page}, f, ensure_ascii=False)


def _load_evo_state() -> tuple[list[dict], bool, str]:
    if os.path.exists(_EVO_STATE_FILE):
        try:
            with open(_EVO_STATE_FILE, encoding="utf-8") as f:
                data = json.load(f)
            runs = data.get("runs", [])
            valid = [r for r in runs if os.path.exists(r.get("path", ""))]
            return valid, data.get("ready", False) and len(valid) >= 2, data.get("page", "Timeline")
        except Exception:
            pass
    return [], False, "Timeline"


# ---------------------------------------------------------------------------
# ZIP handling
# ---------------------------------------------------------------------------

def _is_eval_root(path: Path) -> bool:
    return any(
        d.is_dir() and (d.name.startswith("RegressionBench_") or "regressionbench" in d.name.lower())
        for d in path.iterdir()
    )


def _find_eval_root(base: str) -> str:
    base_path = Path(base)
    if _is_eval_root(base_path):
        return str(base_path)
    for child in base_path.iterdir():
        if child.is_dir():
            if _is_eval_root(child):
                return str(child)
            for grandchild in child.iterdir():
                if grandchild.is_dir() and _is_eval_root(grandchild):
                    return str(grandchild)
    return str(base_path)


def _extract_zip(uploaded) -> str:
    zip_hash = hashlib.md5(uploaded.getvalue()).hexdigest()[:10]
    extract_dir = os.path.join(tempfile.gettempdir(), f"evo_run_{zip_hash}")
    if not os.path.exists(extract_dir):
        with zipfile.ZipFile(io.BytesIO(uploaded.getvalue())) as zf:
            zf.extractall(extract_dir)
    return _find_eval_root(extract_dir)


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def match_queries_across_runs(runs: list[EvolutionRun]) -> list[QueryEvolution]:
    all_keys: set[tuple[str, str]] = set()
    for run in runs:
        all_keys |= run.case_lookup.keys()

    evolutions = []
    for batch, qi in sorted(all_keys):
        cases = [run.case_lookup.get((batch, qi)) for run in runs]
        outcomes = [c.passed if c else None for c in cases]
        chain = "-".join("P" if o else "F" if o is False else "?" for o in outcomes)

        present = [o for o in outcomes if o is not None]
        present_in_all = len(present) == len(runs)

        if not present:
            cat = "flaky"
        elif not present_in_all and len(present) == 1:
            cat = "not_comparable"
        elif all(o is True for o in present):
            cat = "stable_pass"
        elif all(o is False for o in present):
            cat = "stable_fail"
        elif present[-1] is True and present[0] is False:
            cat = "improved"
        elif present[-1] is False and present[0] is True:
            cat = "regressed"
        else:
            cat = "flaky"

        first_case = next((c for c in cases if c), None)
        evolutions.append(QueryEvolution(
            batch=batch,
            query_index=qi,
            query_text=first_case.query_text if first_case else "",
            cases=cases,
            outcomes=outcomes,
            transition_chain=chain,
            category=cat,
        ))
    return evolutions


def compute_evolution_summary(
    evolutions: list[QueryEvolution],
    runs: list[EvolutionRun],
) -> EvolutionSummary:
    cats = Counter(e.category for e in evolutions)

    pass_counts = []
    total_counts = []
    pass_rates = []
    for run in runs:
        total = len(run.all_cases)
        passed = sum(1 for c in run.all_cases if c.passed)
        total_counts.append(total)
        pass_counts.append(passed)
        pass_rates.append(round(passed / max(total, 1) * 100, 1))

    batch_names = sorted({e.batch for e in evolutions})
    batch_pass_rates: dict[str, list[Optional[float]]] = {}
    for bn in batch_names:
        rates: list[Optional[float]] = []
        for run in runs:
            batch_cases = run.batches.get(bn, [])
            if batch_cases:
                rates.append(round(sum(1 for c in batch_cases if c.passed) / len(batch_cases) * 100, 1))
            else:
                rates.append(None)
        batch_pass_rates[bn] = rates

    return EvolutionSummary(
        total_queries=len(evolutions),
        run_labels=[r.label for r in runs],
        pass_rates=pass_rates,
        pass_counts=pass_counts,
        total_counts=total_counts,
        stable_passes=cats.get("stable_pass", 0),
        stable_failures=cats.get("stable_fail", 0),
        improved=cats.get("improved", 0),
        regressed=cats.get("regressed", 0),
        flaky=cats.get("flaky", 0),
        not_comparable=cats.get("not_comparable", 0),
        batch_pass_rates=batch_pass_rates,
    )


# ---------------------------------------------------------------------------
# LLM narrative + delta explanations
# ---------------------------------------------------------------------------

_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache")


def _evo_cache_path(runs, suffix="narrative") -> str:
    h = hashlib.md5("|".join(r.path for r in runs).encode()).hexdigest()[:12]
    return os.path.join(_CACHE_DIR, f"evo_{suffix}_{h}.json")


def _build_evolution_data_block(
    summary: EvolutionSummary,
    evolutions: list[QueryEvolution],
    runs: list[EvolutionRun],
) -> str:
    lines = ["=== RUN EVOLUTION DATA ===", ""]
    lines.append(f"Number of runs: {len(runs)}")
    for i, run in enumerate(runs):
        lines.append(f"  Run {i+1} ({run.label}): {summary.pass_counts[i]}/{summary.total_counts[i]} passed ({summary.pass_rates[i]}%) — {run.description or 'no description'}")
    lines.append("")

    lines.append(f"Total unique queries: {summary.total_queries}")
    lines.append(f"Stable passes (all runs): {summary.stable_passes}")
    lines.append(f"Stable failures (all runs): {summary.stable_failures}")
    lines.append(f"Improved (fail→pass): {summary.improved}")
    lines.append(f"Regressed (pass→fail): {summary.regressed}")
    lines.append(f"Flaky (oscillating): {summary.flaky}")
    lines.append(f"Not comparable (only in one run): {summary.not_comparable}")
    lines.append("")

    not_present = []
    for e in evolutions:
        missing_runs = [runs[i].label for i, o in enumerate(e.outcomes) if o is None]
        if missing_runs:
            not_present.append(f"  [{e.batch}] Q{e.query_index}: missing in {', '.join(missing_runs)}")
    if not_present:
        lines.append(f"=== QUERIES NOT PRESENT IN ALL RUNS ({len(not_present)}) ===")
        for np_line in not_present[:20]:
            lines.append(np_line)
        lines.append("")

    lines.append("=== BATCH PASS RATES ===")
    for bn, rates in sorted(summary.batch_pass_rates.items()):
        rate_str = " → ".join(f"{r}%" if r is not None else "NOT PRESENT" for r in rates)
        present_rates = [r for r in rates if r is not None]
        if len(present_rates) >= 2:
            delta = present_rates[-1] - present_rates[0]
            lines.append(f"  {bn}: {rate_str} (net {delta:+.1f}%)")
        elif len(present_rates) == 1:
            lines.append(f"  {bn}: {rate_str} (only present in one run)")
        else:
            lines.append(f"  {bn}: {rate_str}")
    lines.append("")

    lines.append("=== EFFICIENCY & COST METRICS PER RUN ===")
    for i, run in enumerate(runs):
        cases = run.all_cases
        n = max(len(cases), 1)
        avg_time = sum(c.execution_time_sec for c in cases) / n
        avg_steps = sum(c.step_count for c in cases) / n
        avg_errors = sum(c.error_count for c in cases) / n
        avg_scripts = sum(c.script_exec_count for c in cases) / n
        total_retries = sum(c.retry_count for c in cases)
        retry_loops = sum(1 for c in cases if len(c.script_similarity_groups) > 0)
        false_success = sum(1 for c in cases if c.has_success_claim and not c.passed)
        lines.append(
            f"  {run.label}: avg {avg_time:.1f}s/query, {avg_steps:.1f} steps, "
            f"{avg_errors:.1f} errors, {avg_scripts:.1f} scripts, "
            f"{total_retries} retries, {retry_loops} retry-loops, "
            f"{false_success} false success claims"
        )
    lines.append("")

    speed_changes = []
    for e in evolutions:
        first_case = next((c for c in e.cases if c), None)
        last_case = next((c for c in reversed(e.cases) if c), None)
        if first_case and last_case and first_case is not last_case:
            time_delta = last_case.execution_time_sec - first_case.execution_time_sec
            step_delta = last_case.step_count - first_case.step_count
            if abs(time_delta) > 5 or abs(step_delta) > 3:
                speed_changes.append((e, time_delta, step_delta))
    speed_changes.sort(key=lambda x: abs(x[1]), reverse=True)
    if speed_changes[:10]:
        lines.append("=== QUERIES WITH SIGNIFICANT SPEED/EFFICIENCY CHANGES ===")
        for e, td, sd in speed_changes[:10]:
            direction = "slower" if td > 0 else "faster"
            lines.append(
                f"  [{e.batch}] Q{e.query_index}: {td:+.1f}s ({direction}), "
                f"{sd:+d} steps — {e.query_text[:80]}"
            )
        lines.append("")

    all_error_types_per_run: list[Counter] = [Counter() for _ in runs]
    for run_idx, run in enumerate(runs):
        for c in run.all_cases:
            for et in c.error_types:
                all_error_types_per_run[run_idx][et] += 1
    all_et = sorted({et for ctr in all_error_types_per_run for et in ctr})
    if all_et:
        lines.append("=== ERROR TYPE EVOLUTION ===")
        for et in all_et[:15]:
            counts = [str(ctr.get(et, 0)) for ctr in all_error_types_per_run]
            lines.append(f"  {et}: {' → '.join(counts)}")
        lines.append("")

    stable_fails = [e for e in evolutions if e.category == "stable_fail"][:15]
    if stable_fails:
        lines.append("=== PERSISTENT FAILURES (failing in ALL runs) ===")
        for e in stable_fails:
            error_info = []
            for i, case in enumerate(e.cases):
                if case and case.error_types:
                    error_info.append(f"{runs[i].label}: {', '.join(case.error_types[:2])}")
            err_str = f" | Errors: {'; '.join(error_info)}" if error_info else ""
            lines.append(f"  [{e.batch}] Q{e.query_index}: {e.query_text[:100]}{err_str}")
        lines.append("")

    regressed = [e for e in evolutions if e.category == "regressed"][:10]
    if regressed:
        lines.append("=== REGRESSIONS (were passing, now failing) ===")
        for e in regressed:
            lines.append(f"  [{e.batch}] Q{e.query_index}: {e.query_text[:100]} ({e.transition_chain})")
        lines.append("")

    improved = [e for e in evolutions if e.category == "improved"][:10]
    if improved:
        lines.append("=== IMPROVEMENTS (were failing, now passing) ===")
        for e in improved:
            lines.append(f"  [{e.batch}] Q{e.query_index}: {e.query_text[:100]} ({e.transition_chain})")

    return "\n".join(lines)


def _generate_evolution_narrative(
    summary: EvolutionSummary,
    evolutions: list[QueryEvolution],
    runs: list[EvolutionRun],
) -> str:
    cache_path = _evo_cache_path(runs, "narrative")
    if os.path.exists(cache_path):
        with open(cache_path) as f:
            cached = json.load(f)
            if cached.get("narrative"):
                return cached["narrative"]

    data_block = _build_evolution_data_block(summary, evolutions, runs)
    prompt = f"""You are an expert eval analyst reviewing how an AI agent's eval results evolved across {len(runs)} runs.

{data_block}

Write actionable insights as **bullet points** organized into these sections. Use HTML formatting (<strong> for key terms, <br> for line breaks, <ul>/<li> for bullets).

RULES:
- If a query was NOT PRESENT in a run, say "not present in Run X" — do NOT compare it or claim a percentage change.
- If a query FAILED, say it FAILED — don't sugarcoat with "needs improvement".
- A 100% pass rate on a tiny batch is meaningless — call out small sample sizes.
- Be specific: name batches, error types, query patterns. No generic advice.

FORMAT YOUR RESPONSE AS:

<strong>Overall Verdict</strong>: One sentence — is the agent improving, regressing, or stagnant?

<strong>Performance & Speed</strong>
<ul><li>Are queries running faster or slower? Which batches got more/less efficient?</li>
<li>Are there more retries or retry loops? What's driving the cost up/down?</li>
<li>Flag false success claims (agent said done but actually failed)</li></ul>

<strong>What Worked</strong>
<ul><li>Specific improvements with batch names, which run fixed them, and why it matters</li></ul>

<strong>What Broke</strong>
<ul><li>Specific regressions with batch names, which run broke them, error types</li></ul>

<strong>Persistent Blockers</strong>
<ul><li>Queries failing in ALL runs — what they have in common, root cause patterns</li></ul>

<strong>Missing Coverage</strong>
<ul><li>Queries not present in all runs — are test sets inconsistent? Were evals dropped?</li></ul>

<strong>Business Recommendations</strong>
<ul><li>Which batches/query types should the team invest in fixing? (highest ROI)</li>
<li>Which stable passes can be deprioritized or removed from the regression suite?</li>
<li>Cost reduction: which runs had unnecessary retries, bloated scripts, or wasted steps?</li>
<li>What prompt/model changes should be tried next based on the error patterns?</li></ul>"""

    narrative, provider = _call_llm(prompt, max_tokens=3000, temp=0.2)

    if narrative:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        with open(cache_path, "w") as f:
            json.dump({"narrative": narrative, "provider": provider}, f)

    return narrative or "LLM narrative generation failed. Set GEMINI_API_KEY or GROQ_API_KEY."


def _generate_delta_insights(
    summary: EvolutionSummary,
    evolutions: list[QueryEvolution],
    runs: list[EvolutionRun],
) -> str:
    cache_path = _evo_cache_path(runs, "deltas")
    if os.path.exists(cache_path):
        with open(cache_path) as f:
            cached = json.load(f)
            if cached.get("insights"):
                return cached["insights"]

    data_block = _build_evolution_data_block(summary, evolutions, runs)
    prompt = f"""You are an expert eval analyst. Below is evolution data across {len(runs)} runs.

{data_block}

Write 3-5 bullet points (HTML <ul>/<li>) interpreting the key changes. Use <strong> to highlight key terms.

RULES:
- If a query was NOT PRESENT in a run, say "not present" — do NOT calculate a delta for it.
- If something FAILED, say FAILED directly.
- A 100% pass rate on 2 queries means nothing — call out small sample sizes.
- Focus on what matters: what's broken, what the team should do about it, cost/efficiency implications.
- Be specific: name batches and error types. No generic statements like "overall trend is positive"."""

    insights, provider = _call_llm(prompt, max_tokens=1000, temp=0.2)

    if insights:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        with open(cache_path, "w") as f:
            json.dump({"insights": insights, "provider": provider}, f)

    return insights or ""


def _generate_category_analysis(
    items: list[QueryEvolution],
    runs: list[EvolutionRun],
    category_label: str,
) -> str:
    """LLM-powered analysis explaining WHY metrics changed for a category group."""
    run_paths_hash = hashlib.md5(
        ("|".join(r.path for r in runs) + "|" + category_label).encode()
    ).hexdigest()[:12]
    cache_path = os.path.join(_CACHE_DIR, f"evo_cat_{run_paths_hash}.json")

    if os.path.exists(cache_path):
        try:
            with open(cache_path) as f:
                cached = json.load(f)
            if cached.get("analysis"):
                return cached["analysis"]
        except Exception:
            pass

    # Build data block for LLM
    lines = [f"=== {category_label.upper()} — {len(items)} QUERIES ===", ""]

    # Per-run metrics
    for ri, run in enumerate(runs):
        cases_in_group = []
        for e in items:
            if ri < len(e.cases) and e.cases[ri]:
                cases_in_group.append(e.cases[ri])
        if cases_in_group:
            n = len(cases_in_group)
            avg_steps = sum(c.step_count for c in cases_in_group) / n
            avg_time = sum(c.execution_time_sec for c in cases_in_group) / n
            avg_errors = sum(c.error_count for c in cases_in_group) / n
            passed = sum(1 for c in cases_in_group if c.passed)
            err_types = Counter()
            for c in cases_in_group:
                for et in c.error_types:
                    err_types[et] += 1
            err_str = ", ".join(f"{k}({v})" for k, v in err_types.most_common(5)) or "none"
            lines.append(
                f"  {run.label}: {n} queries, {passed}/{n} passed, "
                f"avg {avg_steps:.1f} steps, {avg_time:.1f}s, {avg_errors:.1f} errors | errors: {err_str}"
            )
        else:
            lines.append(f"  {run.label}: no data for these queries")
    lines.append("")

    # Transition chains
    chains = Counter(e.transition_chain for e in items)
    lines.append("Transition patterns:")
    for chain, count in chains.most_common(5):
        readable = " → ".join("Pass" if c == "P" else "Fail" if c == "F" else "?" for c in chain.split("-"))
        lines.append(f"  {readable}: {count} queries")
    lines.append("")

    # Sample queries with grader evidence
    lines.append("Sample queries:")
    for e in items[:8]:
        lines.append(f"  [{e.batch}] Q{e.query_index}: {e.query_text[:100]}")
        for i, case in enumerate(e.cases):
            if case:
                status = "PASS" if case.passed else "FAIL"
                errs = ", ".join(case.error_types[:3]) if case.error_types else "none"
                lines.append(f"    {runs[i].label}: {status} | {case.step_count} steps, {case.execution_time_sec:.0f}s, errors: {errs}")
                ev = extract_grader_evidence(case)
                if ev and ev.assertions and len(ev.assertions) > 0:
                    a = ev.assertions[0]
                    if a.get("expected") or a.get("actual"):
                        lines.append(f"      Expected: {str(a.get('expected', ''))[:80]} | Actual: {str(a.get('actual', ''))[:80]}")
                cls = runs[i].cls_lookup.get((case.batch, case.query_index))
                if cls and cls.why:
                    lines.append(f"      Why: {cls.why[:120]}")

    data_block = "\n".join(lines)

    prompt = f"""You are an expert eval analyst. Below is data for a group of queries categorized as "{category_label}" across {len(runs)} runs.

{data_block}

Write a concise analysis (4-6 bullet points) using HTML (<ul><li>, <strong>). Explain:
1. WHY the metrics changed between runs (what caused steps/time/errors to go up or down)
2. What the common failure patterns are (if any) and their root causes
3. Whether the changes are due to: new/removed queries, error type shifts, retry behavior, slower execution, or agent behavior changes
4. Any actionable insight (what should the team focus on)

RULES:
- Be specific: name error types, operations, batch names. No generic advice.
- Compare run-to-run: "Run 2 was 40s slower because GenericError doubled" not just "Run 2 was slower"
- If queries are all passing, focus on efficiency trends and what makes them stable
- Keep it under 150 words. No preamble."""

    analysis, provider = _call_llm(prompt, max_tokens=800, temp=0.2)

    if analysis:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        with open(cache_path, "w") as f:
            json.dump({"analysis": analysis, "provider": provider}, f)

    return analysis or ""


# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------

def _chain_html(chain: str) -> str:
    parts = []
    for ch in chain.split("-"):
        cls = ch.lower()
        parts.append(f'<span class="{cls}">{ch}</span>')
    return '<div class="evo-chain">' + '<span class="sep">\u2192</span>'.join(parts) + '</div>'


def _find_flip_run(evol: QueryEvolution, runs: list[EvolutionRun]) -> Optional[int]:
    outcomes = evol.outcomes
    if evol.category == "improved":
        for i in range(len(outcomes) - 1, 0, -1):
            if outcomes[i] is True and outcomes[i - 1] is False:
                return i
    elif evol.category == "regressed":
        for i in range(len(outcomes) - 1, 0, -1):
            if outcomes[i] is False and outcomes[i - 1] is True:
                return i
    return None


# ---------------------------------------------------------------------------
# Page renderers
# ---------------------------------------------------------------------------

def _render_upload_page(runs_meta: list[dict]):
    st.markdown("""
    <div class="evo-hero">
        <h1>Run Evolution</h1>
        <p>Upload eval runs one by one to track how results change over time.</p>
    </div>
    """, unsafe_allow_html=True)

    if runs_meta:
        st.markdown('<div class="evo-section-strip">Uploaded Runs</div>', unsafe_allow_html=True)
        for i, rm in enumerate(runs_meta):
            col1, col2 = st.columns([6, 1])
            with col1:
                st.markdown(f"""
                <div class="evo-run-card">
                    <div class="run-num">{i + 1}</div>
                    <div class="run-info">
                        <div class="run-label">{_h(rm['label'])}</div>
                        <div class="run-desc">{_h(rm.get('description', ''))}</div>
                        <div class="run-stats">{_h(rm['filename'])}</div>
                    </div>
                </div>""", unsafe_allow_html=True)
            with col2:
                if st.button("Remove", key=f"evo_remove_{i}"):
                    runs_meta.pop(i)
                    st.session_state["evo_runs"] = runs_meta
                    st.session_state["evo_ready"] = False
                    _save_evo_state(runs_meta, False)
                    st.rerun()

    st.markdown('<div class="evo-section-strip">Add a Run</div>', unsafe_allow_html=True)

    default_label = f"Run {len(runs_meta) + 1}"
    label = st.text_input("Label", value=default_label, key="evo_new_label")
    description = st.text_input("What changed in this run?", key="evo_new_desc")
    uploaded = st.file_uploader("Upload ZIP of eval results", type=["zip"], key=f"evo_upload_{len(runs_meta)}")

    if uploaded and st.button("Add Run", type="primary"):
        path = _extract_zip(uploaded)
        runs_meta.append({
            "label": label,
            "description": description,
            "path": path,
            "filename": uploaded.name,
        })
        st.session_state["evo_runs"] = runs_meta
        st.session_state["evo_ready"] = False
        _save_evo_state(runs_meta, False)
        st.rerun()

    if len(runs_meta) >= 2:
        st.markdown("---")
        if st.button("Analyze Evolution", type="primary", use_container_width=True):
            st.session_state["evo_ready"] = True
            st.session_state["evo_page"] = "Timeline"
            _save_evo_state(runs_meta, True, "Timeline")
            st.rerun()
    elif len(runs_meta) == 1:
        st.info("Upload at least one more run to enable evolution analysis.")
    else:
        st.info("Upload your first eval run ZIP to get started.")


def _render_timeline(summary: EvolutionSummary, evolutions: list[QueryEvolution], runs: list[EvolutionRun]):
    net_delta = summary.pass_rates[-1] - summary.pass_rates[0]
    delta_cls = "green" if net_delta > 0 else "red" if net_delta < 0 else "slate"

    st.markdown(f"""
    <div class="evo-kpi-grid">
        <div class="evo-kpi"><div class="label">Runs</div><div class="value blue">{len(runs)}</div></div>
        <div class="evo-kpi"><div class="label">Total Queries</div><div class="value slate">{summary.total_queries}</div></div>
        <div class="evo-kpi"><div class="label">Net Pass Rate Change</div><div class="value {delta_cls}">{net_delta:+.1f}%</div></div>
        <div class="evo-kpi"><div class="label">Stable Passes</div><div class="value green">{summary.stable_passes}</div></div>
        <div class="evo-kpi"><div class="label">Regressions</div><div class="value red">{summary.regressed}</div></div>
    </div>
    """, unsafe_allow_html=True)

    # LLM-powered natural language explanation of what the deltas mean
    with st.spinner("Generating insights..."):
        delta_insights = _generate_delta_insights(summary, evolutions, runs)
    if delta_insights:
        st.markdown(f'<div class="evo-insight-card">{delta_insights}</div>', unsafe_allow_html=True)

    st.markdown('<div class="evo-section-strip">Pass Rate Per Run</div>', unsafe_allow_html=True)
    import pandas as pd
    rate_data = {"Run": summary.run_labels, "Pass Rate (%)": summary.pass_rates}
    df = pd.DataFrame(rate_data).set_index("Run")
    st.bar_chart(df, height=250)

    st.markdown('<div class="evo-section-strip">Transition Distribution</div>', unsafe_allow_html=True)
    cats = Counter(e.category for e in evolutions)
    cat_items = [
        ("Always Passed", cats.get("stable_pass", 0), "pass"),
        ("Always Failed", cats.get("stable_fail", 0), "fail"),
        ("Now Passing", cats.get("improved", 0), "improved"),
        ("Now Failing", cats.get("regressed", 0), "regressed"),
        ("Inconsistent", cats.get("flaky", 0), "flaky"),
        ("One Run Only", cats.get("not_comparable", 0), "absent"),
    ]
    cols = st.columns(len(cat_items))
    for i, (lbl, count, badge_cls) in enumerate(cat_items):
        with cols[i]:
            pct = round(100 * count / max(summary.total_queries, 1), 1)
            st.markdown(f"""
            <div class="evo-card" style="text-align:center">
                <div style="font-size:1.6rem;font-weight:700">{count}</div>
                <div style="font-size:0.78rem;color:inherit;opacity:0.6">{lbl}</div>
                <div style="font-size:0.72rem;color:inherit;opacity:0.4">{pct}%</div>
            </div>""", unsafe_allow_html=True)

    st.markdown('<div class="evo-section-strip">Batch Pass Rates Across Runs</div>', unsafe_allow_html=True)
    if summary.batch_pass_rates:
        batch_df_data = {"Batch": []}
        for i, lbl in enumerate(summary.run_labels):
            batch_df_data[lbl] = []
        batch_df_data["Change"] = []

        for bn, rates in sorted(summary.batch_pass_rates.items()):
            batch_df_data["Batch"].append(bn)
            for i, lbl in enumerate(summary.run_labels):
                batch_df_data[lbl].append(f"{rates[i]}%" if rates[i] is not None else "Not present")
            present_rates = [r for r in rates if r is not None]
            if len(present_rates) >= 2:
                delta = present_rates[-1] - present_rates[0]
                if delta > 0:
                    batch_df_data["Change"].append(f"+{delta:.1f}% improved")
                elif delta < 0:
                    batch_df_data["Change"].append(f"{delta:.1f}% regressed")
                else:
                    batch_df_data["Change"].append("no change")
            else:
                batch_df_data["Change"].append("only in one run")

        st.dataframe(pd.DataFrame(batch_df_data).set_index("Batch"), use_container_width=True)

    # Efficiency & performance metrics across runs
    st.markdown('<div class="evo-section-strip">Performance & Efficiency Trends</div>', unsafe_allow_html=True)

    perf_data = {"Metric": [
        "Avg Execution Time (s)", "Avg Steps per Query", "Avg Errors per Query",
        "Avg Scripts per Query", "Total Retries", "Queries with Retry Loops",
    ]}
    for run in runs:
        cases = run.all_cases
        n = max(len(cases), 1)
        avg_time = sum(c.execution_time_sec for c in cases) / n
        avg_steps = sum(c.step_count for c in cases) / n
        avg_errors = sum(c.error_count for c in cases) / n
        avg_scripts = sum(c.script_exec_count for c in cases) / n
        total_retries = sum(c.retry_count for c in cases)
        retry_loop_queries = sum(1 for c in cases if len(c.script_similarity_groups) > 0)

        perf_data[run.label] = [
            f"{avg_time:.1f}", f"{avg_steps:.1f}", f"{avg_errors:.1f}",
            f"{avg_scripts:.1f}", str(total_retries), str(retry_loop_queries),
        ]

    first_times = [float(perf_data[runs[0].label][i]) if i < 4 else int(perf_data[runs[0].label][i]) for i in range(6)]
    last_times = [float(perf_data[runs[-1].label][i]) if i < 4 else int(perf_data[runs[-1].label][i]) for i in range(6)]
    changes = []
    for i in range(6):
        d = last_times[i] - first_times[i]
        better = d < 0
        if abs(d) < 0.05:
            changes.append("—")
        elif better:
            changes.append(f"{d:+.1f} faster" if i == 0 else f"{d:+.1f} fewer")
        else:
            changes.append(f"{d:+.1f} slower" if i == 0 else f"{d:+.1f} more")
    perf_data["Trend"] = changes

    st.dataframe(pd.DataFrame(perf_data).set_index("Metric"), use_container_width=True)


# ---------------------------------------------------------------------------
# Detail view renderers
# ---------------------------------------------------------------------------

def _render_run_column(
    run: EvolutionRun,
    case: Optional[ParsedEvalCase],
    run_index: int,
) -> None:
    if case is None:
        st.markdown(
            f'<div class="evo-detail-col run-absent">'
            f'<strong>Not present in {_h(run.label)}</strong><br>'
            f'<span style="font-size:0.75rem">Query may have been removed from test set</span>'
            f'</div>',
            unsafe_allow_html=True,
        )
        return

    status_cls = "pass" if case.passed else "fail"
    col_cls = "run-pass" if case.passed else "run-fail"
    status_text = "PASSED" if case.passed else "FAILED"

    parts = [f'<div class="evo-detail-col {col_cls}">']
    parts.append(f'<div class="evo-detail-label {status_cls}">{_h(run.label)} — {status_text}</div>')

    parts.append(
        f'<div class="evo-traj-bar">'
        f'<span><span class="num">{case.step_count}</span> steps</span>'
        f'<span><span class="num">{case.error_count}</span> errors</span>'
        f'<span><span class="num">{case.script_exec_count}</span> scripts</span>'
        f'<span><span class="num">{case.execution_time_sec:.1f}</span>s</span>'
        f'</div>'
    )

    if case.error_types:
        badges = "".join(f'<span class="evo-err-badge">{_h(et)}</span>' for et in case.error_types[:5])
        parts.append(badges)

    ev = extract_grader_evidence(case)
    if ev and ev.assertions and len(ev.assertions) > 0:
        a = ev.assertions[0]
        exp_val = a.get("expected", "")
        act_val = a.get("actual", "")
        if exp_val or act_val:
            parts.append(
                f'<div class="evo-grader">'
                f'<div class="evo-grader-label">Expected</div>'
                f'<div class="expected">{_h(str(exp_val)[:150])}</div>'
                f'<div class="evo-grader-label" style="margin-top:0.3rem">Actual</div>'
                f'<div class="actual">{_h(str(act_val)[:150])}</div>'
                f'</div>'
            )
        elif a.get("reason"):
            parts.append(f'<div class="evo-grader"><div class="actual">{_h(str(a["reason"])[:200])}</div></div>')
    elif ev and ev.grader_message:
        parts.append(f'<div class="evo-grader"><div class="actual">{_h(ev.grader_message[:200])}</div></div>')

    cls = run.cls_lookup.get((case.batch, case.query_index))
    if cls:
        cat_badges = "".join(f'<span class="evo-cat-badge">{_h(c)}</span>' for c in cls.trajectory_categories[:3])
        if cls.outcome_category:
            cat_badges += f'<span class="evo-cat-badge">{_h(cls.outcome_category)}</span>'
        parts.append(cat_badges)
        if cls.why:
            parts.append(f'<div class="evo-why">{_h(cls.why[:250])}</div>')

    timeline = (
        f'<details><summary style="cursor:pointer;font-size:0.72rem;font-weight:600;margin-top:0.5rem">'
        f'Trajectory ({case.step_count} steps)</summary><div class="evo-step-timeline">'
    )
    for step in case.steps:
        if step.step_type == "UserQuery":
            timeline += f'<div class="evo-step"><strong>Query:</strong> {_h(step.text[:100])}</div>'
        elif step.step_type == "ScriptExecution":
            intent = describe_script(step.script_full)
            timeline += f'<div class="evo-step script"><strong>Script:</strong> {_h(intent)} ({step.script_len} chars)</div>'
        elif step.step_type == "ScriptResponse":
            if step.error_type:
                err_preview = step.console[:80] if step.console else step.result[:80]
                timeline += f'<div class="evo-step error"><strong>{_h(step.error_type)}:</strong> {_h(err_preview)}</div>'
            else:
                preview = step.result[:80] if step.result else "OK"
                timeline += f'<div class="evo-step"><strong>Response:</strong> {_h(preview)}</div>'
        elif step.step_type == "Assistant":
            timeline += f'<div class="evo-step assistant"><strong>Agent:</strong> {_h(step.text[:120])}</div>'
    timeline += '</div></details>'
    parts.append(timeline)

    parts.append('</div>')
    st.markdown("\n".join(parts), unsafe_allow_html=True)


def _render_error_pattern_comparison(
    evol: QueryEvolution,
    runs: list[EvolutionRun],
) -> None:
    absent_runs = []
    failed_errors: dict[str, list[str]] = {}

    for i, (case, outcome) in enumerate(zip(evol.cases, evol.outcomes)):
        if case is None:
            absent_runs.append(runs[i].label)
        elif not case.passed:
            failed_errors[runs[i].label] = list(case.error_types[:5]) if case.error_types else ["(no error type)"]

    if absent_runs:
        labels = ", ".join(f"<strong>{_h(r)}</strong>" for r in absent_runs)
        st.markdown(
            f'<div class="evo-pattern-box absent">Not present in {labels} — query may have been removed from test set</div>',
            unsafe_allow_html=True,
        )

    if len(failed_errors) >= 2:
        error_sets = [set(v) for v in failed_errors.values()]
        if all(s == error_sets[0] for s in error_sets):
            types_str = ", ".join(f"<strong>{_h(t)}</strong>" for t in sorted(error_sets[0]))
            st.markdown(
                f'<div class="evo-pattern-box same">Same root cause across all failed runs: {types_str}</div>',
                unsafe_allow_html=True,
            )
        else:
            parts = []
            for label, errs in failed_errors.items():
                parts.append(f"<strong>{_h(label)}</strong>: {', '.join(_h(e) for e in errs)}")
            st.markdown(
                f'<div class="evo-pattern-box diff">Error patterns <strong>differ</strong> across runs — root cause may have shifted<br>'
                f'{"<br>".join(parts)}</div>',
                unsafe_allow_html=True,
            )
    elif len(failed_errors) == 1:
        label, errs = next(iter(failed_errors.items()))
        types_str = ", ".join(f"<strong>{_h(e)}</strong>" for e in errs)
        st.markdown(
            f'<div class="evo-pattern-box diff">Failed only in <strong>{_h(label)}</strong>: {types_str}</div>',
            unsafe_allow_html=True,
        )


def _render_query_detail(
    evol: QueryEvolution,
    runs: list[EvolutionRun],
) -> None:
    st.markdown(
        f'<div style="font-size:0.9rem;line-height:1.6;margin-bottom:0.5rem">{_h(evol.query_text)}</div>',
        unsafe_allow_html=True,
    )

    _render_error_pattern_comparison(evol, runs)

    cols = st.columns(len(runs))
    for i, (run, col) in enumerate(zip(runs, cols)):
        with col:
            case = evol.cases[i] if i < len(evol.cases) else None
            _render_run_column(run, case, i)


def _build_inline_detail_html(
    evol: QueryEvolution,
    runs: list[EvolutionRun],
    dark: bool,
) -> str:
    """Build pure-HTML trajectory detail for inline table expansion."""
    bg = "#0d1117" if dark else "#f8fafc"
    pass_bg = "#0d2818" if dark else "#f0fdf4"
    pass_bd = "#238636" if dark else "#bbf7d0"
    fail_bg = "#2d1215" if dark else "#fef2f2"
    fail_bd = "#da3633" if dark else "#fecaca"
    absent_bg = "#161b22" if dark else "#f8fafc"
    absent_bd = "#30363d" if dark else "#e2e8f0"
    text_color = "#e6edf3" if dark else "#1e293b"
    muted = "#8b949e" if dark else "#64748b"
    green = "#3fb950" if dark else "#059669"
    red = "#f85149" if dark else "#dc2626"
    blue = "#58a6ff" if dark else "#2563eb"
    purple = "#bc8cff" if dark else "#7c3aed"
    badge_bg = "#21262d" if dark else "#f1f5f9"
    step_border = "#30363d" if dark else "#e2e8f0"

    out = []

    # --- Full query text ---
    out.append(
        f'<div style="font-size:0.88rem;line-height:1.6;color:{text_color};margin-bottom:0.6rem">'
        f'<strong>Query:</strong> {_h(evol.query_text)}</div>'
    )

    # --- Cross-run error analysis ---
    absent_runs = []
    failed_errors: dict[str, list[str]] = {}
    for i, (case, outcome) in enumerate(zip(evol.cases, evol.outcomes)):
        if case is None:
            absent_runs.append(runs[i].label)
        elif not case.passed:
            failed_errors[runs[i].label] = list(case.error_types[:5]) if case.error_types else ["(no error type)"]

    if absent_runs:
        labels = ", ".join(f"<strong>{_h(r)}</strong>" for r in absent_runs)
        out.append(
            f'<div style="background:{absent_bg};border:1px solid {absent_bd};border-left:3px solid {muted};'
            f'border-radius:6px;padding:0.5rem 0.7rem;margin-bottom:0.5rem;font-size:0.8rem;color:{muted}">'
            f'Not present in {labels}</div>'
        )

    if len(failed_errors) >= 2:
        error_sets = [set(v) for v in failed_errors.values()]
        if all(s == error_sets[0] for s in error_sets):
            types_str = ", ".join(f"<strong>{_h(t)}</strong>" for t in sorted(error_sets[0]))
            out.append(
                f'<div style="background:{pass_bg};border:1px solid {pass_bd};border-left:3px solid {green};'
                f'border-radius:6px;padding:0.5rem 0.7rem;margin-bottom:0.5rem;font-size:0.8rem;color:{text_color}">'
                f'Same root cause across all failed runs: {types_str}</div>'
            )
        else:
            parts_err = []
            for label, errs in failed_errors.items():
                parts_err.append(f"<strong>{_h(label)}</strong>: {', '.join(_h(e) for e in errs)}")
            out.append(
                f'<div style="background:{"#2d2000" if dark else "#fffbeb"};border:1px solid {"#9e6a03" if dark else "#fcd34d"};'
                f'border-left:3px solid {"#d29922" if dark else "#d97706"};border-radius:6px;padding:0.5rem 0.7rem;'
                f'margin-bottom:0.5rem;font-size:0.8rem;color:{text_color}">'
                f'Error patterns <strong>differ</strong> across runs — root cause may have shifted<br>'
                f'{"<br>".join(parts_err)}</div>'
            )
    elif len(failed_errors) == 1:
        label, errs = next(iter(failed_errors.items()))
        types_str = ", ".join(f"<strong>{_h(e)}</strong>" for e in errs)
        out.append(
            f'<div style="background:{fail_bg};border:1px solid {fail_bd};border-left:3px solid {red};'
            f'border-radius:6px;padding:0.5rem 0.7rem;margin-bottom:0.5rem;font-size:0.8rem;color:{text_color}">'
            f'Failed in <strong>{_h(label)}</strong>: {types_str}</div>'
        )

    # --- Per-run columns ---
    out.append(f'<div style="display:grid;grid-template-columns:repeat({len(runs)},1fr);gap:8px">')
    for i, run in enumerate(runs):
        case = evol.cases[i] if i < len(evol.cases) else None
        if case is None:
            out.append(
                f'<div style="background:{absent_bg};border:1px dashed {absent_bd};border-radius:6px;'
                f'padding:0.6rem;text-align:center;color:{muted};font-size:0.78rem">'
                f'<strong>{_h(run.label)}</strong> — not present</div>'
            )
            continue

        col_bg = pass_bg if case.passed else fail_bg
        col_bd = pass_bd if case.passed else fail_bd
        status = "PASSED" if case.passed else "FAILED"
        status_color = green if case.passed else red

        p = [
            f'<div style="background:{col_bg};border:1px solid {col_bd};border-radius:6px;'
            f'padding:0.6rem;font-size:0.78rem;line-height:1.6;color:{text_color}">',
            f'<div style="font-size:0.68rem;font-weight:700;text-transform:uppercase;'
            f'letter-spacing:0.05em;color:{status_color};margin-bottom:0.3rem">'
            f'{_h(run.label)} — {status}</div>',
            f'<div style="display:flex;gap:0.8rem;flex-wrap:wrap;font-size:0.75rem;color:{muted}">'
            f'<span><strong>{case.step_count}</strong> steps</span>'
            f'<span><strong>{case.error_count}</strong> errors</span>'
            f'<span><strong>{case.script_exec_count}</strong> scripts</span>'
            f'<span><strong>{case.execution_time_sec:.1f}</strong>s</span></div>',
        ]

        if case.error_types:
            err_badges = "".join(
                f'<span style="display:inline-block;background:{fail_bg};color:{red};'
                f'font-size:0.65rem;font-weight:600;padding:0.1rem 0.35rem;border-radius:10px;'
                f'margin-right:0.2rem;margin-top:0.2rem">{_h(et)}</span>'
                for et in case.error_types[:5]
            )
            p.append(f'<div style="margin-top:0.2rem">{err_badges}</div>')

        ev = extract_grader_evidence(case)
        if ev and ev.assertions and len(ev.assertions) > 0:
            a = ev.assertions[0]
            exp_val = a.get("expected", "")
            act_val = a.get("actual", "")
            if exp_val or act_val:
                p.append(
                    f'<div style="background:{bg};border:1px solid {absent_bd};border-radius:4px;'
                    f'padding:0.3rem 0.5rem;margin-top:0.3rem;font-size:0.72rem">'
                    f'<span style="color:{muted};font-weight:600;font-size:0.62rem;text-transform:uppercase">Expected</span> '
                    f'<span style="color:{green}">{_h(str(exp_val)[:150])}</span><br>'
                    f'<span style="color:{muted};font-weight:600;font-size:0.62rem;text-transform:uppercase">Actual</span> '
                    f'<span style="color:{red}">{_h(str(act_val)[:150])}</span></div>'
                )
            elif a.get("reason"):
                p.append(
                    f'<div style="background:{bg};border:1px solid {absent_bd};border-radius:4px;'
                    f'padding:0.3rem 0.5rem;margin-top:0.3rem;font-size:0.72rem;color:{red}">'
                    f'{_h(str(a["reason"])[:200])}</div>'
                )
        elif ev and ev.grader_message:
            p.append(
                f'<div style="background:{bg};border:1px solid {absent_bd};border-radius:4px;'
                f'padding:0.3rem 0.5rem;margin-top:0.3rem;font-size:0.72rem;color:{red}">'
                f'{_h(ev.grader_message[:200])}</div>'
            )

        cls = run.cls_lookup.get((case.batch, case.query_index))
        if cls:
            badges = "".join(
                f'<span style="display:inline-block;background:{badge_bg};'
                f'color:{text_color};font-size:0.65rem;font-weight:600;padding:0.1rem 0.4rem;'
                f'border-radius:12px;margin-right:0.2rem">{_h(c)}</span>'
                for c in (cls.trajectory_categories or [])[:4]
            )
            if cls.outcome_category:
                badges += (
                    f'<span style="display:inline-block;background:{badge_bg};'
                    f'color:{text_color};font-size:0.65rem;font-weight:600;padding:0.1rem 0.4rem;'
                    f'border-radius:12px">{_h(cls.outcome_category)}</span>'
                )
            if badges:
                p.append(f'<div style="margin-top:0.3rem">{badges}</div>')
            if cls.why:
                p.append(f'<div style="font-size:0.72rem;color:{muted};margin-top:0.2rem">{_h(cls.why[:250])}</div>')

        # Full step trajectory (always visible)
        step_html = f'<div style="border-left:3px solid {step_border};padding-left:0.6rem;margin-top:0.4rem">'
        for s in case.steps:
            if s.step_type == "UserQuery":
                step_html += (
                    f'<div style="padding:0.15rem 0;font-size:0.72rem;color:{text_color}">'
                    f'<strong>Query:</strong> {_h(s.text[:120])}</div>'
                )
            elif s.step_type == "ScriptExecution":
                intent = describe_script(s.script_full)
                step_html += (
                    f'<div style="padding:0.15rem 0;font-size:0.72rem">'
                    f'<span style="color:{blue}">Script:</span> '
                    f'<span style="color:{text_color}">{_h(intent)}</span> '
                    f'<span style="color:{muted}">({s.script_len} chars)</span></div>'
                )
            elif s.step_type == "ScriptResponse":
                if s.error_type:
                    preview = (s.console or s.result)[:80]
                    step_html += (
                        f'<div style="padding:0.15rem 0;font-size:0.72rem">'
                        f'<span style="color:{red}">{_h(s.error_type)}:</span> '
                        f'<span style="color:{muted}">{_h(preview)}</span></div>'
                    )
                else:
                    preview = (s.result or "OK")[:80]
                    step_html += (
                        f'<div style="padding:0.15rem 0;font-size:0.72rem;color:{muted}">'
                        f'Response: {_h(preview)}</div>'
                    )
            elif s.step_type == "Assistant":
                step_html += (
                    f'<div style="padding:0.15rem 0;font-size:0.72rem">'
                    f'<span style="color:{purple}">Agent:</span> '
                    f'<span style="color:{muted}">{_h(s.text[:120])}</span></div>'
                )
        step_html += '</div>'
        p.append(step_html)

        p.append('</div>')
        out.append("\n".join(p))

    out.append('</div>')
    return "\n".join(out)


def _render_category_insights(
    items: list[QueryEvolution],
    runs: list[EvolutionRun],
    is_pass: bool = False,
) -> None:
    """Show pattern-level insights for a group of queries, then an agg-table."""
    if not items:
        return

    dark = _is_dark()
    card_bg = "#161b22" if dark else "white"
    card_border = "#30363d" if dark else "#e2e8f0"
    card_text = "#e6edf3" if dark else "#1e293b"
    muted = "#8b949e" if dark else "#64748b"
    accent = "#3fb950" if is_pass and dark else "#059669" if is_pass else "#58a6ff" if dark else "#1f4e79"

    n_items = len(items)

    # --- Gather data for summary + chain analysis ---
    batch_dist = Counter(e.batch for e in items)
    avg_steps_list, avg_time_list = [], []
    chain_counter = Counter(e.transition_chain for e in items)

    for e in items:
        for case in e.cases:
            if case:
                avg_steps_list.append(case.step_count)
                avg_time_list.append(case.execution_time_sec)

    avg_steps = sum(avg_steps_list) / max(len(avg_steps_list), 1)
    avg_time = sum(avg_time_list) / max(len(avg_time_list), 1)

    # --- Build insight sections ---
    sections = []

    # 1. Summary line (no header)
    batch_str = ", ".join(f"<strong>{b}</strong> ({c})" for b, c in batch_dist.most_common(5))
    sections.append(
        f'<div style="margin-bottom:0.6rem">'
        f'<strong>{n_items}</strong> queries across <strong>{len(batch_dist)}</strong> batches: {batch_str}. '
        f'Avg <strong>{avg_steps:.0f} steps</strong>, <strong>{avg_time:.0f}s</strong> per query.</div>'
    )

    # 2. Transition pattern analysis (skip when all items share one obvious pattern)
    all_same_chain = len(chain_counter) == 1
    if chain_counter and not all_same_chain:
        chain_parts = []
        for chain, count in chain_counter.most_common(5):
            pct = round(100 * count / n_items)
            labels = chain.split("-")
            readable = " → ".join("Pass" if l == "P" else "Fail" if l == "F" else "?" for l in labels)
            if chain == "-".join(["F"] * len(runs)):
                desc = "failed every run — persistent blocker"
            elif chain == "-".join(["P"] * len(runs)):
                desc = "passed every run — stable"
            elif labels[-1] == "F" and labels[0] == "P":
                flip = next((i for i in range(len(labels) - 1, 0, -1) if labels[i] == "F" and labels[i - 1] == "P"), None)
                desc = f"regressed in {runs[flip].label}" if flip is not None and flip < len(runs) else "regressed"
            elif labels[-1] == "P" and labels[0] == "F":
                flip = next((i for i in range(1, len(labels)) if labels[i] == "P" and labels[i - 1] == "F"), None)
                desc = f"fixed in {runs[flip].label}" if flip is not None and flip < len(runs) else "improved"
            elif labels[0] == "F" and labels[-1] == "F" and "P" in labels:
                pass_runs = [runs[i].label for i, l in enumerate(labels) if l == "P" and i < len(runs)]
                desc = f"temporarily fixed in {', '.join(pass_runs)} but regressed back"
            elif labels[0] == "P" and labels[-1] == "P" and "F" in labels:
                fail_runs = [runs[i].label for i, l in enumerate(labels) if l == "F" and i < len(runs)]
                desc = f"temporarily broken in {', '.join(fail_runs)} but recovered"
            else:
                desc = "inconsistent"
            chain_parts.append(
                f'<strong>{readable}</strong> ({count}/{n_items}, {pct}%) — {desc}'
            )
        sections.append(
            f'<div style="margin-bottom:0.6rem"><span style="font-size:0.7rem;font-weight:700;'
            f'text-transform:uppercase;letter-spacing:0.06em;color:{muted}">How trajectories change</span><br>'
            + "<br>".join(chain_parts) + '</div>'
        )

    # 3. LLM-powered analysis
    # Determine category label from the section context
    if all_same_chain:
        only_chain = list(chain_counter.keys())[0]
        chain_labels = only_chain.split("-")
        if all(l == "P" for l in chain_labels):
            cat_label = "stable_passes"
        elif all(l == "F" for l in chain_labels):
            cat_label = "stable_failures"
        else:
            cat_label = "consistent_" + only_chain
    else:
        cats_in_group = Counter(e.category for e in items)
        cat_label = cats_in_group.most_common(1)[0][0] if cats_in_group else "mixed"

    with st.spinner("Analyzing patterns..."):
        llm_analysis = _generate_category_analysis(items, runs, cat_label)
    if llm_analysis:
        sections.append(
            f'<div style="margin-bottom:0.4rem"><span style="font-size:0.7rem;font-weight:700;'
            f'text-transform:uppercase;letter-spacing:0.06em;color:{muted}">Analysis</span><br>'
            f'{llm_analysis}</div>'
        )

    st.markdown(
        f'<div style="background:{card_bg};border:1px solid {card_border};border-left:4px solid {accent};'
        f'border-radius:10px;padding:1rem 1.3rem;margin-bottom:0.8rem;font-size:0.85rem;line-height:1.7;color:{card_text}">'
        + "".join(sections)
        + '</div>', unsafe_allow_html=True)

    # --- agg-table with inline trajectory detail ---
    col_count = 4 + len(runs)
    run_headers = "".join(f"<th>{_h(r.label)}</th>" for r in runs)
    table_html = (
        '<table class="agg-table"><thead><tr>'
        f'<th>Batch</th><th>Query</th><th>Query Text</th>{run_headers}<th>Pattern</th>'
        '</tr></thead><tbody>'
    )
    for e in sorted(items, key=lambda x: (x.batch, x.query_index)):
        chain = e.transition_chain
        run_cells = ""
        for i, run in enumerate(runs):
            outcome = e.outcomes[i] if i < len(e.outcomes) else None
            if outcome is True:
                run_cells += '<td style="color:#059669;font-weight:700">Pass</td>'
            elif outcome is False:
                run_cells += '<td style="color:#dc2626;font-weight:700">Fail</td>'
            else:
                run_cells += f'<td style="color:{muted}">—</td>'
        chain_html_str = _chain_html(chain)
        table_html += (
            f'<tr><td style="font-size:0.82rem">{_h(e.batch[:20])}</td>'
            f'<td style="font-weight:600">Q{e.query_index}</td>'
            f'<td style="font-size:0.82rem;color:{muted};max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{_h(e.query_text[:80])}</td>'
            f'{run_cells}'
            f'<td>{chain_html_str}</td></tr>'
        )
        # Inline detail row
        detail_html = _build_inline_detail_html(e, runs, dark)
        table_html += (
            f'<tr><td colspan="{col_count}" style="padding:0;border-bottom:1px solid {card_border}">'
            f'<details class="evo-row-detail"><summary style="cursor:pointer;font-size:0.72rem;'
            f'font-weight:600;padding:4px 12px;color:{muted}">Trajectory</summary>'
            f'<div style="padding:8px 12px 12px">{detail_html}</div>'
            f'</details></td></tr>'
        )
    table_html += '</tbody></table>'
    st.markdown(
        f'<div class="stat-card-wrap" style="max-height:520px;overflow-y:auto">{table_html}</div>',
        unsafe_allow_html=True,
    )


def _render_stable_page(evolutions: list[QueryEvolution], runs: list[EvolutionRun]):
    passes = [e for e in evolutions if e.category == "stable_pass"]
    failures = [e for e in evolutions if e.category == "stable_fail"]

    run_count = len(runs)
    st.markdown(
        f'<div class="evo-section-strip">Passed in Every Run ({len(passes)}) &mdash; '
        f'these {len(passes)} queries passed across all {run_count} runs</div>',
        unsafe_allow_html=True,
    )
    if passes:
        _render_category_insights(passes, runs, is_pass=True)
    else:
        st.info("No queries passed in every run.")

    st.markdown(
        f'<div class="evo-section-strip">Failed in Every Run ({len(failures)}) &mdash; '
        f'these {len(failures)} queries failed across all {run_count} runs and need investigation</div>',
        unsafe_allow_html=True,
    )
    if failures:
        _render_category_insights(failures, runs, is_pass=False)
    else:
        st.info("No queries failed in every run.")


def _render_changes_page(evolutions: list[QueryEvolution], runs: list[EvolutionRun]):
    improved = [e for e in evolutions if e.category == "improved"]
    regressed = [e for e in evolutions if e.category == "regressed"]
    flaky = [e for e in evolutions if e.category == "flaky"]

    st.markdown(
        f'<div class="evo-section-strip">Were Failing, Now Passing ({len(improved)}) &mdash; '
        f'queries that were broken earlier but got fixed in later runs</div>',
        unsafe_allow_html=True,
    )
    if improved:
        _render_category_insights(improved, runs, is_pass=True)
    else:
        st.info("No queries changed from failing to passing.")

    st.markdown(
        f'<div class="evo-section-strip">Were Passing, Now Failing ({len(regressed)}) &mdash; '
        f'queries that used to work but broke in later runs</div>',
        unsafe_allow_html=True,
    )
    if regressed:
        _render_category_insights(regressed, runs, is_pass=False)
    else:
        st.info("No queries changed from passing to failing.")

    if flaky:
        st.markdown(
            f'<div class="evo-section-strip">Pass in Some Runs, Fail in Others ({len(flaky)}) &mdash; '
            f'inconsistent results that flip between pass and fail across runs</div>',
            unsafe_allow_html=True,
        )
        _render_category_insights(flaky, runs, is_pass=False)

    not_comp = [e for e in evolutions if e.category == "not_comparable"]
    if not_comp:
        st.markdown(
            f'<div class="evo-section-strip">Only Present in One Run ({len(not_comp)}) &mdash; '
            f'queries added or removed between runs, cannot compare</div>',
            unsafe_allow_html=True,
        )
        _render_category_insights(not_comp, runs, is_pass=False)


def _render_insights_page(
    summary: EvolutionSummary,
    evolutions: list[QueryEvolution],
    runs: list[EvolutionRun],
):
    st.markdown('<div class="evo-section-strip">Evolution Narrative</div>', unsafe_allow_html=True)

    with st.spinner("Generating evolution narrative..."):
        narrative = _generate_evolution_narrative(summary, evolutions, runs)

    st.markdown(f'<div class="evo-narrative">{narrative}</div>', unsafe_allow_html=True)

    st.markdown('<div class="evo-section-strip">Error Pattern Evolution</div>', unsafe_allow_html=True)
    error_counters: list[Counter] = [Counter() for _ in runs]
    for run_idx, run in enumerate(runs):
        for c in run.all_cases:
            if not c.passed:
                for et in c.error_types:
                    error_counters[run_idx][et] += 1

    all_error_types = sorted({et for ctr in error_counters for et in ctr})
    if all_error_types:
        import pandas as pd
        err_data = {"Error Type": all_error_types}
        for i, run in enumerate(runs):
            err_data[run.label] = [error_counters[i].get(et, 0) for et in all_error_types]
        st.dataframe(pd.DataFrame(err_data).set_index("Error Type"), use_container_width=True)
    else:
        st.info("No error types found across runs.")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

EVO_PAGES = ["Upload Runs", "Timeline", "Consistent", "Status Changes", "Insights"]


def render_evolution_mode():
    dark = _is_dark()
    st.markdown(_DARK_CSS if dark else _LIGHT_CSS, unsafe_allow_html=True)

    if "evo_runs" not in st.session_state:
        saved_runs, saved_ready, saved_page = _load_evo_state()
        st.session_state["evo_runs"] = saved_runs
        st.session_state["evo_ready"] = saved_ready
        st.session_state["evo_page"] = saved_page

    runs_meta = st.session_state.get("evo_runs", [])

    if not st.session_state.get("evo_ready"):
        _render_upload_page(runs_meta)
        return

    for p in EVO_PAGES:
        if p == "Upload Runs":
            continue
        is_active = p == st.session_state.get("evo_page", "Timeline")
        btn_type = "primary" if is_active else "secondary"
        if st.sidebar.button(p, key=f"evo_nav_{p}", use_container_width=True, type=btn_type):
            st.session_state["evo_page"] = p
            st.rerun()

    st.sidebar.markdown("---")
    if st.sidebar.button("Change Runs", use_container_width=True):
        st.session_state["evo_ready"] = False
        _save_evo_state(st.session_state.get("evo_runs", []), False)
        st.rerun()

    if st.sidebar.button("Clear Cache & Reload", use_container_width=True):
        _load_run.clear()
        cache_path = _evo_cache_path(
            [type("R", (), {"path": rm["path"]})() for rm in runs_meta],
        )
        if os.path.exists(cache_path):
            os.remove(cache_path)
        cache_path2 = _evo_cache_path(
            [type("R", (), {"path": rm["path"]})() for rm in runs_meta],
            "deltas",
        )
        if os.path.exists(cache_path2):
            os.remove(cache_path2)
        st.rerun()

    gem_key = os.environ.get("GEMINI_API_KEY", "")
    groq_key = os.environ.get("GROQ_API_KEY", "")

    runs: list[EvolutionRun] = []
    for i, rm in enumerate(runs_meta):
        try:
            batches, cases, cls = _load_run(rm["path"], gem_key, groq_key)
        except Exception as e:
            st.error(f"Failed to load {rm['label']}: {e}")
            if st.button("Go back"):
                st.session_state["evo_ready"] = False
                st.rerun()
            return
        runs.append(EvolutionRun(
            run_index=i,
            label=rm["label"],
            description=rm.get("description", ""),
            path=rm["path"],
            batches=batches,
            all_cases=cases,
            classifications=cls,
        ))

    evolutions = match_queries_across_runs(runs)
    summary = compute_evolution_summary(evolutions, runs)

    st.markdown(f"""
    <div class="evo-hero">
        <h1>Run Evolution</h1>
        <p>{len(runs)} runs | {summary.total_queries} queries | {summary.pass_rates[0]}% \u2192 {summary.pass_rates[-1]}% pass rate</p>
    </div>
    """, unsafe_allow_html=True)

    page = st.session_state.get("evo_page", "Timeline")

    if page == "Timeline":
        _render_timeline(summary, evolutions, runs)
    elif page == "Consistent":
        _render_stable_page(evolutions, runs)
    elif page == "Status Changes":
        _render_changes_page(evolutions, runs)
    elif page == "Insights":
        _render_insights_page(summary, evolutions, runs)
