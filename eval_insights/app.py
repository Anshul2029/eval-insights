"""Eval Insights Platform -- Streamlit dashboard for evalVNext trajectory analysis."""
from collections import Counter
from datetime import datetime
import hashlib
import html
import io
import json
import os
from pathlib import Path
import re as _re
import sys
import tempfile
import zipfile

from dotenv import load_dotenv
load_dotenv()
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))
if os.environ.get("GOOGLE_GEMINI_API_KEY") and not os.environ.get("GEMINI_API_KEY"):
    os.environ["GEMINI_API_KEY"] = os.environ["GOOGLE_GEMINI_API_KEY"]

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from trajectory_parser import auto_discover_batches, parse_single_json, ParsedEvalCase, describe_script
from evolution import render_evolution_mode
import failure_classifier as fc
from failure_classifier import (
    classify_all,
    classify_recovery_cases,
    generate_insights,
    generate_full_insights,
    generate_narrative,
    generate_executive_summary,
    analyze_trajectory_efficiency,
    extract_assertion_details,
    _tag_value,
    OUTCOME_CATEGORIES,
    TRAJECTORY_CATEGORIES,
)

def extract_grader_evidence(case):
    return fc.extract_grader_evidence(case)

BASE = os.path.dirname(os.path.abspath(__file__))
HISTORY_FILE = os.path.join(BASE, ".streamlit", "history.json")

st.set_page_config(page_title="Eval Insights", layout="wide", initial_sidebar_state="expanded")


def _h(text):
    return html.escape(str(text))


# ---------------------------------------------------------------------------
# CSS — theme-aware
# ---------------------------------------------------------------------------

_SHARED_CSS = """
section[data-testid="stSidebar"] .stButton button {
    text-align: left !important; justify-content: flex-start !important;
    border: none !important; border-radius: 8px !important;
    padding: 0.55rem 1rem !important; font-size: 0.9rem !important;
    box-shadow: none !important;
}
section[data-testid="stSidebar"] .stButton { margin-bottom: -0.5rem; }
header[data-testid="stHeader"] { display: none !important; }
#MainMenu, footer, .stDeployButton { display: none !important; }
.metric-strip { display: grid; grid-template-columns: repeat(4, 1fr); gap: 0.8rem; margin-bottom: 1.2rem; }
.metric-card .label {
    font-size: 0.72rem; font-weight: 600;
    text-transform: uppercase; letter-spacing: 0.08em;
}
.metric-card .value { font-size: 1.9rem; font-weight: 700; margin-top: 0.3rem; }
.ea-row { display: grid; grid-template-columns: 1fr 1fr; gap: 0.8rem; margin-bottom: 0.5rem; }
.ea-col {
    border-radius: 8px; padding: 0.7rem 0.9rem;
    font-family: 'Consolas', 'Monaco', monospace; font-size: 0.8rem;
    line-height: 1.5; word-break: break-word;
}
.ea-label { font-size: 0.68rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 0.3rem; }
.step-timeline { border-left: 3px solid; padding-left: 1rem; margin: 0.5rem 0; }
.step-item { padding: 0.3rem 0; font-size: 0.82rem; position: relative; margin-bottom: 0.2rem; }
.step-item::before {
    content: ''; position: absolute; left: -1.35rem; top: 0.55rem;
    width: 8px; height: 8px; border-radius: 50%;
}
.provider-badge {
    display: inline-block; background: linear-gradient(135deg, #8b5cf6 0%, #6366f1 100%);
    color: white; font-size: 0.78rem; font-weight: 600;
    padding: 0.35rem 0.9rem; border-radius: 20px; margin-bottom: 1rem;
}
.col-label {
    font-size: 0.78rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.07em;
    padding: 0.4rem 0.7rem; border-radius: 6px; margin-bottom: 0.8rem; display: inline-block;
}
"""

_LIGHT_THEME_CSS = """
section[data-testid="stSidebar"] { background: #f8fafc !important; }
section[data-testid="stSidebar"] [data-testid="stSidebarHeader"] { background: #f8fafc !important; }
.loaded-path {
    background: #f1f5f9; border: 1px solid #e2e8f0; border-radius: 8px;
    padding: 0.5rem 0.8rem; font-size: 0.78rem; color: #64748b;
    word-break: break-all; margin-bottom: 0.5rem;
}
.loaded-path strong { color: #1e293b; }
.sidebar-title { color: #1e293b; font-size: 1.15rem; font-weight: 700; padding: 0.3rem 0 1rem 0; }
section[data-testid="stSidebar"] .stButton button[kind="secondary"] {
    background: transparent !important; color: #64748b !important; font-weight: 400 !important;
}
section[data-testid="stSidebar"] .stButton button[kind="secondary"]:hover {
    background: #f1f5f9 !important; color: #1e293b !important;
}
section[data-testid="stSidebar"] .stButton button[kind="primary"] {
    background: #2563eb !important; color: white !important; font-weight: 600 !important;
}
.hero-banner {
    background: linear-gradient(135deg, #0f172a 0%, #1e3a5f 50%, #1e40af 100%);
    color: white; padding: 2.2rem 2.5rem; border-radius: 14px; margin-bottom: 1.5rem;
    position: relative; overflow: hidden;
}
.hero-banner h1 { font-size: 1.8rem; font-weight: 800; margin: 0 0 0.3rem 0; color: white; }
.hero-banner p { font-size: 0.92rem; color: #93c5fd; margin: 0; }
.metric-card {
    background: white; border: 1px solid #e2e8f0; border-radius: 12px; padding: 1rem 1.2rem;
}
.metric-card .label { color: #64748b; }
.metric-card.total .value { color: #1e293b; }
.metric-card.pass .value { color: #059669; }
.metric-card.fail .value { color: #dc2626; }
.metric-card.rate .value {
    background: linear-gradient(135deg, #2563eb, #7c3aed);
    -webkit-background-clip: text; background-clip: text; -webkit-text-fill-color: transparent;
}
.section-strip {
    background: linear-gradient(90deg, #1e3a5f 0%, #1e40af 100%);
    color: white; padding: 0.85rem 1.3rem; border-radius: 8px;
    font-weight: 700; font-size: 0.95rem; margin: 1.5rem 0 1rem 0;
    letter-spacing: 0.02em;
}
.insight-card { border-radius: 10px; padding: 1rem 1.2rem; margin-bottom: 0.7rem; font-size: 0.9rem; line-height: 1.65; }
.insight-card.good { background: #f0fdf4; border-left: 4px solid #059669; color: #065f46; }
.insight-card.bad { background: #fef2f2; border-left: 4px solid #dc2626; color: #991b1b; }
.query-box {
    background: #f0f9ff; border: 1px solid #2563eb; border-radius: 10px; padding: 1.1rem 1.3rem;
    font-size: 0.92rem; color: #1e293b; margin-bottom: 1rem; line-height: 1.7;
}
.ea-expected { background: #f0fdf4; border: 1px solid #059669; color: #065f46; }
.ea-actual { background: #fef2f2; border: 1px solid #dc2626; color: #991b1b; }
.ea-label { color: #64748b; }
.cat-badge {
    display: inline-block; background: #f1f5f9; color: #334155;
    font-size: 0.73rem; font-weight: 600;
    padding: 0.22rem 0.7rem; border-radius: 20px; margin-right: 0.3rem; margin-bottom: 0.5rem;
}
.outcome-badge {
    display: inline-block; background: linear-gradient(135deg, #0891b2, #0d9488);
    color: white; font-size: 0.73rem; font-weight: 600;
    padding: 0.22rem 0.7rem; border-radius: 20px; margin-right: 0.3rem; margin-bottom: 0.5rem;
}
.mod-badge {
    display: inline-block; background: #e2e8f0; color: #334155;
    font-size: 0.7rem; font-weight: 500; padding: 0.18rem 0.55rem;
    border-radius: 20px; margin-right: 0.3rem; margin-bottom: 0.5rem;
}
.traj-bar {
    background: white; border: 1px solid #e2e8f0; border-radius: 8px;
    padding: 0.6rem 1rem; display: flex; gap: 2rem;
    font-size: 0.82rem; color: #64748b; margin-bottom: 0.8rem;
}
.traj-num { font-weight: 700; font-size: 1.05rem; color: #1e293b; }
.col-label.good { background: #f0fdf4; color: #059669; }
.col-label.bad { background: #fef2f2; color: #dc2626; }
.grader-msg {
    background: #f0f9ff; border: 1px solid #2563eb; border-radius: 8px;
    padding: 0.8rem 1rem; font-size: 0.85rem; color: #1e40af;
    line-height: 1.6; margin-bottom: 0.8rem; font-style: italic;
}
.step-timeline { border-color: #e2e8f0; }
.step-item { color: #64748b; }
.step-item::before { background: #94a3b8; }
.step-item.error::before { background: #dc2626; }
.step-item.script::before { background: #2563eb; }
.step-item.assistant::before { background: #7c3aed; }
.agg-table { width:100%; border-collapse:collapse; font-size:0.88rem; }
.agg-table th { background:#1f4e79; color:#fff; padding:8px 12px; text-align:left; font-weight:600; }
.agg-table td { padding:7px 12px; border-bottom:1px solid #dee2e6; }
.agg-table tr:hover td { background:#f0f4f8; }
.desc-btn {
    background:#f1f5f9; border:1px solid #cbd5e1; border-radius:6px; padding:2px 8px;
    font-size:0.72rem; color:#475569; cursor:pointer; position:relative; display:inline-block;
}
.desc-btn:hover .desc-tip {
    visibility:visible; opacity:1;
}
.desc-tip {
    visibility:hidden; opacity:0; position:absolute; bottom:calc(100% + 8px); right:0;
    background:#1e293b; color:#f8fafc; padding:10px 14px; border-radius:8px; font-size:0.78rem;
    line-height:1.6; width:360px; z-index:9999; box-shadow:0 4px 16px rgba(0,0,0,.2);
    transition:opacity 0.15s; text-align:left; font-weight:400;
}
.desc-tip strong { color:#93c5fd; font-weight:600; }
.desc-tip::after {
    content:''; position:absolute; top:100%; right:16px;
    border:6px solid transparent; border-top-color:#1e293b;
}
.stat-card-wrap { border:1px solid #dee2e6; border-radius:8px; overflow:visible; box-shadow:0 1px 3px rgba(0,0,0,.06); margin-bottom:16px; }
.stat-card-hdr { background:#1f4e79; color:#fff; padding:9px 16px; font-size:0.78rem; font-weight:700; letter-spacing:0.06em; text-transform:uppercase; border-radius:8px 8px 0 0; }
"""

_DARK_THEME_CSS = """
.stApp, [data-testid="stAppViewContainer"] { background-color: #0d1117 !important; color: #e6edf3 !important; }
section[data-testid="stSidebar"] { background: #0d1117 !important; }
section[data-testid="stSidebar"] [data-testid="stSidebarHeader"] { background: #0d1117 !important; }
section[data-testid="stSidebar"] * { color: #e6edf3 !important; }
.stTextInput > div > div > input { background: #0d1117 !important; color: #e6edf3 !important; border-color: #30363d !important; }
.stTextArea > div > div > textarea { background: #0d1117 !important; color: #e6edf3 !important; border-color: #30363d !important; }
.stSelectbox > div > div { background: #0d1117 !important; color: #e6edf3 !important; }
[data-testid="stExpander"] { background: #161b22 !important; border-color: #30363d !important; }
[data-testid="stExpander"] * { color: #e6edf3 !important; }
.stMarkdown, .stMarkdown p, h1, h2, h3 { color: #e6edf3 !important; }
hr { border-color: #30363d !important; }
.stAlert { background: #161b22 !important; color: #e6edf3 !important; }
.loaded-path {
    background: #161b22; border: 1px solid #30363d; border-radius: 8px;
    padding: 0.5rem 0.8rem; font-size: 0.78rem; color: #8b949e;
    word-break: break-all; margin-bottom: 0.5rem;
}
.loaded-path strong { color: #e6edf3; }
.sidebar-title { color: #e6edf3; font-size: 1.15rem; font-weight: 700; padding: 0.3rem 0 1rem 0; }
section[data-testid="stSidebar"] .stButton button[kind="secondary"] {
    background: transparent !important; color: #8b949e !important; font-weight: 400 !important;
}
section[data-testid="stSidebar"] .stButton button[kind="secondary"]:hover {
    background: #21262d !important; color: #e6edf3 !important;
}
section[data-testid="stSidebar"] .stButton button[kind="primary"] {
    background: #58a6ff !important; color: #0d1117 !important; font-weight: 600 !important;
}
.hero-banner {
    background: linear-gradient(135deg, #0d1117 0%, #161b22 50%, #1f3a5f 100%);
    color: #e6edf3; padding: 2.2rem 2.5rem; border-radius: 14px; margin-bottom: 1.5rem;
    position: relative; overflow: hidden; border: 1px solid #30363d;
}
.hero-banner h1 { font-size: 1.8rem; font-weight: 800; margin: 0 0 0.3rem 0; color: #e6edf3; }
.hero-banner p { font-size: 0.92rem; color: #58a6ff; margin: 0; }
.metric-card {
    background: linear-gradient(135deg, #161b22 0%, #1c2128 100%);
    border: 1px solid #30363d; border-radius: 12px; padding: 1rem 1.2rem;
}
.metric-card .label { color: #8b949e; }
.metric-card.total .value { color: #e6edf3; }
.metric-card.pass .value { color: #3fb950; }
.metric-card.fail .value { color: #f85149; }
.metric-card.rate .value {
    background: linear-gradient(135deg, #58a6ff, #bc8cff);
    -webkit-background-clip: text; background-clip: text; -webkit-text-fill-color: transparent;
}
.section-strip {
    background: linear-gradient(90deg, #161b22 0%, #21262d 100%);
    color: #e6edf3; padding: 0.85rem 1.3rem; border-radius: 8px;
    font-weight: 700; font-size: 0.95rem; margin: 1.5rem 0 1rem 0;
    border: 1px solid #30363d; letter-spacing: 0.02em;
}
.insight-card { border-radius: 10px; padding: 1rem 1.2rem; margin-bottom: 0.7rem; font-size: 0.9rem; line-height: 1.65; }
.insight-card.good {
    background: linear-gradient(135deg, #0d2818 0%, #0f3520 100%);
    border-left: 4px solid #3fb950; color: #7ee787;
}
.insight-card.bad {
    background: linear-gradient(135deg, #2d1215 0%, #3d1519 100%);
    border-left: 4px solid #f85149; color: #ffa198;
}
.query-box {
    background: linear-gradient(135deg, #0d1117 0%, #161b22 100%);
    border: 1px solid #58a6ff; border-radius: 10px; padding: 1.1rem 1.3rem;
    font-size: 0.92rem; color: #e6edf3; margin-bottom: 1rem; line-height: 1.7;
}
.ea-expected { background: #0d2818; border: 1px solid #238636; color: #7ee787; }
.ea-actual { background: #2d1215; border: 1px solid #da3633; color: #ffa198; }
.ea-label { color: #8b949e; }
.cat-badge {
    display: inline-block; background: linear-gradient(135deg, #21262d, #30363d);
    color: #e6edf3; font-size: 0.73rem; font-weight: 600;
    padding: 0.22rem 0.7rem; border-radius: 20px; margin-right: 0.3rem; margin-bottom: 0.5rem;
}
.outcome-badge {
    display: inline-block; background: linear-gradient(135deg, #0891b2, #0d9488);
    color: white; font-size: 0.73rem; font-weight: 600;
    padding: 0.22rem 0.7rem; border-radius: 20px; margin-right: 0.3rem; margin-bottom: 0.5rem;
}
.mod-badge {
    display: inline-block; background: #484f58; color: #e6edf3;
    font-size: 0.7rem; font-weight: 500; padding: 0.18rem 0.55rem;
    border-radius: 20px; margin-right: 0.3rem; margin-bottom: 0.5rem;
}
.traj-bar {
    background: #161b22; border: 1px solid #30363d; border-radius: 8px;
    padding: 0.6rem 1rem; display: flex; gap: 2rem;
    font-size: 0.82rem; color: #8b949e; margin-bottom: 0.8rem;
}
.traj-num { font-weight: 700; font-size: 1.05rem; color: #e6edf3; }
.col-label.good { background: #0d2818; color: #7ee787; }
.col-label.bad { background: #2d1215; color: #ffa198; }
.grader-msg {
    background: #0d1117; border: 1px solid #58a6ff; border-radius: 8px;
    padding: 0.8rem 1rem; font-size: 0.85rem; color: #79c0ff;
    line-height: 1.6; margin-bottom: 0.8rem; font-style: italic;
}
.step-timeline { border-color: #30363d; }
.step-item { color: #8b949e; }
.step-item::before { background: #484f58; }
.step-item.error::before { background: #f85149; }
.step-item.script::before { background: #58a6ff; }
.step-item.assistant::before { background: #bc8cff; }
.agg-table th { background:#1a3a5c; }
.agg-table td { border-bottom-color:#30363d; color:#e6edf3; }
.agg-table tr:hover td { background:#161b22; }
.desc-btn { background:#21262d; border-color:#30363d; color:#8b949e; }
.desc-tip { background:#0d1117; color:#e6edf3; border:1px solid #30363d; }
.desc-tip strong { color:#58a6ff; }
.desc-tip::after { border-top-color:#0d1117; }
.stat-card-wrap { border-color:#30363d; }
.stat-card-hdr { background:#1a3a5c; }
"""


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

@st.cache_data(show_spinner="Parsing trajectories...", ttl=300)
def load_data(base_path: str, gem_key: str = "", groq_key: str = "", single_json: bool = False):
    if single_json:
        batches = parse_single_json(base_path)
    else:
        batches = auto_discover_batches(base_path)
    all_cases = [c for cases in batches.values() for c in cases]
    classifications = classify_all(all_cases, gemini_key=gem_key)
    recovery_cls = classify_recovery_cases(all_cases)
    ins = generate_insights(classifications)
    full = generate_full_insights(all_cases, classifications)
    narr = generate_narrative(all_cases, classifications, ins, full)
    return batches, all_cases, classifications, recovery_cls, ins, full, narr


# ---------------------------------------------------------------------------
# Directory history
# ---------------------------------------------------------------------------

def _load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE) as f:
                return json.load(f)
        except Exception:
            return []
    return []


def _save_to_history(path):
    history = _load_history()
    path = os.path.normpath(path)
    if path in history:
        history.remove(path)
    history.insert(0, path)
    history = history[:10]
    os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f)


# ---------------------------------------------------------------------------
# Sidebar navigation
# ---------------------------------------------------------------------------

PAGES = ["Load Data", "Dashboard", "Deep Analysis", "Query Explorer"]

if "page" not in st.session_state:
    st.session_state["page"] = "Load Data"

# Force sidebar open and wide in iframe
st.markdown("""<style>
    section[data-testid="stSidebar"] {
        display: flex !important;
        min-width: 300px !important;
        width: 300px !important;
        transform: none !important;
        position: relative !important;
    }
    section[data-testid="stSidebar"] > div {
        width: 300px !important;
        min-width: 300px !important;
    }
    button[data-testid="stSidebarCollapseButton"] { display: none !important; }
    section[data-testid="stSidebar"] .stRadio label,
    section[data-testid="stSidebar"] .stRadio div[role="radiogroup"] label {
        white-space: nowrap !important;
        font-size: 0.85rem !important;
    }
    section[data-testid="stSidebar"] .stButton button {
        white-space: nowrap !important;
        font-size: 0.85rem !important;
        padding: 0.4rem 0.8rem !important;
    }
    .block-container { max-width: 100% !important; }
</style>""", unsafe_allow_html=True)

st.sidebar.markdown('<div class="sidebar-title">Eval Insights</div>', unsafe_allow_html=True)

dark_mode = st.sidebar.toggle("Dark mode", value=False, key="dark_mode")
_theme_css = _DARK_THEME_CSS if dark_mode else _LIGHT_THEME_CSS
st.markdown(f"<style>{_SHARED_CSS}\n{_theme_css}</style>", unsafe_allow_html=True)

_mode = st.sidebar.radio("Mode", ["Single Run", "Run Evolution"], index=0, key="app_mode")

if _mode == "Run Evolution":
    render_evolution_mode()
    st.stop()

for p in PAGES:
    is_active = p == st.session_state["page"]
    btn_type = "primary" if is_active else "secondary"
    if st.sidebar.button(p, key=f"nav_{p}", use_container_width=True, type=btn_type):
        st.session_state["page"] = p
        st.rerun()

page = st.session_state["page"]
st.sidebar.markdown("---")
if st.sidebar.button("Refresh Data", use_container_width=True):
    load_data.clear()
    st.rerun()

if "base_path" not in st.session_state:
    history = _load_history()
    st.session_state["base_path"] = history[0] if history else BASE

base_path = st.session_state["base_path"]

try:
    batches, all_cases, classifications, recovery_classifications, insights, full, narrative = load_data(base_path, os.environ.get("GEMINI_API_KEY", ""), os.environ.get("GROQ_API_KEY", ""), single_json=st.session_state.get("single_json", False))
except Exception:
    if page != "Load Data":
        st.error(f"Failed to load data from: {base_path}. Go to 'Load Data' to select a valid path.")
        st.stop()
    else:
        batches, all_cases, classifications, recovery_classifications, insights, full, narrative = {}, [], [], [], {}, {}, []

if not all_cases and page != "Load Data":
    st.warning("No eval batches found. Go to 'Load Data' to upload or select a directory.")
    st.stop()

st.sidebar.markdown(
    f'<div class="loaded-path"><strong>Loaded:</strong><br>{_h(os.path.basename(base_path))}</div>',
    unsafe_allow_html=True,
)

batch_names = sorted(batches.keys()) if batches else []
sel_batches = st.sidebar.multiselect("Filter by batch", batch_names, default=batch_names) if batch_names else []

groq_key = os.environ.get("GROQ_API_KEY", "")
gemini_key = os.environ.get("GEMINI_API_KEY", "")
if groq_key:
    _llm_label = "Groq (Llama 3.3 70B)"
    _llm_color = "#3fb950"
elif gemini_key:
    _llm_label = "Gemini 2.5 Flash"
    _llm_color = "#58a6ff"
else:
    _llm_label = "None — using rule-based fallback"
    _llm_color = "#f85149"
st.sidebar.markdown(
    f'<div style="font-size:0.75rem;padding:6px 10px;border-radius:6px;'
    f'background:{"#161b22" if st.session_state.get("dark_mode") else "#f1f5f9"};'
    f'margin-bottom:8px">'
    f'<span style="color:{"#8b949e" if st.session_state.get("dark_mode") else "#64748b"}">LLM:</span> '
    f'<span style="color:{_llm_color};font-weight:600">{_llm_label}</span></div>',
    unsafe_allow_html=True,
)
if st.session_state.get("_full_report_html"):
    st.sidebar.download_button(
        "\u2913  Export Full Report",
        data=st.session_state["_full_report_html"],
        file_name=f"eval_insights_{datetime.now():%Y%m%d_%H%M}.html",
        mime="text/html",
        use_container_width=True,
        type="primary",
    )
st.sidebar.markdown("---")

# Apply filters
f_cases = [c for c in all_cases if c.batch in sel_batches]
f_cls = [c for c in classifications if c.batch in sel_batches]
f_passed = [c for c in f_cases if c.passed]
f_failed = [c for c in f_cases if not c.passed]
total_q, total_p, total_f = len(f_cases), len(f_passed), len(f_failed)
pass_rate = total_p / max(total_q, 1) * 100
case_lookup = {(c.batch, c.query_index): c for c in f_cases}


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

CAT_COLORS = ["#dc2626", "#ea580c", "#d97706", "#ca8a04", "#65a30d", "#0891b2", "#7c3aed", "#be185d"]

def _cat_color(cat_name: str) -> str:
    cat_names = sorted(set(c.primary_category for c in classifications))
    idx = cat_names.index(cat_name) if cat_name in cat_names else 0
    return CAT_COLORS[idx % len(CAT_COLORS)]


_traj_evidence_cache: dict[str, str] = {}

def _get_trajectory_evidence_llm(case, traj_category: str, query_text: str) -> dict:
    cache_key = f"{case.batch}:{case.query_index}:{traj_category}"
    if cache_key in _traj_evidence_cache:
        return _traj_evidence_cache[cache_key]
    cache_dir = Path(".cache")
    cache_dir.mkdir(exist_ok=True)
    cache_file = cache_dir / "traj_evidence.json"
    disk_cache: dict = {}
    if cache_file.exists():
        try:
            disk_cache = json.loads(cache_file.read_text(encoding="utf-8"))
            if cache_key in disk_cache:
                _traj_evidence_cache[cache_key] = disk_cache[cache_key]
                return disk_cache[cache_key]
        except (json.JSONDecodeError, KeyError):
            pass

    step_lines = []
    for s in case.steps:
        if s.step_type == "UserQuery":
            step_lines.append(f"Step {s.step_index} [UserQuery]: \"{s.text[:200]}\"")
        elif s.step_type == "ScriptExecution":
            script_preview = s.script_full[:400] if s.script_full else "(empty)"
            step_lines.append(f"Step {s.step_index} [Script]: ```{script_preview}```")
        elif s.step_type == "ScriptResponse":
            if s.error_type:
                step_lines.append(f"Step {s.step_index} [ERROR]: {s.error_type} | console: {s.console[:250] if s.console else 'none'} | result: {s.result[:250] if s.result else 'none'}")
            else:
                step_lines.append(f"Step {s.step_index} [OK]: {s.result[:150] if s.result else 'success'}")
        elif s.step_type == "Assistant":
            step_lines.append(f"Step {s.step_index} [Agent]: \"{s.text[:200]}\"")

    cat_desc = TRAJECTORY_CATEGORIES.get(traj_category, "")
    category_focus = {
        "Tool Misuse": "Find the exact step(s) where the agent used the WRONG API, function, or parameters.",
        "Context Loss": "Find the exact step(s) where the agent forgot or ignored earlier context/state.",
        "Goal Drift": "Find the exact step(s) where the agent started doing something different from the original query.",
        "Retry Loops": "Find the exact steps where the agent REPEATED the same or similar failing approach.",
        "Silent Quality Degradation": "The output has no script errors but the result is wrong. Find WHERE the agent produced the wrong output.",
        "Cascading Errors": "Find the FIRST error that triggered a chain of downstream failures.",
    }
    focus = category_focus.get(traj_category, "")

    if traj_category == "Unclassified" or not focus:
        prompt = f"""You are analyzing an Excel Copilot agent trajectory that failed.
YOUR TASK: Identify what went wrong. Be concise, max 2 sentences per field.
QUERY: "{query_text}"
TRAJECTORY:
{chr(10).join(step_lines)}
Return ONLY valid JSON: {{"issue": "...", "fix": "..."}}"""
    else:
        prompt = f"""You are analyzing an Excel Copilot agent trajectory classified as "{traj_category}" ({cat_desc}).
YOUR TASK: {focus}
Be concise, max 2 sentences per field. ALWAYS provide a concrete fix.
QUERY: "{query_text}"
TRAJECTORY:
{chr(10).join(step_lines)}
Return ONLY valid JSON: {{"issue": "...", "fix": "..."}}"""

    resp, _ = fc._call_llm(prompt, max_tokens=300)
    result = {"issue": "", "fix": ""}
    if resp:
        try:
            cleaned = resp.strip()
            if cleaned.startswith("```"):
                cleaned = _re.sub(r"^```\w*\n?", "", cleaned)
                cleaned = _re.sub(r"\n?```$", "", cleaned)
            parsed = json.loads(cleaned)
            if isinstance(parsed, dict):
                result = {"issue": parsed.get("issue", ""), "fix": parsed.get("fix", "")}
        except (json.JSONDecodeError, ValueError):
            result = {"issue": resp.strip(), "fix": ""}

    if result["issue"]:
        _traj_evidence_cache[cache_key] = result
        disk_cache[cache_key] = result
        try:
            cache_file.write_text(json.dumps(disk_cache, indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
    return result


def _render_failure_card(cr, show_trajectory=False):
    case = case_lookup.get((cr.batch, cr.query_index))
    traj_cats = cr.trajectory_categories if cr.trajectory_categories else [cr.trajectory_category or cr.primary_category]
    badges = ""
    for tc in traj_cats:
        badges += f'<span class="cat-badge">{_h(tc)}</span>'
    if cr.outcome_category:
        badges += f'<span class="outcome-badge">{_h(cr.outcome_category)}</span>'
    for sec in cr.secondary_categories:
        badges += f'<span class="mod-badge">{_h(sec)}</span>'
    conf_colors = {"high": "#059669", "medium": "#d97706", "low": "#94a3b8"}
    conf_color = conf_colors.get(cr.confidence, "#94a3b8")
    badges += (f'<span style="display:inline-block;background:{conf_color};color:white;'
               f'font-size:0.68rem;font-weight:600;padding:0.15rem 0.5rem;border-radius:12px;'
               f'margin-left:0.3rem">{_h(cr.confidence)}</span>')
    st.markdown(f'<div style="margin-bottom:0.3rem">{badges}</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="query-box">{_h(cr.query_text)}</div>', unsafe_allow_html=True)

    if case:
        ev = extract_grader_evidence(case)
        if ev.assertions:
            a = ev.assertions[0]
            exp, act, reason = a.get("expected", ""), a.get("actual", ""), a.get("reason", "")
            assertion_html = ('<div style="max-height:200px;overflow-y:auto;background:#fffbeb;border:1px solid #fcd34d;'
                'border-left:4px solid #f59e0b;border-radius:8px;padding:0.7rem 0.9rem;margin-bottom:0.5rem">'
                '<div style="font-size:0.72rem;font-weight:700;color:#92400e;text-transform:uppercase;'
                'letter-spacing:0.05em;margin-bottom:0.4rem">What the grader found</div>')
            if exp and act and exp != "None" and act != "None":
                assertion_html += (f'<div class="ea-row"><div class="ea-col ea-expected">'
                    f'<div class="ea-label">Expected</div>{_h(exp)}</div>'
                    f'<div class="ea-col ea-actual"><div class="ea-label">Got</div>{_h(act)}</div></div>')
            elif reason:
                assertion_html += f'<div style="font-size:0.82rem;color:#64748b">{_h(reason)}</div>'
            if len(ev.assertions) > 1:
                assertion_html += (f'<div style="font-size:0.75rem;color:#92400e;margin-top:0.3rem;font-style:italic">'
                    f'+ {len(ev.assertions) - 1} more assertion(s)</div>')
            assertion_html += '</div>'
            st.markdown(assertion_html, unsafe_allow_html=True)
        elif ev.grader_message:
            st.markdown(f'<div class="grader-msg">{_h(ev.grader_message)}</div>', unsafe_allow_html=True)

    if cr.why:
        st.markdown(f'<div style="font-size:0.85rem;color:#475569;font-style:italic;margin-bottom:0.5rem">{_h(cr.why)}</div>', unsafe_allow_html=True)

    if case and cr.trajectory_category and cr.trajectory_category != "Unclassified":
        traj_ev = _get_trajectory_evidence_llm(case, cr.trajectory_category, cr.query_text)
        if traj_ev and traj_ev.get("issue"):
            issue_html = (f'<div style="background:#f5f3ff;border:1px solid #ddd6fe;border-left:4px solid #7c3aed;'
                f'border-radius:8px;padding:0.6rem 0.8rem;margin-bottom:0.5rem;font-size:0.82rem">'
                f'<div style="font-weight:600;color:#7c3aed;margin-bottom:0.3rem">What went wrong</div>'
                f'<div style="color:#4c1d95">{_h(traj_ev["issue"])}</div>')
            if traj_ev.get("fix"):
                issue_html += (f'<div style="margin-top:0.4rem;padding-top:0.4rem;border-top:1px solid #ddd6fe">'
                    f'<span style="font-weight:600;color:#059669">Fix:</span> '
                    f'<span style="color:#065f46">{_h(traj_ev["fix"])}</span></div>')
            issue_html += '</div>'
            st.markdown(issue_html, unsafe_allow_html=True)

    if case:
        time_str = f"{case.execution_time_sec:.1f}s" if case.execution_time_sec else "n/a"
        st.markdown(
            f'<div class="traj-bar">'
            f'<div><span class="traj-num">{case.step_count}</span> steps</div>'
            f'<div><span class="traj-num">{case.error_count}</span> errors</div>'
            f'<div><span class="traj-num">{case.script_exec_count}</span> scripts</div>'
            f'<div><span class="traj-num">{time_str}</span> time</div>'
            f'</div>', unsafe_allow_html=True)

    if show_trajectory and case:
        timeline = f'<details><summary style="cursor:pointer;font-size:0.82rem;font-weight:600;color:#7c3aed;margin-bottom:0.3rem">Trajectory Steps ({case.step_count} steps)</summary><div class="step-timeline">'
        for step in case.steps:
            if step.step_type == "UserQuery":
                timeline += f'<div class="step-item"><strong>Step {step.step_index} -- Query:</strong> "{_h(step.text)}"</div>'
            elif step.step_type == "ScriptExecution":
                intent = describe_script(step.script_full)
                timeline += f'<div class="step-item script"><strong>Step {step.step_index} -- Script:</strong> {_h(intent)} ({step.script_len} chars)</div>'
            elif step.step_type == "ScriptResponse":
                if step.error_type:
                    timeline += (f'<div class="step-item error"><strong>Step {step.step_index} -- ERROR:</strong> {_h(step.error_type)}'
                        + (f' -- {_h(step.console)}' if step.console else '') + '</div>')
                else:
                    timeline += f'<div class="step-item"><strong>Step {step.step_index} -- Response:</strong> {_h(step.result or "OK")}</div>'
            elif step.step_type == "Assistant":
                continue
        timeline += '</div></details>'
        st.markdown(timeline, unsafe_allow_html=True)


def _render_success_card(c, recovery_cls_lookup=None):
    if recovery_cls_lookup is None:
        recovery_cls_lookup = {}
    intent = _tag_value(c, "taxonomy.query.intent.primary") or ""
    complexity = _tag_value(c, "taxonomy.query.intent.complexity") or ""
    specificity = _tag_value(c, "taxonomy.query.specificity") or ""

    st.markdown(f'<div class="query-box">{_h(c.query_text)}</div>', unsafe_allow_html=True)

    badges = ""
    rc = recovery_cls_lookup.get((c.batch, c.query_index))
    if rc:
        for tc in (rc.trajectory_categories or [rc.trajectory_category]):
            badges += (f'<span style="background:#7c3aed;color:#fff;padding:2px 10px;'
                       f'border-radius:12px;font-size:0.78rem;font-weight:600;margin-right:4px;">{_h(tc)}</span>')
    if c.error_count > 0:
        badges += (f'<span style="background:#f59e0b;color:#fff;padding:2px 10px;'
                   f'border-radius:12px;font-size:0.78rem;font-weight:600;">'
                   f'Recovered from {c.error_count} error{"s" if c.error_count > 1 else ""}</span> ')
    if c.error_count == 0 and c.step_count < 8:
        badges += (f'<span style="background:#16a34a;color:#fff;padding:2px 10px;'
                   f'border-radius:12px;font-size:0.78rem;font-weight:600;">Golden Trajectory</span> ')
    if badges:
        st.markdown(badges, unsafe_allow_html=True)

    st.markdown(
        f'<div class="traj-bar">'
        f'<span><span class="traj-num">{c.step_count}</span> steps</span>'
        f'<span><span class="traj-num">{c.script_exec_count}</span> scripts</span>'
        f'<span><span class="traj-num">{c.error_count}</span> errors</span>'
        f'<span><span class="traj-num">{c.execution_time_sec:.1f}s</span> time</span>'
        f'</div>', unsafe_allow_html=True)

    if c.error_count > 0 and c.error_types:
        st.markdown(f'<div style="font-size:0.8rem;color:#b45309;margin-top:2px;">Error types: {", ".join(c.error_types)}</div>', unsafe_allow_html=True)

    pills = ""
    if complexity:
        pills += f'<span style="background:#dbeafe;color:#1e40af;padding:2px 8px;border-radius:10px;font-size:0.75rem;margin-right:4px;">{_h(complexity)}</span>'
    if specificity:
        pills += f'<span style="background:#f3e8ff;color:#7c3aed;padding:2px 8px;border-radius:10px;font-size:0.75rem;margin-right:4px;">{_h(specificity)}</span>'
    if intent:
        pills += f'<span style="background:#fef3c7;color:#92400e;padding:2px 8px;border-radius:10px;font-size:0.75rem;">{_h(intent)}</span>'
    if pills:
        st.markdown(pills, unsafe_allow_html=True)

    with st.expander("Show trajectory timeline"):
        timeline = '<div class="step-timeline">'
        for step in c.steps:
            if step.step_type == "UserQuery":
                timeline += f'<div class="step-item"><strong>Step {step.step_index} -- Query:</strong> "{_h(step.text)}"</div>'
            elif step.step_type == "ScriptExecution":
                intent_desc = describe_script(step.script_full)
                timeline += f'<div class="step-item script"><strong>Step {step.step_index} -- Script:</strong> {_h(intent_desc)} ({step.script_len} chars)</div>'
            elif step.step_type == "ScriptResponse":
                if step.error_type:
                    timeline += (f'<div class="step-item error"><strong>Step {step.step_index} -- ERROR (recovered):</strong> {_h(step.error_type)}'
                        + (f' -- {_h(step.console)}' if step.console else '') + '</div>')
                else:
                    timeline += f'<div class="step-item"><strong>Step {step.step_index} -- Response:</strong> {_h(step.result or "OK")}</div>'
            elif step.step_type == "Assistant":
                continue
        timeline += '</div>'
        st.markdown(timeline, unsafe_allow_html=True)


def _build_trajectory_html(case) -> str:
    """Build a compact HTML trajectory timeline for inline table expansion."""
    if not case:
        return ""
    dark = st.session_state.get("dark_mode", False)
    muted = "#8b949e" if dark else "#64748b"
    text_c = "#e6edf3" if dark else "#1e293b"
    err_c = "#f85149" if dark else "#dc2626"
    ok_border = "#28a745"
    script_border = "#6c757d"
    query_border = "#2563eb"

    traj_bar_bg = "#0d1117" if dark else "#f8f9fa"
    traj_bar_border = "#30363d" if dark else "#e2e8f0"

    stats_bar = (
        f'<div style="display:flex;gap:1.5rem;padding:0.5rem 0.8rem;margin-bottom:0.5rem;'
        f'background:{traj_bar_bg};border:1px solid {traj_bar_border};border-radius:6px;font-size:0.82rem">'
        f'<span><strong style="color:{text_c}">{case.step_count}</strong> <span style="color:{muted}">steps</span></span>'
        f'<span><strong style="color:{err_c if case.error_count > 0 else text_c}">{case.error_count}</strong> <span style="color:{muted}">errors</span></span>'
        f'<span><strong style="color:{text_c}">{case.script_exec_count}</strong> <span style="color:{muted}">scripts</span></span>'
        f'<span><strong style="color:{text_c}">{case.execution_time_sec:.1f}s</strong> <span style="color:{muted}">time</span></span>'
        f'</div>')

    timeline = ""
    for step in case.steps:
        if step.step_type == "UserQuery":
            timeline += (f'<div style="border-left:3px solid {query_border};padding:4px 10px;margin:3px 0;'
                         f'font-size:0.78rem;color:{muted}"><strong>Step {step.step_index} — Query:</strong> '
                         f'"{_h(step.text)}"</div>')
        elif step.step_type == "ScriptExecution":
            intent_desc = describe_script(step.script_full)
            timeline += (f'<div style="border-left:3px solid {script_border};padding:4px 10px;margin:3px 0;'
                         f'font-size:0.78rem;color:{muted}"><strong>Step {step.step_index} — Script:</strong> '
                         f'{_h(intent_desc)} ({step.script_len} chars)</div>')
        elif step.step_type == "ScriptResponse":
            if step.error_type:
                timeline += (f'<div style="border-left:3px solid {err_c};padding:4px 10px;margin:3px 0;'
                             f'font-size:0.78rem;color:{err_c}"><strong>Step {step.step_index} — ERROR:</strong> '
                             f'{_h(step.error_type)}'
                             + (f' — {_h(step.console)}' if step.console else '') + '</div>')
            else:
                timeline += (f'<div style="border-left:3px solid {ok_border};padding:4px 10px;margin:3px 0;'
                             f'font-size:0.78rem;color:{muted}"><strong>Step {step.step_index} — OK:</strong> '
                             f'{_h(step.result or "success")}</div>')
        elif step.step_type == "Assistant":
            continue

    return stats_bar + f'<div style="max-height:400px;overflow-y:auto">{timeline}</div>'


def _build_category_query_table(queries_in_cat, cls_lookup_map=None):
    """Build an HTML table with Batch/Query/Query Text/Status columns and ▶ Trajectory dropdowns."""
    if not queries_in_cat:
        return ""
    dark = st.session_state.get("dark_mode", False)
    muted = "#8b949e" if dark else "#64748b"
    card_border = "#30363d" if dark else "#e2e8f0"
    pass_c = "#3fb950" if dark else "#059669"
    fail_c = "#f85149" if dark else "#dc2626"

    if cls_lookup_map is None:
        cls_lookup_map = {}

    table = (
        '<table class="agg-table"><thead><tr>'
        '<th>Batch</th><th>Query</th><th>Query Text</th><th>Status</th>'
        '</tr></thead><tbody>')

    for b, qi, qt in queries_in_cat:
        case = case_lookup.get((b, qi))
        passed = case.passed if case else None
        if passed is True:
            status_html = f'<span style="color:{pass_c};font-weight:700">Pass</span>'
        elif passed is False:
            status_html = f'<span style="color:{fail_c};font-weight:700">Fail</span>'
        else:
            status_html = f'<span style="color:{muted}">—</span>'

        table += (
            f'<tr>'
            f'<td style="font-size:0.82rem">{_h(b[:25])}</td>'
            f'<td style="font-weight:600">Q{qi}</td>'
            f'<td style="font-size:0.82rem;color:{muted};max-width:350px;overflow:hidden;'
            f'text-overflow:ellipsis;white-space:nowrap">{_h(qt[:100])}</td>'
            f'<td>{status_html}</td>'
            f'</tr>')

        # Inline trajectory dropdown row
        detail_html = _build_trajectory_html(case) if case else ""
        if detail_html:
            table += (
                f'<tr><td colspan="4" style="padding:0;border-bottom:1px solid {card_border}">'
                f'<details><summary style="cursor:pointer;font-size:0.72rem;'
                f'font-weight:600;padding:4px 12px;color:{muted}">&#9654; Trajectory</summary>'
                f'<div style="padding:8px 12px 12px">{detail_html}</div>'
                f'</details></td></tr>')

    table += '</tbody></table>'
    return table


# ---------------------------------------------------------------------------
# Chart helpers
# ---------------------------------------------------------------------------

def _chart_colors():
    dark = st.session_state.get("dark_mode", False)
    return {
        "text": "#e6edf3" if dark else "#1e293b",
        "grid": "#30363d" if dark else "#e2e8f0",
        "pass_color": "#3fb950" if dark else "#059669",
        "fail_color": "#f85149" if dark else "#dc2626",
        "accent": "#58a6ff" if dark else "#2563eb",
        "muted": "#8b949e" if dark else "#64748b",
        "card_bg": "#161b22" if dark else "#ffffff",
        "card_border": "#30363d" if dark else "#e2e8f0",
    }


def _base_layout(title="", height=220):
    colors = _chart_colors()
    return dict(
        title=dict(text=title, font=dict(size=13, color=colors["text"]), x=0, xanchor="left"),
        height=height,
        margin=dict(l=0, r=10, t=30, b=0),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color=colors["text"], size=11),
        showlegend=False,
        xaxis=dict(gridcolor=colors["grid"], zerolinecolor=colors["grid"]),
        yaxis=dict(gridcolor=colors["grid"], zerolinecolor=colors["grid"]),
    )


def _show_chart(fig):
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})



def _build_full_report(
    f_cases, f_passed, f_failed, f_cls,
    total_q, total_p, total_f, pass_rate,
    sel_batches, classifications,
    recovery_classifications,
    exec_summary_html, llm_takeaways_html,
    success_insights, failure_insights,
    llm_fail_analysis, llm_recs,
    fc_section_html, recovery_section_html,
    golden_count, recovery_count, false_claim_count,
    wasted_steps_total, wasted_query_count, retry_loop_count,
    avg_steps_p, avg_steps_f, avg_time_p, avg_time_f,
):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    batch_str = ", ".join(sel_batches) if sel_batches else "All"

    def _card(content, border_color):
        return (f'<div class="card" style="border-left:4px solid {border_color}">'
                f'{content}</div>')

    def _table(headers, rows):
        h = "".join(f"<th>{h}</th>" for h in headers)
        r = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
        return f'<table class="tbl"><thead><tr>{h}</tr></thead><tbody>{r}</tbody></table>'

    sections = []

    # ===================== EXECUTIVE SUMMARY =====================
    sections.append('<h2 class="page-hdr">Executive Summary</h2>')
    if exec_summary_html:
        sections.append(f'<div class="card" style="border-left:5px solid #1f4e79">{exec_summary_html}</div>')
    if llm_takeaways_html:
        sections.append('<h3>Key Takeaways</h3>')
        sections.append(f'<div class="takeaways">{llm_takeaways_html}</div>')

    # ===================== DASHBOARD =====================
    sections.append('<h2 class="page-hdr">Dashboard</h2>')

    # KPI strip
    sections.append(f"""
    <div class="kpi-strip">
        <div class="kpi-item"><div class="kpi-val">{total_q}</div><div class="kpi-lbl">Total</div></div>
        <div class="kpi-item"><div class="kpi-val pass">{total_p}</div><div class="kpi-lbl">Passed</div></div>
        <div class="kpi-item"><div class="kpi-val fail">{total_f}</div><div class="kpi-lbl">Failed</div></div>
        <div class="kpi-item"><div class="kpi-val">{pass_rate:.1f}%</div><div class="kpi-lbl">Pass Rate</div></div>
    </div>""")

    # Batch breakdown
    batch_rows = []
    for bn in sorted(sel_batches):
        bc = [c for c in f_cases if c.batch == bn]
        bp = sum(1 for c in bc if c.passed)
        bf = sum(1 for c in bc if not c.passed)
        bt = len(bc)
        rate = bp / max(bt, 1) * 100
        batch_rows.append([_h(bn), str(bt),
                          f'<span class="pass">{bp}</span>',
                          f'<span class="fail">{bf}</span>',
                          f'<strong>{rate:.0f}%</strong>'])
    if batch_rows:
        sections.append('<h3>Batch Breakdown</h3>')
        sections.append(_table(["Batch", "Total", "Pass", "Fail", "Rate"], batch_rows))

    # Key Metrics
    golden_all = [c for c in f_passed if c.error_count == 0 and c.step_count < 8]
    sections.append('<h3>Key Metrics</h3>')
    sections.append(_table(
        ["Avg Steps (Pass)", "Avg Steps (Fail)", "Avg Time (Pass)", "Avg Time (Fail)", "Golden Runs"],
        [[f'<span class="pass">{avg_steps_p:.1f}</span>',
          f'<span class="fail">{avg_steps_f:.1f}</span>',
          f'<span class="pass">{avg_time_p:.1f}s</span>',
          f'<span class="fail">{avg_time_f:.1f}s</span>',
          f'<span class="pass">{len(golden_all)}</span>']]
    ))

    # Error types
    err_counts: dict[str, int] = {}
    err_queries: dict[str, set] = {}
    for c in f_cases:
        for et in c.error_types:
            err_counts[et] = err_counts.get(et, 0) + 1
            err_queries.setdefault(et, set()).add((c.batch, c.query_index))
    if err_counts:
        err_rows = []
        for et, cnt in sorted(err_counts.items(), key=lambda x: -x[1])[:10]:
            err_rows.append([_h(et), str(cnt), str(len(err_queries.get(et, set())))])
        sections.append('<h3>Error Type Analysis</h3>')
        sections.append(_table(["Error Type", "Occurrences", "Queries Affected"], err_rows))

    # Trajectory categories
    traj_cat_dist: dict[str, int] = {}
    for cr in f_cls:
        for tc in (cr.trajectory_categories or [cr.primary_category]):
            traj_cat_dist[tc] = traj_cat_dist.get(tc, 0) + 1
    if traj_cat_dist:
        tcat_rows = []
        for cat, cnt in sorted(traj_cat_dist.items(), key=lambda x: -x[1]):
            pct = f"{round(cnt / max(total_f, 1) * 100)}%"
            tcat_rows.append([f'<strong>{_h(cat)}</strong>', str(cnt), pct])
        sections.append('<h3>Failure Trajectory Categories</h3>')
        sections.append(_table(["Category", "Count", "% of Failures"], tcat_rows))

    # Outcome categories
    outc_dist: dict[str, int] = {}
    for cr in f_cls:
        oc = cr.outcome_category or "Unclassified"
        outc_dist[oc] = outc_dist.get(oc, 0) + 1
    if outc_dist and not (len(outc_dist) == 1 and "Unclassified" in outc_dist):
        ocat_rows = []
        for ocat, cnt in sorted(outc_dist.items(), key=lambda x: -x[1]):
            pct = f"{round(cnt / max(total_f, 1) * 100)}%"
            ocat_rows.append([f'<strong>{_h(ocat)}</strong>', str(cnt), pct])
        sections.append('<h3>Outcome Categories</h3>')
        sections.append(_table(["Category", "Count", "% of Failures"], ocat_rows))

    # What works well / poorly
    case_lookup_r = {(c.batch, c.query_index): c for c in f_cases}
    intent_pass: dict[str, dict] = {}
    for c in f_cases:
        intent = _tag_value(c, "taxonomy.query.intent.primary") or "Unknown"
        intent_pass.setdefault(intent, {"p": 0, "f": 0})
        intent_pass[intent]["p" if c.passed else "f"] += 1

    well_items = []
    poor_items = []
    for intent, stats in intent_pass.items():
        t = stats["p"] + stats["f"]
        if t < 3:
            continue
        pr = stats["p"] / t * 100
        if pr >= 85:
            well_items.append(f'<strong>{_h(intent)}</strong> — {pr:.0f}% pass rate ({stats["p"]}/{t})')
        fr = stats["f"] / t * 100
        if fr >= 30:
            poor_items.append(f'<strong>{_h(intent)}</strong> — {fr:.0f}% fail rate ({stats["f"]}/{t})')

    if well_items:
        sections.append('<h3>What Works Well</h3>')
        for w in well_items[:6]:
            sections.append(_card(w, "#059669"))
    if poor_items:
        sections.append('<h3>What Works Poorly</h3>')
        for p in poor_items[:5]:
            sections.append(_card(p, "#dc2626"))

    # ===================== DEEP ANALYSIS =====================
    sections.append('<h2 class="page-hdr">Deep Analysis</h2>')

    # Overview KPI
    sections.append(f"""
    <table class="tbl">
    <thead><tr><th>Total</th><th>Passed</th><th>Failed</th><th>Recovery</th>
    <th>Golden</th><th>False Claims</th><th>Wasted Steps</th></tr></thead>
    <tbody><tr>
    <td><strong>{total_q}</strong></td>
    <td class="pass"><strong>{total_p}</strong></td>
    <td class="fail"><strong>{total_f}</strong></td>
    <td class="warn"><strong>{recovery_count}</strong></td>
    <td class="pass"><strong>{golden_count}</strong></td>
    <td class="fail"><strong>{false_claim_count}</strong></td>
    <td class="fail"><strong>{wasted_steps_total}</strong></td>
    </tr></tbody></table>
    <p class="meta">Retry-loop failures: {retry_loop_count} &nbsp;|&nbsp;
    Queries with wasted steps: {wasted_query_count}</p>
    """)

    if success_insights:
        sections.append("<h3>Success Analysis</h3>")
        for si in success_insights:
            sections.append(_card(si, "#059669"))

    if failure_insights:
        sections.append("<h3>Failure Analysis</h3>")
        for fi in failure_insights:
            sections.append(_card(fi, "#dc2626"))

    if llm_fail_analysis:
        sections.append('<h3>Root Cause Analysis</h3>')
        sections.append(_card(llm_fail_analysis, "#1f4e79"))

    if fc_section_html:
        sections.append('<h3>False Success Claims</h3>')
        sections.append(_card(fc_section_html, "#dc2626"))

    if recovery_section_html:
        sections.append('<h3>Recovery Analysis</h3>')
        sections.append(recovery_section_html)

    if llm_recs:
        sections.append('<h3>Recommendations</h3>')
        sections.append(_card(llm_recs, "#1f4e79"))

    # ===================== QUERY EXPLORER (summary table) =====================
    sections.append('<h2 class="page-hdr">Query Details</h2>')

    cls_lookup_r = {(cr.batch, cr.query_index): cr for cr in f_cls}
    q_rows = []
    for c in sorted(f_cases, key=lambda c: (c.passed, -c.error_count, c.batch, c.query_index)):
        status = '<span class="pass">PASS</span>' if c.passed else '<span class="fail">FAIL</span>'
        if c.passed and c.error_count > 0:
            status = '<span class="warn">RECOVERED</span>'

        cr = cls_lookup_r.get((c.batch, c.query_index))
        cats = ""
        if cr:
            cats = ", ".join(cr.trajectory_categories or [cr.primary_category])
            if cr.outcome_category:
                cats += f" | {cr.outcome_category}"

        why = ""
        if cr and cr.why:
            why = _h(cr.why)

        q_rows.append([
            status,
            _h(c.batch),
            f"Q{c.query_index}",
            f'<span class="query-text">{_h(c.query_text)}</span>',
            str(c.step_count),
            str(c.error_count),
            f"{c.execution_time_sec:.0f}s",
            f'<span class="cats">{_h(cats)}</span>' if cats else "",
            f'<span class="why">{why}</span>' if why else "",
        ])

    sections.append(_table(
        ["Status", "Batch", "Q#", "Query", "Steps", "Errors", "Time", "Categories", "Root Cause"],
        q_rows
    ))

    body = "\n".join(sections)

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Eval Insights Report — {ts}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  background:#f8f9fa;color:#1e293b;line-height:1.6;padding:2rem 3rem;max-width:1200px;margin:0 auto}}
h1{{font-size:1.6rem;color:#1f4e79;margin-bottom:0.2rem}}
h2.page-hdr{{font-size:1.2rem;color:#fff;background:#1f4e79;padding:10px 18px;margin:2.5rem 0 1rem;
  border-radius:8px;letter-spacing:0.03em}}
h3{{font-size:1rem;color:#1f4e79;margin:1.5rem 0 0.5rem;padding-bottom:0.25rem;
  border-bottom:2px solid #e2e8f0}}
.meta{{font-size:0.82rem;color:#64748b;margin:0.4rem 0 0.8rem}}
.card{{background:#fff;border:1px solid #e2e8f0;border-radius:10px;
  padding:0.9rem 1.2rem;margin-bottom:0.5rem;font-size:0.88rem;line-height:1.7}}
.takeaways .takeaway{{background:#fff;border:1px solid #e2e8f0;border-left:4px solid #1f4e79;
  border-radius:10px;padding:0.9rem 1.2rem;margin-bottom:0.5rem;font-size:0.88rem;line-height:1.7}}
table.tbl{{width:100%;border-collapse:collapse;margin:0.5rem 0 1rem;font-size:0.85rem}}
table.tbl th{{background:#1f4e79;color:#fff;padding:8px 12px;text-align:left;font-weight:600}}
table.tbl td{{padding:7px 12px;border-bottom:1px solid #e2e8f0;vertical-align:top}}
table.tbl tr:hover td{{background:#f0f4f8}}
.kpi-strip{{display:flex;gap:1rem;margin:0.5rem 0 1.5rem}}
.kpi-item{{flex:1;background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:1rem;text-align:center}}
.kpi-val{{font-size:1.5rem;font-weight:800}} .kpi-lbl{{font-size:0.78rem;color:#64748b;margin-top:2px}}
.pass{{color:#059669;font-weight:700}} .fail{{color:#dc2626;font-weight:700}}
.warn{{color:#d97706;font-weight:700}}
.query-text{{font-size:0.82rem;line-height:1.4;display:block;max-width:350px}}
.cats{{font-size:0.78rem;color:#7c3aed}} .why{{font-size:0.78rem;color:#64748b}}
.footer{{margin-top:2.5rem;padding-top:1rem;border-top:1px solid #e2e8f0;
  font-size:0.75rem;color:#94a3b8;text-align:center}}
ul{{margin:0.3rem 0 0.3rem 1.2rem}} li{{margin-bottom:0.3rem}}
@media print{{body{{padding:1rem}} .kpi-strip{{gap:0.5rem}} h2.page-hdr{{break-before:page}}}}
</style></head><body>
<h1>Eval Insights Report</h1>
<p class="meta">Generated {ts} &nbsp;|&nbsp; Batches: {_h(batch_str)} &nbsp;|&nbsp;
{total_q} queries, {total_p} passed, {total_f} failed ({pass_rate:.1f}%)</p>
{body}
<div class="footer">Eval Insights Platform — evidence-based trajectory analysis</div>
</body></html>"""


# Hero banner on all pages except Load Data
if page != "Load Data":
    st.markdown(f"""
    <div class="hero-banner" style="padding:1.2rem 2rem">
        <h1 style="font-size:1.5rem">Eval Insights Platform</h1>
        <p>{len(batches)} batches | {total_q} queries | {pass_rate:.1f}% pass rate</p>
    </div>
    """, unsafe_allow_html=True)

    st.markdown(f"""
    <div class="metric-strip">
      <div class="metric-card total"><div class="label">Total Queries</div><div class="value">{total_q}</div></div>
      <div class="metric-card pass"><div class="label">Passed</div><div class="value">{total_p}</div></div>
      <div class="metric-card fail"><div class="label">Failed</div><div class="value">{total_f}</div></div>
      <div class="metric-card rate"><div class="label">Pass Rate</div><div class="value">{pass_rate:.1f}%</div></div>
    </div>
    """, unsafe_allow_html=True)

    # Build report eagerly so download is always available
    if not st.session_state.get("_full_report_html"):
        _avg_sp = sum(c.step_count for c in f_passed) / max(len(f_passed), 1) if f_passed else 0
        _avg_sf = sum(c.step_count for c in f_failed) / max(len(f_failed), 1) if f_failed else 0
        _avg_tp = sum(c.execution_time_sec for c in f_passed) / max(len(f_passed), 1) if f_passed else 0
        _avg_tf = sum(c.execution_time_sec for c in f_failed) / max(len(f_failed), 1) if f_failed else 0
        _golden = [c for c in f_passed if c.error_count == 0 and c.step_count < 8]
        _recovery = [c for c in f_passed if c.error_count > 0]
        _false_claims = [c for c in f_failed if c.has_success_claim]
        st.session_state["_full_report_html"] = _build_full_report(
            f_cases=f_cases, f_passed=f_passed, f_failed=f_failed, f_cls=f_cls,
            total_q=total_q, total_p=total_p, total_f=total_f, pass_rate=pass_rate,
            sel_batches=sel_batches, classifications=classifications,
            recovery_classifications=recovery_classifications,
            exec_summary_html="", llm_takeaways_html="",
            success_insights=[], failure_insights=[],
            llm_fail_analysis=None, llm_recs=None,
            fc_section_html="", recovery_section_html="",
            golden_count=len(_golden), recovery_count=len(_recovery),
            false_claim_count=len(_false_claims),
            wasted_steps_total=0, wasted_query_count=0, retry_loop_count=0,
            avg_steps_p=_avg_sp, avg_steps_f=_avg_sf,
            avg_time_p=_avg_tp, avg_time_f=_avg_tf,
        )



# ===================================================================
# PAGE: Load Data
# ===================================================================

if page == "Load Data":
    st.header("Load Eval Results")

    source = st.radio("Source", ["Folder path", "Upload ZIP", "Single JSON file"], horizontal=True)

    if source == "Upload ZIP":
        uploaded = st.file_uploader("Upload a ZIP of eval results (up to 1 GB)", type=["zip"])
        if uploaded:
            zip_hash = hashlib.md5(uploaded.getvalue()).hexdigest()[:10]
            extract_dir = os.path.join(tempfile.gettempdir(), f"eval_{zip_hash}")
            if not os.path.exists(extract_dir):
                with zipfile.ZipFile(io.BytesIO(uploaded.getvalue())) as zf:
                    zf.extractall(extract_dir)
                st.success(f"Uploaded and extracted successfully: {uploaded.name}")
            if st.session_state.get("base_path") != extract_dir:
                st.session_state["base_path"] = extract_dir
                st.session_state["single_json"] = False
                _save_to_history(extract_dir)
                st.rerun()
    elif source == "Single JSON file":
        uploaded_json = st.file_uploader("Upload a playOutput JSON report", type=["json"])
        json_path_input = st.text_input("Or enter path to a JSON file", value="")
        if uploaded_json:
            json_hash = hashlib.md5(uploaded_json.getvalue()).hexdigest()[:10]
            tmp_path = os.path.join(tempfile.gettempdir(), f"eval_single_{json_hash}.json")
            if not os.path.exists(tmp_path):
                with open(tmp_path, "wb") as f:
                    f.write(uploaded_json.getvalue())
                st.success(f"Uploaded: {uploaded_json.name}")
            if st.session_state.get("base_path") != tmp_path:
                st.session_state["base_path"] = tmp_path
                st.session_state["single_json"] = True
                st.rerun()
        elif st.button("Load JSON"):
            path = json_path_input.strip()
            if path and os.path.isfile(path) and path.endswith(".json"):
                st.session_state["base_path"] = path
                st.session_state["single_json"] = True
                _save_to_history(path)
                st.rerun()
            else:
                st.error("Invalid JSON file path.")
    else:
        history = _load_history()
        if history:
            selected = st.selectbox("Recent paths", history)
        else:
            selected = ""
        new_path = st.text_input("Or enter a new folder path", value="")
        if st.button("Load"):
            path = new_path.strip() if new_path.strip() else selected
            if path and os.path.isdir(path):
                st.session_state["base_path"] = path
                st.session_state["single_json"] = False
                _save_to_history(path)
                st.rerun()
            else:
                st.error("Invalid directory path.")



# ===================================================================
# PAGE: Dashboard (one-screen patterns + charts)
# ===================================================================

elif page == "Dashboard":

    dark = st.session_state.get("dark_mode", False)
    colors = _chart_colors()
    label_color = "#8b949e" if dark else "#64748b"
    pct_color = "#8b949e" if dark else "#94a3b8"
    text_muted = colors["muted"]

    # --- Theme-aware card styles ---
    well_bg = "linear-gradient(135deg,#0d2818,#0f3520)" if dark else "linear-gradient(135deg,#f0fdf4,#dcfce7)"
    well_border = "#3fb950" if dark else "#22c55e"
    well_title = "#7ee787" if dark else "#15803d"
    well_sub = "#56d364" if dark else "#166534"
    poor_bg = "linear-gradient(135deg,#2d1215,#3d1519)" if dark else "linear-gradient(135deg,#fef2f2,#fee2e2)"
    poor_border = "#f85149" if dark else "#ef4444"
    poor_title = "#ffa198" if dark else "#991b1b"
    poor_sub = "#ffa198" if dark else "#991b1b"
    poor_badge_bg = "#3d1519" if dark else "#fef2f2"
    poor_spec_color = "#d29922" if dark else "#92400e"
    poor_example_color = "#8b949e" if dark else "#666"
    poor_divider = "#f8514930" if dark else "#fecaca"

    # ---------------------------------------------------------------
    # 1. Efficiency + Breakdown (full width, stacked)
    # ---------------------------------------------------------------
    st.markdown('<div class="section-strip">Efficiency &amp; Breakdown</div>', unsafe_allow_html=True)

    kpi_good = colors["pass_color"]
    kpi_bad = colors["fail_color"]
    kpi_warn = "#d29922" if dark else "#d97706"

    batch_rows = []
    for bn in sorted(sel_batches):
        bc = [c for c in f_cases if c.batch == bn]
        bp = sum(1 for c in bc if c.passed)
        bf = sum(1 for c in bc if not c.passed)
        bt = len(bc)
        batch_rows.append({"name": bn, "total": bt, "pass": bp, "fail": bf, "rate": bp / max(bt, 1) * 100})
    if batch_rows:
        batch_html = ('<div class="stat-card-wrap"><div class="stat-card-hdr">Batch Breakdown</div>'
                      '<table class="agg-table"><thead><tr><th>Batch</th><th>Total</th><th>Pass</th><th>Fail</th><th>Rate</th></tr></thead><tbody>')
        for r in sorted(batch_rows, key=lambda x: x["rate"]):
            rate_color = kpi_good if r["rate"] >= 80 else kpi_warn if r["rate"] >= 50 else kpi_bad
            batch_html += (f'<tr><td style="font-weight:600">{_h(r["name"])}</td><td>{r["total"]}</td>'
                           f'<td style="color:{kpi_good};font-weight:600">{r["pass"]}</td>'
                           f'<td style="color:{kpi_bad};font-weight:600">{r["fail"]}</td>'
                           f'<td style="color:{rate_color};font-weight:700">{r["rate"]:.0f}%</td></tr>')
        batch_html += '</tbody></table></div>'
        st.markdown(batch_html, unsafe_allow_html=True)

    avg_steps_p = sum(c.step_count for c in f_passed) / max(len(f_passed), 1) if f_passed else 0
    avg_steps_f = sum(c.step_count for c in f_failed) / max(len(f_failed), 1) if f_failed else 0
    avg_time_p = sum(c.execution_time_sec for c in f_passed) / max(len(f_passed), 1) if f_passed else 0
    avg_time_f = sum(c.execution_time_sec for c in f_failed) / max(len(f_failed), 1) if f_failed else 0
    golden = [c for c in f_passed if c.error_count == 0 and c.step_count < 8]

    stats_html = (
        f'<div class="stat-card-wrap"><div class="stat-card-hdr">Key Metrics</div>'
        f'<table class="agg-table"><thead><tr>'
        f'<th>Avg Steps (Pass)</th><th>Avg Steps (Fail)</th><th>Avg Time (Pass)</th><th>Avg Time (Fail)</th><th>Golden Runs</th>'
        f'</tr></thead><tbody><tr>'
        f'<td style="font-weight:700;color:{kpi_good}">{avg_steps_p:.1f}</td>'
        f'<td style="font-weight:700;color:{kpi_bad}">{avg_steps_f:.1f}</td>'
        f'<td style="font-weight:700;color:{kpi_good}">{avg_time_p:.1f}s</td>'
        f'<td style="font-weight:700;color:{kpi_bad}">{avg_time_f:.1f}s</td>'
        f'<td style="font-weight:700;color:{kpi_good}">{len(golden)}</td>'
        f'</tr></tbody></table></div>'
    )
    st.markdown(stats_html, unsafe_allow_html=True)

    # --- Error Type Analysis (agg-table styled) ---
    error_counts: dict[str, dict] = {}
    error_type_queries: dict[str, set] = {}
    for c in f_cases:
        for et in c.error_types:
            error_counts.setdefault(et, {"recovered": 0, "failed": 0})
            error_counts[et]["recovered" if c.passed else "failed"] += 1
            error_type_queries.setdefault(et, set()).add((c.batch, c.query_index))
    if error_counts:
        err_sorted = sorted(error_counts.items(), key=lambda x: -(x[1]["recovered"] + x[1]["failed"]))[:10]
        err_html = ('<div class="stat-card-wrap"><div class="stat-card-hdr">Error Type Analysis</div>'
                    '<table class="agg-table"><thead><tr><th>Error Type</th><th>Occurrences</th><th>Queries Affected</th></tr></thead><tbody>')
        for et, d in err_sorted:
            total_occ = d["recovered"] + d["failed"]
            q_count = len(error_type_queries.get(et, set()))
            err_html += f'<tr><td>{_h(et)}</td><td>{total_occ}</td><td>{q_count}</td></tr>'
        err_html += '</tbody></table></div>'
        st.markdown(err_html, unsafe_allow_html=True)

    # ---------------------------------------------------------------
    # 2. Failure Analysis — Trajectory Categories (agg-table)
    # ---------------------------------------------------------------

    traj_cat_dist: dict[str, int] = {}
    traj_cat_queries: dict[str, list[tuple[str, str, str]]] = {}
    for cr in f_cls:
        for tc in (cr.trajectory_categories or [cr.primary_category]):
            traj_cat_dist[tc] = traj_cat_dist.get(tc, 0) + 1
            traj_cat_queries.setdefault(tc, []).append((cr.batch, cr.query_index, cr.query_text))

    traj_cat_total: dict[str, int] = {}
    for cr in f_cls:
        for tc in (cr.trajectory_categories or [cr.primary_category]):
            traj_cat_total[tc] = traj_cat_total.get(tc, 0) + 1
    f_recovery_cls_for_cats = [rc for rc in recovery_classifications if rc.batch in sel_batches] if recovery_classifications else []
    for rc in f_recovery_cls_for_cats:
        for tc in (rc.trajectory_categories or []):
            traj_cat_total[tc] = traj_cat_total.get(tc, 0) + 1

    if traj_cat_dist:
        traj_cats_sorted = sorted(traj_cat_dist.items(), key=lambda x: -x[1])
        tcat_html = ('<div class="stat-card-wrap"><div class="stat-card-hdr">Failure Analysis — Trajectory Categories</div>'
                     '<table class="agg-table"><thead><tr><th>Category</th><th>In Failures</th><th>%</th><th>Total (all queries)</th><th></th></tr></thead><tbody>')
        for cat_name, count in traj_cats_sorted:
            pct = f"{round(count / max(total_f, 1) * 100)}%"
            total_count = traj_cat_total.get(cat_name, count)
            cat_desc = TRAJECTORY_CATEGORIES.get(cat_name, "")
            if cat_desc:
                requires = ""
                if "REQUIRES:" in cat_desc:
                    parts = cat_desc.split("REQUIRES:")
                    desc_part = parts[0].strip()
                    requires = parts[1].strip()
                else:
                    desc_part = cat_desc
                tip_content = f'<strong>{_h(cat_name)}</strong><br>{_h(desc_part)}'
                if requires:
                    tip_content += f'<br><br><strong>How it\'s detected:</strong> {_h(requires)}'
                tip_html = f'<span class="desc-btn">info<span class="desc-tip">{tip_content}</span></span>'
            else:
                tip_html = ""
            tcat_html += (f'<tr><td style="font-weight:600">{_h(cat_name)}</td>'
                          f'<td>{count}</td><td>{pct}</td>'
                          f'<td>{total_count}</td>'
                          f'<td>{tip_html}</td></tr>')
        tcat_html += '</tbody></table></div>'
        st.markdown(tcat_html, unsafe_allow_html=True)

        for cat_name, _ in traj_cats_sorted:
            queries_in_cat = traj_cat_queries.get(cat_name, [])
            if queries_in_cat:
                with st.expander(f"{cat_name} — {len(queries_in_cat)} queries"):
                    cat_table = _build_category_query_table(queries_in_cat)
                    st.markdown(f'<div style="max-height:520px;overflow-y:auto">{cat_table}</div>', unsafe_allow_html=True)

    # ---------------------------------------------------------------
    # 3. Outcome Categories (agg-table)
    # ---------------------------------------------------------------

    outc_dist: dict[str, int] = {}
    outc_queries: dict[str, list[tuple[str, str, str]]] = {}
    for cr in f_cls:
        oc = cr.outcome_category or "Unclassified"
        outc_dist[oc] = outc_dist.get(oc, 0) + 1
        outc_queries.setdefault(oc, []).append((cr.batch, cr.query_index, cr.query_text))

    if outc_dist and not (len(outc_dist) == 1 and "Unclassified" in outc_dist):
        outc_items = sorted(outc_dist.items(), key=lambda x: -x[1])
        outc_html = ('<div class="stat-card-wrap"><div class="stat-card-hdr">Outcome Categories</div>'
                     '<table class="agg-table"><thead><tr><th>Category</th><th>Count</th><th>%</th><th></th></tr></thead><tbody>')
        for ocat, ocount in outc_items:
            opct = f"{round(ocount / max(total_f, 1) * 100)}%"
            odesc = OUTCOME_CATEGORIES.get(ocat, "")
            if odesc:
                tip_content = (f'<strong>{_h(ocat)}</strong><br>{_h(odesc)}'
                               f'<br><br><strong>How it\'s detected:</strong> LLM judge compares the agent\'s final output against the expected answer and classifies the mismatch type.')
                tip_html = f'<span class="desc-btn">info<span class="desc-tip">{tip_content}</span></span>'
            else:
                tip_html = ""
            outc_html += (f'<tr><td style="font-weight:600">{_h(ocat)}</td>'
                          f'<td>{ocount}</td><td>{opct}</td>'
                          f'<td>{tip_html}</td></tr>')
        outc_html += '</tbody></table></div>'
        st.markdown(outc_html, unsafe_allow_html=True)

        for ocat, _ in outc_items:
            oq_list = outc_queries.get(ocat, [])
            if oq_list:
                with st.expander(f"{ocat} — {len(oq_list)} queries"):
                    ocat_table = _build_category_query_table(oq_list)
                    st.markdown(f'<div style="max-height:520px;overflow-y:auto">{ocat_table}</div>', unsafe_allow_html=True)
    elif not f_cls:
        st.success("No failures to analyze.")
    else:
        groq_key_check = os.environ.get("GROQ_API_KEY", "")
        if not gemini_key and not groq_key_check:
            st.info("Set GROQ_API_KEY or GEMINI_API_KEY to enable LLM-powered outcome classification.")



# ===================================================================
# PAGE: Deep Analysis (recovery, false claims, wasted steps, recommendations)
# ===================================================================

elif page == "Deep Analysis":

    dark = st.session_state.get("dark_mode", False)
    colors = _chart_colors()
    kpi_good = colors["pass_color"]
    kpi_bad = colors["fail_color"]
    kpi_warn = "#d29922" if dark else "#d97706"
    text_muted = colors["muted"]
    card_bg = "#161b22" if dark else "white"
    card_border = "#30363d" if dark else "#e2e8f0"
    card_text = "#e6edf3" if dark else "#1e293b"
    card_muted = "#8b949e" if dark else "#64748b"
    accent = "#58a6ff" if dark else "#1f4e79"

    # --- Generate Executive Summary + Takeaways silently (for downloaded report only) ---
    if not st.session_state.get("_exec_summary_html"):
        f_recovery_cls = [rc for rc in recovery_classifications if rc.batch in sel_batches] if recovery_classifications else []
        exec_summary = generate_executive_summary(f_cases, f_cls, f_recovery_cls)
        if exec_summary:
            st.session_state["_exec_summary_html"] = exec_summary

    if not st.session_state.get("_llm_takeaways_html"):
        _takeaway_cache_key = f"exec_takeaways_{hashlib.md5(base_path.encode()).hexdigest()[:10]}"
        _takeaway_cache_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache", f"{_takeaway_cache_key}.json")
        _cached_tw = None
        if os.path.exists(_takeaway_cache_path):
            try:
                with open(_takeaway_cache_path) as _cf:
                    _cached_tw = json.load(_cf).get("takeaways")
            except Exception:
                pass
        if _cached_tw:
            st.session_state["_llm_takeaways_html"] = _cached_tw
        else:
            _pd = [c for c in f_cases if c.passed]
            _fd = [c for c in f_cases if not c.passed]
            _td = Counter(tc for cr in f_cls for tc in cr.trajectory_categories)
            _od = Counter(cr.outcome_category for cr in f_cls if cr.outcome_category)
            _ed = Counter(et for c in f_cases for et in c.error_types)
            _bd = {}
            for c in f_cases:
                _bd.setdefault(c.batch, {"p": 0, "f": 0})
                _bd[c.batch]["p" if c.passed else "f"] += 1
            _lines = [
                f"Total: {len(f_cases)} queries, {len(_pd)} passed, {len(_fd)} failed ({len(_pd)/max(len(f_cases),1)*100:.1f}%)",
                f"Golden: {len([c for c in _pd if c.error_count==0 and c.step_count<8])}, Recovery: {len([c for c in _pd if c.error_count>0])}, False claims: {len([c for c in _fd if c.has_success_claim])}",
                "",
            ]
            if _bd:
                _lines.append("Batch breakdown:")
                for bn, bs in sorted(_bd.items(), key=lambda x: x[1]["f"], reverse=True):
                    t = bs["p"] + bs["f"]
                    _lines.append(f"  {bn}: {bs['p']}/{t} ({bs['p']/t*100:.0f}%)")
                _lines.append("")
            if _td:
                _lines.append("Trajectory patterns: " + ", ".join(f"{k}({v})" for k, v in _td.most_common(6)))
            if _od:
                _lines.append("Outcome patterns: " + ", ".join(f"{k}({v})" for k, v in _od.most_common(6)))
            if _ed:
                _lines.append("Error types: " + ", ".join(f"{k}({v})" for k, v in _ed.most_common(6)))
            _tw_prompt = (
                "You are an expert eval analyst. Produce 5-7 KEY TAKEAWAYS as HTML.\n\n"
                + "\n".join(_lines) +
                "\n\nFormat: <div class=\"takeaway\"><strong>Title</strong><br>1-2 sentence explanation.</div>\n"
                "Cover: biggest risk, best area, worst area, most actionable fix, unexpected finding. Be specific with numbers."
            )
            _tw_result, _ = fc._call_llm(_tw_prompt, max_tokens=1000, temp=0.2)
            if _tw_result:
                st.session_state["_llm_takeaways_html"] = _tw_result
                os.makedirs(os.path.dirname(_takeaway_cache_path), exist_ok=True)
                with open(_takeaway_cache_path, "w") as _cf:
                    json.dump({"takeaways": _tw_result}, _cf)


    recovery_cases = sorted([c for c in f_passed if c.error_count > 0], key=lambda c: -c.error_count)
    false_claim_cases = sorted([c for c in f_failed if c.has_success_claim], key=lambda c: -c.step_count)
    golden_cases = [c for c in f_passed if c.error_count == 0 and c.step_count < 8]
    clean_pass = [c for c in f_passed if c.error_count == 0]
    cls_lookup_da = {(cr.batch, cr.query_index): cr for cr in f_cls}

    wasted_steps_total = 0
    wasted_queries = []
    for c in f_failed:
        if c.error_positions:
            first_err = min(c.error_positions)
            after = c.step_count - first_err
            if after > 3:
                wasted_steps_total += after
                wasted_queries.append((c, after, first_err))
    wasted_queries.sort(key=lambda x: -x[1])

    f_recovery_cls_lookup = {}
    if recovery_classifications:
        for rc in recovery_classifications:
            if rc.batch in sel_batches:
                f_recovery_cls_lookup[(rc.batch, rc.query_index)] = rc

    avg_steps_p = sum(c.step_count for c in f_passed) / max(len(f_passed), 1) if f_passed else 0
    avg_steps_f = sum(c.step_count for c in f_failed) / max(len(f_failed), 1) if f_failed else 0
    avg_time_p = sum(c.execution_time_sec for c in f_passed) / max(len(f_passed), 1) if f_passed else 0
    avg_time_f = sum(c.execution_time_sec for c in f_failed) / max(len(f_failed), 1) if f_failed else 0
    avg_scripts_p = sum(c.script_exec_count for c in f_passed) / max(len(f_passed), 1) if f_passed else 0
    avg_scripts_f = sum(c.script_exec_count for c in f_failed) / max(len(f_failed), 1) if f_failed else 0

    # --- KPI strip ---
    st.markdown(f"""
    <div class="stat-card-wrap"><div class="stat-card-hdr">Deep Analysis Overview</div>
    <table class="agg-table"><thead><tr>
        <th>Total</th><th>Passed</th><th>Failed</th><th>Recovery</th><th>Golden</th><th>False Claims</th><th>Wasted Steps</th>
    </tr></thead><tbody><tr>
        <td style="font-weight:700">{total_q}</td>
        <td style="font-weight:700;color:{kpi_good}">{total_p}</td>
        <td style="font-weight:700;color:{kpi_bad}">{total_f}</td>
        <td style="font-weight:700;color:{kpi_warn}">{len(recovery_cases)}</td>
        <td style="font-weight:700;color:{kpi_good}">{len(golden_cases)}</td>
        <td style="font-weight:700;color:{kpi_bad}">{len(false_claim_cases)}</td>
        <td style="font-weight:700;color:{kpi_bad}">{wasted_steps_total}</td>
    </tr></tbody></table></div>
    """, unsafe_allow_html=True)

    retry_loop_cases = [c for c in f_failed if len(c.script_similarity_groups) > 0]

    llm_fail_analysis = None
    llm_recs = None

    # =================================================================
    # Failure Analysis
    # =================================================================
    st.markdown('<div class="section-strip">Failure Analysis</div>', unsafe_allow_html=True)

    failure_insights = []

    # What operation was the agent doing when errors occurred?
    if f_failed:
        failing_ops = Counter()
        silent_fails = 0
        for c in f_failed:
            if not c.error_positions:
                silent_fails += 1
                continue
            for err_pos in c.error_positions:
                script_before = None
                for s in reversed(c.steps[:err_pos + 1]):
                    if s.step_type == "ScriptExecution":
                        script_before = describe_script(s.script_full)
                        break
                if script_before:
                    failing_ops[script_before] += 1

        if failing_ops:
            top_ops = failing_ops.most_common(5)
            ops_str = ", ".join(f"<strong>{op[0]}</strong> ({op[1]} errors)" for op in top_ops)
            silent_note = f" Additionally, <strong>{silent_fails}</strong> queries fail silently — no script errors but the output is wrong." if silent_fails else ""
            failure_insights.append(
                f"<strong>Operations that trigger errors most:</strong> {ops_str}. "
                f"These are the operations the agent struggles with.{silent_note}")
        elif silent_fails:
            failure_insights.append(
                f"<strong>{silent_fails} queries</strong> fail without any script errors — "
                f"the agent executes cleanly but produces wrong output. These are the hardest to debug.")

    # Error types with actual error messages
    if f_failed:
        fail_error_types = Counter(et for c in f_failed for et in c.error_types)
        if fail_error_types:
            top_fail_errs = fail_error_types.most_common(5)
            err_str = ", ".join(f"<strong>{e[0]}</strong> ({e[1]})" for e in top_fail_errs)
            failure_insights.append(
                f"<strong>Most common errors in failures:</strong> {err_str}.")

        # Actual error messages from trajectories
        error_samples: dict[str, list[str]] = {}
        for c in f_failed:
            for s in c.steps:
                if s.step_type == "ScriptResponse" and s.error_type:
                    raw_msg = (s.result or "").strip()
                    console = (s.console or "").strip()
                    # Deduplicate repeated console lines
                    if console:
                        seen_lines = []
                        for ln in console.split("\n"):
                            ln = ln.strip()
                            if ln and ln not in seen_lines:
                                seen_lines.append(ln)
                        console = "\n".join(seen_lines[:5])
                    msg = raw_msg
                    if console:
                        msg = f'{raw_msg}\n[console: {console}]' if raw_msg else console
                    if not msg:
                        continue
                    if len(msg) > 300:
                        msg = msg[:300] + "…"
                    if s.error_type not in error_samples:
                        error_samples[s.error_type] = []
                    if len(error_samples[s.error_type]) < 2:
                        if msg not in error_samples[s.error_type]:
                            error_samples[s.error_type].append(msg)
        if error_samples:
            err_detail_parts = []
            for etype, msgs in list(error_samples.items())[:4]:
                sample_str = "<br>".join(
                    f'<span style="font-size:0.8rem;color:{card_muted};font-family:monospace">{_h(m)}</span>'
                    for m in msgs
                )
                err_detail_parts.append(f"<strong>{_h(etype)}</strong>:<br>{sample_str}")
            failure_insights.append(
                "<strong>Actual error messages:</strong><br>" + "<br>".join(err_detail_parts))

    # Script operations that fail
    if f_failed:
        fail_scripts = Counter(describe_script(s.script_full) for c in f_failed for s in c.steps if s.step_type == "ScriptExecution")
        pass_scripts_set = set(describe_script(s.script_full) for c in f_passed for s in c.steps if s.step_type == "ScriptExecution")
        fail_only_ops = [(op, cnt) for op, cnt in fail_scripts.most_common(5) if cnt >= 2]
        if fail_only_ops:
            ops_str = ", ".join(f"<strong>{op[0]}</strong> ({op[1]})" for op in fail_only_ops)
            failure_insights.append(
                f"<strong>Most attempted operations in failures:</strong> {ops_str}.")

    # Step efficiency gap
    if f_failed and avg_steps_f - avg_steps_p > 2:
        failure_insights.append(
            f"Failed queries use <strong>{avg_steps_f:.0f} steps</strong> on average vs "
            f"<strong>{avg_steps_p:.0f}</strong> for passes — {avg_steps_f - avg_steps_p:.0f} more. "
            f"They also take <strong>{avg_time_f:.0f}s</strong> vs <strong>{avg_time_p:.0f}s</strong> and "
            f"execute <strong>{avg_scripts_f:.1f} scripts</strong> vs <strong>{avg_scripts_p:.1f}</strong>.")

    # Wasted steps
    if wasted_steps_total > 0:
        worst = wasted_queries[0] if wasted_queries else None
        avg_waste = wasted_steps_total / max(len(wasted_queries), 1)
        failure_insights.append(
            f"<strong>{wasted_steps_total} steps wasted</strong> after first error across "
            f"<strong>{len(wasted_queries)} queries</strong> (avg {avg_waste:.0f} wasted per query). "
            + (f"Worst case: <strong>{worst[0].batch} Q{worst[0].query_index}</strong> wasted {worst[1]} steps after error at step {worst[2]}. " if worst else "")
            + "The agent continues executing after errors instead of stopping or changing approach.")

    # Retry loops
    if retry_loop_cases:
        failure_insights.append(
            f"<strong>{len(retry_loop_cases)} failing queries</strong> contain retry loops — "
            f"the agent repeats near-identical scripts without meaningful adaptation. "
            f"These queries average <strong>{sum(c.step_count for c in retry_loop_cases) / len(retry_loop_cases):.0f} steps</strong>.")

    for fi in failure_insights:
        st.markdown(
            f'<div style="background:{card_bg};border:1px solid {card_border};border-left:4px solid {kpi_bad};'
            f'border-radius:10px;padding:0.9rem 1.2rem;margin-bottom:0.5rem;font-size:0.88rem;line-height:1.7;color:{card_text}">'
            f'{fi}</div>', unsafe_allow_html=True)

    # LLM-powered root cause analysis
    if f_failed:
        _deep_analysis_cache_key = f"deep_fail_{hashlib.md5(base_path.encode()).hexdigest()[:10]}"
        _deep_cache_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache", f"{_deep_analysis_cache_key}.json")

        cached_analysis = None
        if os.path.exists(_deep_cache_path):
            try:
                with open(_deep_cache_path) as _cf:
                    cached_analysis = json.load(_cf).get("analysis")
            except Exception:
                pass

        if cached_analysis:
            llm_fail_analysis = cached_analysis
        else:
            # Build data for LLM
            fail_data_lines = [f"=== FAILURE ANALYSIS: {len(f_failed)} failed queries ===", ""]
            fail_data_lines.append(f"Total: {total_f} failed, {total_p} passed out of {total_f + total_p}")
            fail_data_lines.append(f"Avg steps: failed={avg_steps_f:.0f}, passed={avg_steps_p:.0f}")
            fail_data_lines.append(f"Avg time: failed={avg_time_f:.0f}s, passed={avg_time_p:.0f}s")
            fail_data_lines.append("")

            fail_err_types = Counter(et for c in f_failed for et in c.error_types)
            if fail_err_types:
                fail_data_lines.append("Error type distribution:")
                for et, cnt in fail_err_types.most_common(8):
                    fail_data_lines.append(f"  {et}: {cnt}")
                fail_data_lines.append("")

            fail_data_lines.append("Failed query details (sample):")
            for c in f_failed[:10]:
                fail_data_lines.append(f"  [{c.batch}] Q{c.query_index}: {c.query_text[:100]}")
                fail_data_lines.append(f"    Steps: {c.step_count}, Errors: {c.error_count}, Time: {c.execution_time_sec:.0f}s")
                if c.error_types:
                    fail_data_lines.append(f"    Error types: {', '.join(c.error_types[:4])}")
                for s in c.steps:
                    if s.step_type == "ScriptResponse" and s.error_type:
                        msg = (s.console or s.result or "")[:100].strip()
                        if msg:
                            fail_data_lines.append(f"    Error msg: {msg}")
                            break
                ev = extract_grader_evidence(c)
                if ev and ev.assertions and len(ev.assertions) > 0:
                    a = ev.assertions[0]
                    if a.get("expected") or a.get("actual"):
                        fail_data_lines.append(f"    Expected: {str(a.get('expected', ''))[:80]}")
                        fail_data_lines.append(f"    Actual: {str(a.get('actual', ''))[:80]}")
                # Classification
                cls_match = next((cl for cl in classifications if cl.batch == c.batch and cl.query_index == c.query_index), None)
                if cls_match:
                    fail_data_lines.append(f"    Categories: {', '.join(cls_match.trajectory_categories or [])}")
                    if cls_match.why:
                        fail_data_lines.append(f"    Why: {cls_match.why[:120]}")
                fail_data_lines.append("")

            fail_prompt = f"""You are an expert eval analyst. Analyze these failing queries and provide root cause insights.

{chr(10).join(fail_data_lines)}

Write a concise analysis (5-7 bullet points) using HTML (<ul><li>, <strong>). Cover:
1. ROOT CAUSES: What specific patterns cause failures? Group by root cause, not just error type.
2. ERROR PATTERNS: What do the actual error messages reveal about the underlying issue?
3. QUERY PATTERNS: Are certain types of queries (data analysis, formatting, multi-step) more prone to failure?
4. GRADER MISMATCHES: What do expected vs actual values tell us about where the agent goes wrong?
5. ACTIONABLE FIXES: What specific changes would fix the most failures?

RULES:
- Be specific: name actual error messages, batch names, query patterns
- Don't repeat the raw data — interpret it. "3 GenericErrors" is data, "Agent miscalculates aggregations because it doesn't handle null values" is insight
- Keep under 200 words"""

            with st.spinner("Analyzing failure root causes..."):
                llm_fail_analysis, _ = fc._call_llm(fail_prompt, max_tokens=1000, temp=0.2)

            if llm_fail_analysis:
                os.makedirs(os.path.dirname(_deep_cache_path), exist_ok=True)
                with open(_deep_cache_path, "w") as _cf:
                    json.dump({"analysis": llm_fail_analysis}, _cf)

        if llm_fail_analysis:
            st.markdown(
                f'<div style="background:{card_bg};border:1px solid {card_border};border-left:4px solid {accent};'
                f'border-radius:10px;padding:1rem 1.3rem;margin-bottom:0.5rem;font-size:0.85rem;line-height:1.7;color:{card_text}">'
                f'<div style="font-size:0.7rem;font-weight:700;text-transform:uppercase;letter-spacing:0.06em;'
                f'color:{card_muted};margin-bottom:0.4rem">Root Cause Analysis</div>'
                f'{llm_fail_analysis}</div>', unsafe_allow_html=True)

    if not failure_insights:
        st.info("No failing queries to analyze.")

    # =================================================================
    # Success Analysis
    # =================================================================
    st.markdown('<div class="section-strip">Success Analysis</div>', unsafe_allow_html=True)

    success_insights = []

    if golden_cases:
        g_pct = len(golden_cases) / max(total_p, 1) * 100
        g_avg_steps = sum(c.step_count for c in golden_cases) / len(golden_cases)
        g_avg_time = sum(c.execution_time_sec for c in golden_cases) / len(golden_cases)
        g_batches = Counter(c.batch for c in golden_cases)
        top_g_batch = g_batches.most_common(1)[0] if g_batches else ("", 0)
        success_insights.append(
            f"<strong>{len(golden_cases)} golden trajectories</strong> ({g_pct:.0f}% of passes) — "
            f"solved with avg {g_avg_steps:.0f} steps and {g_avg_time:.0f}s. "
            f"Most golden runs in <strong>{_h(top_g_batch[0])}</strong> ({top_g_batch[1]}). "
            f"These represent the agent at its best — use as optimization benchmarks.")

    if clean_pass:
        cp_pct = len(clean_pass) / max(total_p, 1) * 100
        success_insights.append(
            f"<strong>{len(clean_pass)} queries ({cp_pct:.0f}% of passes)</strong> passed without any errors. "
            f"The remaining {len(recovery_cases)} passes encountered errors but recovered.")

    if recovery_cases:
        rec_pct = len(recovery_cases) / max(total_p, 1) * 100
        rec_error_types = Counter(et for c in recovery_cases for et in c.error_types)
        top_rec_errs = rec_error_types.most_common(3)
        avg_rec_steps = sum(c.step_count for c in recovery_cases) / len(recovery_cases)
        avg_rec_errors = sum(c.error_count for c in recovery_cases) / len(recovery_cases)
        err_str = ", ".join(f"{e[0]} ({e[1]})" for e in top_rec_errs) if top_rec_errs else "various"
        success_insights.append(
            f"<strong>{len(recovery_cases)} queries ({rec_pct:.0f}% of passes) recovered from errors</strong> — "
            f"avg {avg_rec_steps:.0f} steps with {avg_rec_errors:.1f} errors per query. "
            f"Most recovered from: {err_str}. "
            f"The agent shows resilience — study these patterns to fix currently failing queries.")

    if f_passed:
        pass_scripts = Counter(describe_script(s.script_full) for c in f_passed for s in c.steps if s.step_type == "ScriptExecution")
        top_pass_ops = pass_scripts.most_common(3)
        if top_pass_ops:
            ops_str = ", ".join(f"{op[0]} ({op[1]})" for op in top_pass_ops)
            success_insights.append(
                f"Most common operations in passing queries: <strong>{ops_str}</strong>. "
                f"The agent handles these script patterns well.")

    for si in success_insights:
        st.markdown(
            f'<div style="background:{card_bg};border:1px solid {card_border};border-left:4px solid {kpi_good};'
            f'border-radius:10px;padding:0.9rem 1.2rem;margin-bottom:0.5rem;font-size:0.88rem;line-height:1.7;color:{card_text}">'
            f'{si}</div>', unsafe_allow_html=True)

    if not success_insights:
        st.info("No passing queries to analyze.")

    # =================================================================
    # False Success Claims (insight, not table)
    # =================================================================
    _report_fc_html = ""
    if false_claim_cases:
        st.markdown('<div class="section-strip">False Success Claims</div>', unsafe_allow_html=True)

        fc_pct = len(false_claim_cases) / max(total_f, 1) * 100
        fc_cats = Counter()
        for c in false_claim_cases:
            cr = cls_lookup_da.get((c.batch, c.query_index))
            if cr:
                for tc in (cr.trajectory_categories or [cr.primary_category]):
                    fc_cats[tc] += 1
        fc_cat_str = ", ".join(f"<strong>{c[0]}</strong> ({c[1]})" for c in fc_cats.most_common(3)) if fc_cats else "various"
        fc_batches = Counter(c.batch for c in false_claim_cases)
        fc_batch_str = ", ".join(f"{b[0]} ({b[1]})" for b in fc_batches.most_common(3))
        avg_fc_steps = sum(c.step_count for c in false_claim_cases) / len(false_claim_cases)

        _report_fc_html = (
            f'<strong>{len(false_claim_cases)}/{total_f} failures ({fc_pct:.0f}%)</strong> have the agent claiming success when it actually failed. '
            f'These queries average <strong>{avg_fc_steps:.0f} steps</strong> and span batches: {_h(fc_batch_str)}.<br>'
            f'Most common trajectory patterns: {fc_cat_str}.<br>'
            f'The agent lacks self-evaluation — it completes execution and reports success without verifying the output matches expectations.')

        st.markdown(
            f'<div style="background:{card_bg};border:1px solid {card_border};border-left:4px solid {kpi_bad};'
            f'border-radius:10px;padding:0.9rem 1.2rem;margin-bottom:0.5rem;font-size:0.88rem;line-height:1.7;color:{card_text}">'
            f'{_report_fc_html}</div>',
            unsafe_allow_html=True)

    # =================================================================
    # Recovery Analysis (insight, not table)
    # =================================================================
    _report_recovery_html = ""
    failed_same_errors = []
    if recovery_cases:
        st.markdown('<div class="section-strip">Recovery Analysis</div>', unsafe_allow_html=True)

        rec_error_types = Counter(et for c in recovery_cases for et in c.error_types)
        top_rec = rec_error_types.most_common(3)
        avg_rec_steps = sum(c.step_count for c in recovery_cases) / len(recovery_cases)
        avg_rec_time = sum(c.execution_time_sec for c in recovery_cases) / len(recovery_cases)
        avg_rec_errors = sum(c.error_count for c in recovery_cases) / len(recovery_cases)
        rec_batches = Counter(c.batch for c in recovery_cases)
        rec_batch_str = ", ".join(f"{b[0]} ({b[1]})" for b in rec_batches.most_common(3))

        failed_same_errors = []
        for et, _ in top_rec:
            failed_with_same = [c for c in f_failed if et in c.error_types]
            if failed_with_same:
                failed_same_errors.append((et, len(failed_with_same)))

        _rec_main = (
            f'<strong>{len(recovery_cases)} queries</strong> hit errors but still passed — '
            f'avg <strong>{avg_rec_steps:.0f} steps</strong>, <strong>{avg_rec_errors:.1f} errors</strong>, '
            f'<strong>{avg_rec_time:.0f}s</strong>. Batches: {_h(rec_batch_str)}.<br>'
            f'Most recovered-from errors: {", ".join(f"<strong>{e[0]}</strong> ({e[1]})" for e in top_rec) if top_rec else "various"}.')

        st.markdown(
            f'<div style="background:{card_bg};border:1px solid {card_border};border-left:4px solid {kpi_warn};'
            f'border-radius:10px;padding:0.9rem 1.2rem;margin-bottom:0.5rem;font-size:0.88rem;line-height:1.7;color:{card_text}">'
            f'{_rec_main}</div>', unsafe_allow_html=True)

        _report_recovery_html = f'<div class="card" style="border-left:4px solid #d97706">{_rec_main}</div>'

        if failed_same_errors:
            cross_str = ", ".join(f"<strong>{e[0]}</strong> — recovered {rec_error_types[e[0]]}x but still fails {e[1]}x" for e in failed_same_errors[:3])
            _cross_html = (
                f'<strong>Cross-reference with failures:</strong> {cross_str}. '
                f'The agent can recover from these errors sometimes — investigate what differs in the failing cases.')
            st.markdown(
                f'<div style="background:{card_bg};border:1px solid {card_border};border-left:4px solid {accent};'
                f'border-radius:10px;padding:0.9rem 1.2rem;margin-bottom:0.5rem;font-size:0.88rem;line-height:1.7;color:{card_text}">'
                f'{_cross_html}</div>',
                unsafe_allow_html=True)
            _report_recovery_html += f'\n<div class="card" style="border-left:4px solid #1f4e79">{_cross_html}</div>'

    # =================================================================
    # Recommendations (LLM-generated)
    # =================================================================
    if f_failed or recovery_cases or false_claim_cases:
        st.markdown('<div class="section-strip">Recommendations</div>', unsafe_allow_html=True)

        _rec_cache_key = f"deep_rec_{hashlib.md5(base_path.encode()).hexdigest()[:10]}"
        _rec_cache_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache", f"{_rec_cache_key}.json")

        cached_recs = None
        if os.path.exists(_rec_cache_path):
            try:
                with open(_rec_cache_path) as _cf:
                    cached_recs = json.load(_cf).get("recs")
            except Exception:
                pass

        if cached_recs:
            llm_recs = cached_recs
        else:
            rec_data = [f"=== EVAL SUMMARY ==="]
            rec_data.append(f"Total: {total_f} failed, {total_p} passed")
            rec_data.append(f"Golden trajectories (no errors, <8 steps): {len(golden_cases)}")
            rec_data.append(f"Recovery cases (passed despite errors): {len(recovery_cases)}")
            rec_data.append(f"False success claims: {len(false_claim_cases)}")
            rec_data.append(f"Retry loop failures: {len(retry_loop_cases)}")
            rec_data.append(f"Wasted steps total: {wasted_steps_total} across {len(wasted_queries)} queries")
            rec_data.append(f"Avg steps: pass={avg_steps_p:.0f}, fail={avg_steps_f:.0f}")
            rec_data.append(f"Avg time: pass={avg_time_p:.0f}s, fail={avg_time_f:.0f}s")
            rec_data.append("")

            fail_err = Counter(et for c in f_failed for et in c.error_types)
            if fail_err:
                rec_data.append("Error types: " + ", ".join(f"{k}({v})" for k, v in fail_err.most_common(5)))

            fail_ops = Counter()
            for c in f_failed:
                for s in c.steps:
                    if s.step_type == "ScriptExecution":
                        fail_ops[describe_script(s.script_full)] += 1
            if fail_ops:
                rec_data.append("Failing operations: " + ", ".join(f"{k}({v})" for k, v in fail_ops.most_common(5)))

            # Trajectory/outcome categories
            tc_counts = Counter()
            oc_counts = Counter()
            for cl in classifications:
                if not any(c.batch == cl.batch and c.query_index == cl.query_index and c.passed for c in f_cases):
                    for tc in (cl.trajectory_categories or []):
                        tc_counts[tc] += 1
                    if cl.outcome_category:
                        oc_counts[cl.outcome_category] += 1
            if tc_counts:
                rec_data.append("Trajectory patterns: " + ", ".join(f"{k}({v})" for k, v in tc_counts.most_common(5)))
            if oc_counts:
                rec_data.append("Outcome patterns: " + ", ".join(f"{k}({v})" for k, v in oc_counts.most_common(5)))

            rec_prompt = f"""You are an expert eval analyst. Based on these results, give 4-6 specific, actionable recommendations.

{chr(10).join(rec_data)}

Write as numbered HTML items. Each recommendation should have:
- A <strong>specific finding</strong> (what the data shows)
- A concrete <strong>action</strong> (what to do about it)

RULES:
- Each recommendation must be DIFFERENT — don't repeat the same advice in different words
- Be specific: "Add null-check before aggregation in sheet operations" not "improve error handling"
- Prioritize by impact: highest-ROI fixes first
- Reference actual error types, operations, and patterns from the data
- If recovery cases exist, suggest extracting the recovery strategy
- Keep each recommendation to 2 sentences max
- Use HTML: <strong> for key terms, numbered list with line breaks"""

            with st.spinner("Generating recommendations..."):
                llm_recs, _ = fc._call_llm(rec_prompt, max_tokens=800, temp=0.2)

            if llm_recs:
                os.makedirs(os.path.dirname(_rec_cache_path), exist_ok=True)
                with open(_rec_cache_path, "w") as _cf:
                    json.dump({"recs": llm_recs}, _cf)

        if llm_recs:
            st.markdown(
                f'<div style="background:{card_bg};border:1px solid {card_border};border-left:4px solid {accent};'
                f'border-radius:10px;padding:1rem 1.3rem;margin-bottom:0.6rem;font-size:0.85rem;line-height:1.8;color:{card_text}">'
                f'{llm_recs}</div>', unsafe_allow_html=True)
        else:
            st.info("Set GEMINI_API_KEY or GROQ_API_KEY to enable LLM-powered recommendations.")

    # Build full report and store in session state
    st.session_state["_full_report_html"] = _build_full_report(
        f_cases=f_cases, f_passed=f_passed, f_failed=f_failed, f_cls=f_cls,
        total_q=total_q, total_p=total_p, total_f=total_f, pass_rate=pass_rate,
        sel_batches=sel_batches, classifications=classifications,
        recovery_classifications=recovery_classifications,
        exec_summary_html=st.session_state.get("_exec_summary_html", ""),
        llm_takeaways_html=st.session_state.get("_llm_takeaways_html", ""),
        success_insights=success_insights, failure_insights=failure_insights,
        llm_fail_analysis=llm_fail_analysis, llm_recs=llm_recs,
        fc_section_html=_report_fc_html, recovery_section_html=_report_recovery_html,
        golden_count=len(golden_cases), recovery_count=len(recovery_cases),
        false_claim_count=len(false_claim_cases),
        wasted_steps_total=wasted_steps_total, wasted_query_count=len(wasted_queries),
        retry_loop_count=len(retry_loop_cases),
        avg_steps_p=avg_steps_p, avg_steps_f=avg_steps_f,
        avg_time_p=avg_time_p, avg_time_f=avg_time_f,
    )


# ===================================================================
# PAGE: Query Explorer (filtered individual query detail)
# ===================================================================

elif page == "Query Explorer":

    recovery_cls_lookup: dict[tuple, object] = {}
    if recovery_classifications:
        for rc in recovery_classifications:
            if rc.batch in sel_batches:
                recovery_cls_lookup[(rc.batch, rc.query_index)] = rc
    cls_lookup = {(cr.batch, cr.query_index): cr for cr in f_cls}

    # --- Quick filter presets ---
    preset_cols = st.columns(6)
    presets = [
        ("All", "all"), ("Failed", "failed"), ("Passed", "passed"),
        ("Golden", "golden"), ("Recovery", "recovery"), ("False Claims", "false_claims"),
    ]
    if "qe_preset" not in st.session_state:
        st.session_state["qe_preset"] = "all"
    for idx, (label, key) in enumerate(presets):
        with preset_cols[idx]:
            if st.button(label, key=f"preset_{key}", use_container_width=True,
                         type="primary" if st.session_state["qe_preset"] == key else "secondary"):
                st.session_state["qe_preset"] = key
                st.rerun()

    active_preset = st.session_state["qe_preset"]

    # Set default filter values based on preset
    default_status = "All"
    default_sort = "Default"
    if active_preset == "failed":
        default_status = "Failed"
    elif active_preset == "passed":
        default_status = "Passed"
    elif active_preset == "golden":
        default_status = "Passed"
        default_sort = "Steps (high)"
    elif active_preset == "recovery":
        default_status = "Passed"
        default_sort = "Errors (high)"
    elif active_preset == "false_claims":
        default_status = "Failed"

    # --- Filter bar ---
    fc1, fc2, fc3, fc4 = st.columns([3, 1, 1, 1])
    with fc1:
        search = st.text_input("Search", "", placeholder="Filter by query text, batch, or Q#...", label_visibility="collapsed")
    with fc2:
        status_opts = ["All", "Passed", "Failed"]
        status_filter = st.selectbox("Status", status_opts, index=status_opts.index(default_status), label_visibility="collapsed")
    with fc3:
        available_cats = sorted(set(cr.primary_category for cr in f_cls)) if f_cls else []
        cat_filter = st.selectbox("Category", ["All Categories"] + available_cats, label_visibility="collapsed")
    with fc4:
        sort_opts = ["Default", "Steps (high)", "Errors (high)", "Time (high)"]
        sort_by = st.selectbox("Sort", sort_opts, index=sort_opts.index(default_sort), label_visibility="collapsed")

    # --- Build filtered list ---
    display_items = []
    for c in f_cases:
        cr = cls_lookup.get((c.batch, c.query_index))
        if status_filter == "Passed" and not c.passed:
            continue
        if status_filter == "Failed" and c.passed:
            continue
        if cat_filter != "All Categories" and not c.passed:
            if cr and cr.primary_category != cat_filter:
                continue
            if not cr:
                continue
        if active_preset == "golden" and not (c.passed and c.error_count == 0 and c.step_count < 8):
            continue
        if active_preset == "recovery" and not (c.passed and c.error_count > 0):
            continue
        if active_preset == "false_claims" and not (not c.passed and c.has_success_claim):
            continue
        if search:
            sl = search.lower()
            if not (sl in c.query_text.lower() or sl in c.batch.lower()
                    or search in str(c.query_index)
                    or (cr and sl in cr.primary_category.lower())):
                continue
        display_items.append((c, cr))

    if sort_by == "Steps (high)":
        display_items.sort(key=lambda x: -x[0].step_count)
    elif sort_by == "Errors (high)":
        display_items.sort(key=lambda x: -x[0].error_count)
    elif sort_by == "Time (high)":
        display_items.sort(key=lambda x: -x[0].execution_time_sec)
    else:
        display_items.sort(key=lambda x: (x[0].batch, int(x[0].query_index)))

    # --- Results strip ---
    n_shown = len(display_items)
    n_pass = sum(1 for c, _ in display_items if c.passed)
    n_fail = n_shown - n_pass
    dark = st.session_state.get("dark_mode", False)
    strip_color = "#8b949e" if dark else "#64748b"
    pass_c = "#3fb950" if dark else "#059669"
    fail_c = "#f85149" if dark else "#dc2626"
    st.markdown(
        f'<div style="font-size:0.82rem;color:{strip_color};margin-bottom:0.6rem">'
        f'Showing <strong>{n_shown}</strong> of {total_q} queries '
        f'-- <span style="color:{pass_c};font-weight:600">{n_pass} passed</span> '
        f'| <span style="color:{fail_c};font-weight:600">{n_fail} failed</span></div>',
        unsafe_allow_html=True)

    # --- Query list (rich inline cards with insights) ---
    card_bg = "#161b22" if dark else "#ffffff"
    card_border_default = "#30363d" if dark else "#e2e8f0"
    muted_c = "#8b949e" if dark else "#94a3b8"
    text_c = "#e6edf3" if dark else "#1e293b"
    subtext_c = "#8b949e" if dark else "#64748b"
    insight_bg_fail = "#1a0b0b" if dark else "#fef2f2"
    insight_bg_pass = "#0b1a0e" if dark else "#f0fdf4"
    insight_bg_recovery = "#1a160b" if dark else "#fffbeb"
    insight_border_fail = "#f85149" if dark else "#fca5a5"
    insight_border_pass = "#3fb950" if dark else "#86efac"
    insight_border_recovery = "#d29922" if dark else "#fcd34d"

    for c, cr in display_items:
        rc = recovery_cls_lookup.get((c.batch, c.query_index))
        is_recovery = c.passed and c.error_count > 0
        is_golden = c.passed and c.error_count == 0 and c.step_count < 8
        is_false_claim = not c.passed and c.has_success_claim

        if not c.passed:
            border_left = "#f85149" if dark else "#dc2626"
            status_bg = "#f8514920" if dark else "#dc262610"
            status_text = "#f85149" if dark else "#dc2626"
            status_label = "FAIL"
        elif is_recovery:
            border_left = "#d29922" if dark else "#d97706"
            status_bg = "#d2992220" if dark else "#d9770610"
            status_text = "#d29922" if dark else "#d97706"
            status_label = "RECOVERED"
        elif is_golden:
            border_left = "#3fb950" if dark else "#059669"
            status_bg = "#3fb95020" if dark else "#05966910"
            status_text = "#3fb950" if dark else "#059669"
            status_label = "GOLDEN"
        else:
            border_left = "#3fb950" if dark else "#059669"
            status_bg = "#3fb95020" if dark else "#05966910"
            status_text = "#3fb950" if dark else "#059669"
            status_label = "PASS"

        # Build inline insight text
        insight_html = ""
        if not c.passed and cr and cr.why:
            fix_part = ""
            if cr.suggested_fix:
                fix_part = (f'<span style="color:{("#3fb950" if dark else "#059669")};font-weight:600">'
                            f'Fix:</span> {_h(cr.suggested_fix)}')
            insight_html = (
                f'<div style="background:{insight_bg_fail};border-left:3px solid {insight_border_fail};'
                f'padding:0.5rem 0.7rem;border-radius:0 6px 6px 0;margin-top:0.4rem;font-size:0.8rem">'
                f'<span style="color:{("#f85149" if dark else "#b91c1c")};font-weight:600">Root cause:</span> '
                f'<span style="color:{subtext_c}">{_h(cr.why)}</span>'
                + (f'<br>{fix_part}' if fix_part else '') +
                '</div>')
        elif is_recovery and rc and rc.why:
            insight_html = (
                f'<div style="background:{insight_bg_recovery};border-left:3px solid {insight_border_recovery};'
                f'padding:0.5rem 0.7rem;border-radius:0 6px 6px 0;margin-top:0.4rem;font-size:0.8rem">'
                f'<span style="color:{("#d29922" if dark else "#92400e")};font-weight:600">Recovery:</span> '
                f'<span style="color:{subtext_c}">{_h(rc.why)}</span></div>')
        elif is_recovery:
            err_types = ", ".join(c.error_types) if c.error_types else "unknown"
            insight_html = (
                f'<div style="background:{insight_bg_recovery};border-left:3px solid {insight_border_recovery};'
                f'padding:0.5rem 0.7rem;border-radius:0 6px 6px 0;margin-top:0.4rem;font-size:0.8rem">'
                f'<span style="color:{("#d29922" if dark else "#92400e")};font-weight:600">Recovery:</span> '
                f'<span style="color:{subtext_c}">Hit {c.error_count} error(s) ({_h(err_types)}) '
                f'but still produced correct answer in {c.step_count} steps</span></div>')
        elif is_golden:
            insight_html = (
                f'<div style="background:{insight_bg_pass};border-left:3px solid {insight_border_pass};'
                f'padding:0.5rem 0.7rem;border-radius:0 6px 6px 0;margin-top:0.4rem;font-size:0.8rem">'
                f'<span style="color:{("#3fb950" if dark else "#059669")};font-weight:600">Golden path:</span> '
                f'<span style="color:{subtext_c}">Solved cleanly in {c.step_count} steps, '
                f'{c.script_exec_count} scripts, no errors</span></div>')
        elif is_false_claim and cr and cr.why:
            insight_html = (
                f'<div style="background:{insight_bg_fail};border-left:3px solid {insight_border_fail};'
                f'padding:0.5rem 0.7rem;border-radius:0 6px 6px 0;margin-top:0.4rem;font-size:0.8rem">'
                f'<span style="color:{("#f85149" if dark else "#b91c1c")};font-weight:600">False claim:</span> '
                f'<span style="color:{subtext_c}">Agent claimed success but failed. {_h(cr.why)}</span></div>')

        # Build per-step evidence annotations for inline display in trajectory
        evidence_annotations: dict[int, str] = {}
        if not c.passed and cr:
            cats = cr.trajectory_categories or [cr.primary_category]
            for tc in cats[:2]:
                tc_lower = tc.lower() if tc else ""
                if "retry" in tc_lower and c.script_similarity_groups:
                    for grp in c.script_similarity_groups:
                        if len(grp) >= 2:
                            for si in grp:
                                evidence_annotations[si] = f'{_h(tc)}: repeated similar script without meaningful adaptation'
                elif "tool misuse" in tc_lower and c.error_positions:
                    for epos in c.error_positions[:3]:
                        evidence_annotations[epos] = f'{_h(tc)}: wrong API method or incorrect parameters'
                elif "cascading" in tc_lower and len(c.error_positions) >= 2:
                    first_err = min(c.error_positions)
                    evidence_annotations[first_err] = f'{_h(tc)}: initial error — root cause of cascade'
                    for epos in c.error_positions:
                        if epos > first_err and epos not in evidence_annotations:
                            evidence_annotations[epos] = f'{_h(tc)}: downstream failure from Step {first_err}'
                elif "context loss" in tc_lower:
                    if c.error_positions:
                        for epos in c.error_positions[:2]:
                            evidence_annotations[epos] = f'{_h(tc)}: lost track of earlier context or results'
                elif "goal drift" in tc_lower:
                    if c.error_positions:
                        pivot = min(c.error_positions)
                        evidence_annotations[pivot] = f'{_h(tc)}: agent diverged from original intent here'
                elif "silent" in tc_lower:
                    last_step = c.steps[-1] if c.steps else None
                    if last_step:
                        evidence_annotations[last_step.step_index] = f'{_h(tc)}: no errors but output is incorrect'

        # Wasted steps insight for failures
        wasted_html = ""
        if not c.passed and c.step_count > 6:
            error_positions = c.error_positions if c.error_positions else []
            if error_positions:
                first_err = min(error_positions)
                steps_after = c.step_count - first_err
                if steps_after > 3:
                    wasted_html = (
                        f'<span style="background:{("#58a6ff20" if dark else "#dbeafe")};color:{("#58a6ff" if dark else "#1e40af")};'
                        f'font-size:0.72rem;padding:2px 8px;border-radius:10px;margin-left:6px">'
                        f'{steps_after} steps after first error — could be saved</span>')

        # Category badges
        cat_badges = ""
        if not c.passed and cr:
            cats = cr.trajectory_categories or [cr.primary_category]
            for tc in cats[:2]:
                cat_badges += (f'<span style="background:{("#7c3aed30" if dark else "#f3e8ff")};'
                               f'color:{("#a78bfa" if dark else "#7c3aed")};font-size:0.72rem;'
                               f'font-weight:600;padding:2px 8px;border-radius:10px;margin-right:4px">'
                               f'{_h(tc)}</span>')
            if cr.outcome_category:
                cat_badges += (f'<span style="background:{("#d2992220" if dark else "#fef3c7")};'
                               f'color:{("#d29922" if dark else "#92400e")};font-size:0.72rem;'
                               f'font-weight:600;padding:2px 8px;border-radius:10px;margin-right:4px">'
                               f'{_h(cr.outcome_category)}</span>')
        elif is_recovery and rc:
            for tc in (rc.trajectory_categories or [rc.trajectory_category or ""])[:2]:
                if tc:
                    cat_badges += (f'<span style="background:{("#d2992220" if dark else "#fef3c7")};'
                                   f'color:{("#d29922" if dark else "#92400e")};font-size:0.72rem;'
                                   f'font-weight:600;padding:2px 8px;border-radius:10px;margin-right:4px">'
                                   f'{_h(tc)}</span>')

        # Stats pills
        stat_pills = (
            f'<span style="font-size:0.75rem;color:{muted_c}">'
            f'{c.step_count} steps</span>'
            f'<span style="color:{("#30363d" if dark else "#cbd5e1")};margin:0 4px">|</span>'
            f'<span style="font-size:0.75rem;color:{("#f85149" if dark else "#dc2626") if c.error_count > 0 else muted_c}">'
            f'{c.error_count} errors</span>'
            f'<span style="color:{("#30363d" if dark else "#cbd5e1")};margin:0 4px">|</span>'
            f'<span style="font-size:0.75rem;color:{muted_c}">'
            f'{c.execution_time_sec:.0f}s</span>'
            + wasted_html
        )

        # Build trajectory timeline HTML (inside card)
        traj_bar_bg = "#0d1117" if dark else "#f8f9fa"
        traj_bar_border = "#30363d" if dark else "#e2e8f0"
        traj_num_c = "#e6edf3" if dark else "#1e293b"
        traj_label_c = "#8b949e" if dark else "#64748b"
        detail_accent = "#7c3aed" if dark else "#7c3aed"

        traj_stats_bar = (
            f'<div style="display:flex;gap:1.5rem;padding:0.5rem 0.8rem;margin-top:0.5rem;'
            f'background:{traj_bar_bg};border:1px solid {traj_bar_border};border-radius:6px;font-size:0.82rem">'
            f'<span><span style="font-weight:700;color:{traj_num_c}">{c.step_count}</span>'
            f' <span style="color:{traj_label_c}">steps</span></span>'
            f'<span><span style="font-weight:700;color:{("#f85149" if dark else "#dc2626") if c.error_count > 0 else traj_num_c}">{c.error_count}</span>'
            f' <span style="color:{traj_label_c}">errors</span></span>'
            f'<span><span style="font-weight:700;color:{traj_num_c}">{c.script_exec_count}</span>'
            f' <span style="color:{traj_label_c}">script executions</span></span>'
            f'<span><span style="font-weight:700;color:{traj_num_c}">{c.execution_time_sec:.1f}s</span>'
            f' <span style="color:{traj_label_c}">exec time</span></span>'
            f'</div>')

        # Build collapsible timeline
        ev_badge_bg = "#7c3aed20" if dark else "#f5f3ff"
        ev_badge_border = "#7c3aed50" if dark else "#ddd6fe"
        ev_badge_color = "#a78bfa" if dark else "#7c3aed"
        timeline_html = ""
        for step in c.steps:
            ann = evidence_annotations.get(step.step_index, "")
            ev_tag = ""
            if ann:
                ev_tag = (f'<div style="background:{ev_badge_bg};border:1px solid {ev_badge_border};'
                          f'border-left:3px solid {ev_badge_color};padding:3px 8px;margin:1px 0 3px 12px;'
                          f'border-radius:0 4px 4px 0;font-size:0.72rem;color:{ev_badge_color};font-weight:600">'
                          f'EVIDENCE: {ann}</div>')
            if step.step_type == "UserQuery":
                timeline_html += (f'<div style="border-left:3px solid #2563eb;padding:4px 10px;margin:3px 0;'
                                  f'font-size:0.78rem;color:{traj_label_c}"><strong>Step {step.step_index} — Query:</strong> '
                                  f'"{_h(step.text)}"</div>')
            elif step.step_type == "ScriptExecution":
                intent_desc = describe_script(step.script_full)
                border_color = ev_badge_color if ann else "#6c757d"
                timeline_html += (f'<div style="border-left:3px solid {border_color};padding:4px 10px;margin:3px 0;'
                                  f'font-size:0.78rem;color:{traj_label_c}"><strong>Step {step.step_index} — Script:</strong> '
                                  f'{_h(intent_desc)} ({step.script_len} chars)</div>')
            elif step.step_type == "ScriptResponse":
                if step.error_type:
                    border_color = ev_badge_color if ann else "#dc3545"
                    timeline_html += (f'<div style="border-left:3px solid {border_color};padding:4px 10px;margin:3px 0;'
                                      f'font-size:0.78rem;color:{("#f85149" if dark else "#dc2626")}"><strong>Step {step.step_index} — ERROR:</strong> '
                                      f'{_h(step.error_type)}'
                                      + (f' — {_h(step.console)}' if step.console else '') + '</div>')
                else:
                    timeline_html += (f'<div style="border-left:3px solid #28a745;padding:4px 10px;margin:3px 0;'
                                      f'font-size:0.78rem;color:{traj_label_c}"><strong>Step {step.step_index} — OK:</strong> '
                                      f'{_h(step.result or "success")}</div>')
            elif step.step_type == "Assistant":
                continue
            if ev_tag:
                timeline_html += ev_tag

        traj_details = (
            f'<details style="margin-top:0.3rem">'
            f'<summary style="cursor:pointer;font-size:0.82rem;font-weight:600;color:{detail_accent};padding:4px 0">'
            f'Trajectory Steps ({c.step_count} steps)</summary>'
            f'<div style="margin-top:4px;max-height:400px;overflow-y:auto">{timeline_html}</div>'
            f'</details>')

        # Build full card HTML
        card_html = (
            f'<div style="border:1px solid {card_border_default};border-left:4px solid {border_left};'
            f'border-radius:8px;padding:0.7rem 1rem;margin-bottom:0.5rem;background:{card_bg}">'
            # Row 1: Status badge + batch/query ID + stats
            f'<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">'
            f'<span style="background:{status_bg};color:{status_text};font-weight:700;'
            f'font-size:0.72rem;padding:2px 10px;border-radius:12px;letter-spacing:0.03em">{status_label}</span>'
            f'<span style="font-size:0.78rem;color:{muted_c}">{_h(c.batch)}</span>'
            f'<span style="font-size:0.78rem;color:{muted_c};opacity:0.5">Q{c.query_index}</span>'
            f'<span style="flex:1"></span>'
            f'{stat_pills}'
            f'</div>'
            # Row 2: Query text (prominent)
            f'<div style="font-size:0.9rem;color:{text_c};font-weight:500;margin:0.35rem 0 0.2rem;'
            f'line-height:1.35">{_h(c.query_text)}'
            +
            f'</div>'
            # Row 3: Category badges
            + (f'<div style="margin-top:0.25rem">{cat_badges}</div>' if cat_badges else '') +
            # Row 4: Inline insight
            insight_html +
            # Row 5: Trajectory stats bar
            traj_stats_bar +
            # Row 6: Collapsible trajectory timeline
            traj_details +
            f'</div>'
        )
        st.markdown(card_html, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------
st.markdown("---")
st.caption("Eval Insights Platform -- evidence-based trajectory analysis")
