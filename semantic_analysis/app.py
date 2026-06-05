"""Semantic Drift Detection — Streamlit UI for offline trajectory analysis."""
import html
import os
import sys

import streamlit as st

_ROOT = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_ROOT)
for p in (_ROOT, _PARENT, os.path.join(_PARENT, "eval_insights")):
    if p not in sys.path:
        sys.path.insert(0, p)

from trajectory_parser import auto_discover_batches
from drift_analyzer import (
    analyze_batch, analyze_case, BUCKET_OWNERS,
    DRIFT_THRESHOLD, CRITICAL_THRESHOLD,
    _extract_keywords, _get_category, _relevance_score, NORMAL_TRANSITIONS,
)

st.set_page_config(page_title="Semantic Drift Detection", layout="wide")

def _h(t):
    return html.escape(str(t))

# ---------------------------------------------------------------------------
# Dark theme CSS
# ---------------------------------------------------------------------------
st.markdown("""
<style>
header[data-testid="stHeader"] { display: none !important; }
#MainMenu, footer, .stDeployButton { display: none !important; }
section[data-testid="stSidebar"] { background: #0d1117 !important; }

.drift-hero {
    background: linear-gradient(135deg, #0d1117 0%, #161b22 50%, #1a1a2e 100%);
    color: #e6edf3; padding: 2rem 2.5rem; border-radius: 14px;
    margin-bottom: 1.5rem; border: 1px solid #30363d;
}
.drift-hero h1 { font-size: 1.6rem; font-weight: 800; margin: 0; color: #e6edf3; }
.drift-hero p { font-size: 0.88rem; color: #8b949e; margin: 0.3rem 0 0 0; }

.kpi-strip { display: grid; grid-template-columns: repeat(5, 1fr); gap: 0.8rem; margin-bottom: 1.2rem; }
.kpi-card {
    background: #161b22; border: 1px solid #30363d; border-radius: 10px;
    padding: 0.9rem 1.1rem;
}
.kpi-card .label { font-size: 0.7rem; font-weight: 600; color: #8b949e; text-transform: uppercase; letter-spacing: 0.06em; }
.kpi-card .value { font-size: 1.7rem; font-weight: 700; margin-top: 0.2rem; }
.kpi-card .value.red { color: #f85149; }
.kpi-card .value.green { color: #3fb950; }
.kpi-card .value.blue { color: #58a6ff; }
.kpi-card .value.amber { color: #d29922; }
.kpi-card .value.white { color: #e6edf3; }

.section-strip {
    background: linear-gradient(90deg, #161b22 0%, #21262d 100%);
    color: #e6edf3; padding: 0.7rem 1.1rem; border-radius: 8px;
    font-weight: 600; font-size: 0.92rem; margin: 1.2rem 0 1rem 0;
    border: 1px solid #30363d;
}

.badge {
    display: inline-block; font-size: 0.72rem; font-weight: 600;
    padding: 0.2rem 0.6rem; border-radius: 12px;
}
.badge-cat { background: #21262d; color: #58a6ff; border: 1px solid #30363d; }
.badge-bucket { background: #2d1215; color: #ffa198; border: 1px solid #da3633; }
.badge-owner { background: #0d2818; color: #7ee787; border: 1px solid #238636; }
.badge-pass { background: #0d2818; color: #3fb950; border: 1px solid #238636; }
.badge-fail { background: #2d1215; color: #f85149; border: 1px solid #da3633; }

.wf-card {
    background: #161b22; border: 1px solid #30363d; border-radius: 12px;
    padding: 1.4rem 1.6rem; margin-bottom: 1rem;
}
.wf-query {
    background: #0d1117; border: 1px solid #58a6ff; border-left: 4px solid #58a6ff;
    border-radius: 8px; padding: 1rem 1.2rem; margin-bottom: 1.2rem;
    font-size: 0.92rem; color: #e6edf3; line-height: 1.6;
}
.wf-query .qlabel { font-size: 0.7rem; color: #58a6ff; font-weight: 700;
    text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 0.3rem; }

.step-card {
    background: #0d1117; border-radius: 8px; padding: 0.9rem 1.1rem;
    margin-bottom: 0.5rem; border-left: 4px solid #30363d;
    font-size: 0.85rem; color: #8b949e; line-height: 1.6;
}
.step-card.on-track { border-left-color: #238636; }
.step-card.potential-drift { border-left-color: #d29922; }
.step-card.drift-confirmed { border-left-color: #da3633; background: #1a0a0a; }
.step-card .step-header {
    display: flex; align-items: center; gap: 0.6rem; margin-bottom: 0.4rem;
}
.step-card .step-num {
    font-weight: 700; font-size: 0.78rem; padding: 0.15rem 0.5rem;
    border-radius: 4px; color: #0d1117;
}
.step-num.green { background: #238636; }
.step-num.amber { background: #d29922; }
.step-num.red { background: #da3633; }
.step-num.gray { background: #484f58; color: #8b949e; }
.step-card .thought-text { color: #c9d1d9; font-style: italic; }
.step-card .score-val { font-weight: 700; }
.step-card .score-val.green { color: #3fb950; }
.step-card .score-val.amber { color: #d29922; }
.step-card .score-val.red { color: #f85149; }

.intervention-box {
    background: linear-gradient(135deg, #1a0a0a 0%, #2d1215 100%);
    border: 1px solid #da3633; border-radius: 10px;
    padding: 1.1rem 1.3rem; margin: 0.8rem 0;
}
.intervention-box h4 { color: #f85149; font-size: 0.85rem; margin: 0 0 0.5rem 0; }
.intervention-box p { color: #ffa198; font-size: 0.85rem; line-height: 1.6; margin: 0; }

.scoring-table {
    width: 100%; border-collapse: collapse; font-size: 0.82rem; margin: 0.5rem 0;
}
.scoring-table th {
    text-align: left; padding: 0.5rem 0.8rem; color: #8b949e;
    border-bottom: 1px solid #30363d; font-weight: 600; font-size: 0.72rem;
    text-transform: uppercase; letter-spacing: 0.05em;
}
.scoring-table td {
    padding: 0.5rem 0.8rem; color: #e6edf3; border-bottom: 1px solid #21262d;
}
.scoring-table .dim { color: #8b949e; }

.pattern-bar {
    display: flex; align-items: center; gap: 0.5rem;
    font-size: 0.85rem; color: #e6edf3; margin: 0.3rem 0;
}
.pattern-bar .bar { background: #f85149; height: 16px; border-radius: 4px; min-width: 4px; }
.pattern-bar .label { color: #8b949e; min-width: 60px; }

.owner-card {
    background: #161b22; border: 1px solid #30363d; border-radius: 10px;
    padding: 0.8rem 1rem; margin-bottom: 0.5rem;
}
.owner-card .name { font-weight: 700; color: #58a6ff; font-size: 0.88rem; }
.owner-card .team { font-size: 0.78rem; color: #8b949e; }
.owner-card .count { font-size: 1.4rem; font-weight: 700; color: #f85149; float: right; }

.traj-selector {
    background: #161b22; border: 1px solid #30363d; border-radius: 8px;
    padding: 0.5rem 0.8rem; margin: 0.3rem 0; font-size: 0.82rem;
    color: #8b949e; cursor: pointer;
}
.traj-selector:hover { background: #21262d; }
.traj-selector.active { border-color: #58a6ff; color: #e6edf3; }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown('<div style="color:#e6edf3;font-size:1.15rem;font-weight:700;padding:0.3rem 0 1rem 0">Semantic Drift</div>', unsafe_allow_html=True)
    default_path = r"C:\Users\t-ashende\Documents\evalVNext-results\evalVNext-results"
    base_path = st.text_input("Data folder", value=default_path if os.path.isdir(default_path) else "")
    load_btn = st.button("Load & Analyze", type="primary", use_container_width=True)

# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------
if not base_path or not load_btn:
    if "drift_report" not in st.session_state:
        st.markdown("""
        <div class="drift-hero">
            <h1>Semantic Drift Detection</h1>
            <p>Offline keyword-based analysis — no API key needed.
            Detects where the agent drifted from the user's goal, classifies into failure buckets,
            and identifies synthesis gaps.</p>
        </div>
        """, unsafe_allow_html=True)
        st.info("Enter a data folder path in the sidebar and click **Load & Analyze**.")
        st.stop()

if load_btn and base_path:
    with st.spinner("Discovering batches and analyzing trajectories..."):
        batches = auto_discover_batches(base_path)
        all_cases = [c for cases in batches.values() for c in cases]
        report = analyze_batch(all_cases)
        st.session_state["drift_report"] = report
        st.session_state["all_cases"] = all_cases

if "drift_report" not in st.session_state:
    st.stop()

report = st.session_state["drift_report"]

# ---------------------------------------------------------------------------
# Hero + KPIs
# ---------------------------------------------------------------------------
st.markdown("""
<div class="drift-hero">
    <h1>Semantic Drift Detection</h1>
    <p>Offline analysis across {total} trajectories — {drifted} with detected drift</p>
</div>
""".format(total=report.total_trajectories, drifted=report.drifted_count), unsafe_allow_html=True)

drift_pct = round(100 * report.drifted_count / max(report.total_trajectories, 1), 1)
common_step = report.most_common_drift_step or "-"
st.markdown(f"""
<div class="kpi-strip">
    <div class="kpi-card"><div class="label">Trajectories</div><div class="value white">{report.total_trajectories}</div></div>
    <div class="kpi-card"><div class="label">Drifted</div><div class="value red">{report.drifted_count}</div></div>
    <div class="kpi-card"><div class="label">Drift Rate</div><div class="value amber">{drift_pct}%</div></div>
    <div class="kpi-card"><div class="label">Wasted Tokens</div><div class="value red">~{report.total_wasted_tokens:,}</div></div>
    <div class="kpi-card"><div class="label">Critical Step</div><div class="value blue">Step {common_step}</div></div>
</div>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Workflow deep-dive: pick a trajectory
# ---------------------------------------------------------------------------
drifted = [r for r in report.results if r.critical_step and r.total_steps >= 2]
if not drifted:
    st.success("No drift detected in any trajectory.")
    st.stop()

st.markdown('<div class="section-strip">Workflow Deep-Dive</div>', unsafe_allow_html=True)
st.markdown("Pick a trajectory to see its full workflow — every thought the agent had, where it drifted, and where a human checkpoint would help.")

options = [f"Q{r.query_index} — {r.query[:70]}... (drift at step {r.critical_step})"
           for r in drifted]
selected_idx = st.selectbox("Select trajectory", range(len(options)),
                            format_func=lambda i: options[i])
r = drifted[selected_idx]

# --- Query card ---
pass_label = "PASS" if r.passed else "FAIL"
pass_cls = "badge-pass" if r.passed else "badge-fail"
st.markdown(f"""
<div class="wf-card">
    <div class="wf-query">
        <div class="qlabel">User Query (Q{_h(r.query_index)})</div>
        {_h(r.query)}
    </div>
    <div style="display:flex;gap:0.5rem;flex-wrap:wrap;margin-bottom:0.8rem">
        <span class="badge badge-cat">Goal: {_h(r.query_cat)}</span>
        <span class="badge {pass_cls}">{pass_label}</span>
        <span class="badge" style="background:#21262d;color:#8b949e;border:1px solid #30363d">
            {r.total_steps} thought segments &nbsp;|&nbsp; {r.exec_time:.1f}s &nbsp;|&nbsp; ~{r.approx_tokens:,} tokens
        </span>
    </div>
""", unsafe_allow_html=True)

# --- Step-by-step workflow ---
st.markdown(f"""
    <div style="font-size:0.78rem;color:#8b949e;margin-bottom:0.6rem">
        FULL WORKFLOW — each card is one thought segment from the agent's internal reasoning
    </div>
""", unsafe_allow_html=True)

first_drift_step = None
for s in r.steps:
    if s.score is not None and s.score >= DRIFT_THRESHOLD:
        cls = "on-track"
        num_cls = "green"
        score_cls = "green"
        status_text = "On track"
    elif s.score is not None and s.score >= CRITICAL_THRESHOLD:
        cls = "potential-drift"
        num_cls = "amber"
        score_cls = "amber"
        status_text = "Potential drift"
    elif s.score is not None:
        cls = "drift-confirmed"
        num_cls = "red"
        score_cls = "red"
        status_text = "DRIFT"
    else:
        cls = ""
        num_cls = "gray"
        score_cls = ""
        status_text = "skipped"

    score_html = f'<span class="score-val {score_cls}">{s.score:.2f}</span>' if s.score is not None else '<span class="dim">-</span>'
    cat_html = f'<span class="dim">{_h(s.q_cat)}</span> &rarr; <span style="color:#e6edf3">{_h(s.t_cat)}</span>'
    bucket_html = f' &nbsp;|&nbsp; <span class="badge badge-bucket" style="font-size:0.68rem">{_h(s.bucket)}</span> <span class="badge badge-owner" style="font-size:0.68rem">{_h(s.fix_owner)}</span>' if s.bucket else ''

    is_critical = (r.critical_step == s.step)
    critical_marker = ' &nbsp; <span style="color:#f85149;font-weight:700;font-size:0.75rem">&#9888; INTERVENTION POINT</span>' if is_critical else ''

    st.markdown(f"""
    <div class="step-card {cls}">
        <div class="step-header">
            <span class="step-num {num_cls}">Step {s.step}</span>
            {score_html}
            <span class="dim">{status_text}</span>
            {critical_marker}
        </div>
        <div class="thought-text">"{_h(s.thought_preview[:200])}"</div>
        <div style="margin-top:0.3rem;font-size:0.78rem">
            {cat_html}{bucket_html}
        </div>
    </div>""", unsafe_allow_html=True)

    if s.intervene and first_drift_step is None:
        first_drift_step = s

# Close workflow card
st.markdown('</div>', unsafe_allow_html=True)

# --- Intervention suggestion ---
if first_drift_step:
    remaining = r.total_steps - first_drift_step.step + 1
    st.markdown(f"""
    <div class="intervention-box">
        <h4>Suggested Human Checkpoint: After Step {first_drift_step.step - 1}</h4>
        <p>
            The agent was on track through step {first_drift_step.step - 1}, then drifted at step {first_drift_step.step}
            (goal was <strong>{_h(first_drift_step.q_cat)}</strong>, agent switched to <strong>{_h(first_drift_step.t_cat)}</strong>).
            Inserting a checkpoint here would have saved <strong>{r.wasted_steps} steps</strong>
            (~{r.wasted_tokens:,} tokens). The fix belongs to <strong>{_h(first_drift_step.fix_owner)}</strong>
            ({_h(first_drift_step.bucket)}).
        </p>
    </div>
    """, unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Scoring breakdown for the drifting step
# ---------------------------------------------------------------------------
if first_drift_step:
    st.markdown(f'<div class="section-strip">Scoring Breakdown — Step {first_drift_step.step}</div>', unsafe_allow_html=True)
    st.markdown(f"""
    <div class="wf-card">
        <div style="font-size:0.82rem;color:#8b949e;margin-bottom:0.8rem">
            How the drift score of <strong style="color:#f85149">{first_drift_step.score:.2f}</strong> was computed
            (threshold: {DRIFT_THRESHOLD} for drift, {CRITICAL_THRESHOLD} for confirmed drift)
        </div>
    """, unsafe_allow_html=True)

    q_words = _extract_keywords(r.query)
    t_words = _extract_keywords(first_drift_step.thought_preview)
    overlap = q_words & t_words
    overlap_ratio = len(overlap) / max(len(q_words), 1)

    q_cat, _ = _get_category(r.query)
    t_cat, t_score = _get_category(first_drift_step.thought_preview)
    is_normal = (q_cat, t_cat) in NORMAL_TRANSITIONS
    if q_cat == t_cat:
        cat_match = 1.0
        cat_reason = "same category"
    elif is_normal:
        cat_match = 0.8
        cat_reason = "normal transition"
    else:
        cat_match = 0.3
        cat_reason = "category mismatch"

    q_analytical = q_cat in ("analysis", "calculation", "data_read")
    t_formatting = t_cat == "formatting" and t_score > 0
    penalty = 0.4 if (q_analytical and t_formatting) else 1.0
    penalty_reason = "analytical goal + formatting action" if penalty < 1.0 else "none"

    raw = (overlap_ratio * 0.4 + cat_match * 0.6) * penalty

    st.markdown(f"""
    <table class="scoring-table">
        <tr><th>Component</th><th>Value</th><th>Weight</th><th>Contribution</th></tr>
        <tr>
            <td>Keyword overlap</td>
            <td>{len(overlap)}/{len(q_words)} words &nbsp;
                <span class="dim">({', '.join(sorted(overlap)[:5]) if overlap else 'none'})</span></td>
            <td class="dim">40%</td>
            <td>{overlap_ratio:.2f} x 0.4 = <strong>{overlap_ratio*0.4:.2f}</strong></td>
        </tr>
        <tr>
            <td>Category alignment</td>
            <td>{_h(q_cat)} &rarr; {_h(t_cat)} &nbsp;
                <span class="dim">({cat_reason})</span></td>
            <td class="dim">60%</td>
            <td>{cat_match:.1f} x 0.6 = <strong>{cat_match*0.6:.2f}</strong></td>
        </tr>
        <tr>
            <td>Formatting penalty</td>
            <td><span class="dim">{penalty_reason}</span></td>
            <td class="dim">multiplier</td>
            <td>x {penalty:.1f}</td>
        </tr>
        <tr style="border-top:2px solid #30363d">
            <td><strong>Final score</strong></td>
            <td></td><td></td>
            <td><strong style="color:#f85149">{raw:.2f}</strong>
                <span class="dim">(threshold: {DRIFT_THRESHOLD})</span></td>
        </tr>
    </table>
    """, unsafe_allow_html=True)

    st.markdown(f"""
        <div style="margin-top:0.8rem;font-size:0.82rem;color:#8b949e">
            <strong style="color:#e6edf3">Query keywords:</strong> {', '.join(sorted(q_words)[:12])}<br/>
            <strong style="color:#e6edf3">Thought keywords:</strong> {', '.join(sorted(t_words)[:12])}<br/>
            <strong style="color:#e6edf3">Shared:</strong>
            <span style="color:#3fb950">{', '.join(sorted(overlap)) if overlap else 'none'}</span>
        </div>
    </div>
    """, unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Aggregate patterns (collapsed)
# ---------------------------------------------------------------------------
with st.expander("Cross-Trajectory Patterns", expanded=False):
    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**Which steps show drift**")
        if report.step_drift_counts:
            max_count = max(report.step_drift_counts.values())
            for step, count in sorted(report.step_drift_counts.items(), key=lambda x: -x[1])[:8]:
                width = int(200 * count / max(max_count, 1))
                st.markdown(f"""
                <div class="pattern-bar">
                    <div class="label">Step {step}</div>
                    <div class="bar" style="width:{width}px"></div>
                    <span style="color:#8b949e;font-size:0.8rem">{count}/{report.total_trajectories}</span>
                </div>""", unsafe_allow_html=True)

    with col2:
        st.markdown("**Top category mismatches**")
        if report.category_mismatches:
            for mismatch, count in sorted(report.category_mismatches.items(), key=lambda x: -x[1])[:8]:
                st.markdown(f"""
                <div style="font-size:0.85rem;color:#e6edf3;margin:0.2rem 0">
                    <code style="color:#ffa198">{_h(mismatch)}</code>
                    <span style="color:#8b949e;margin-left:0.5rem">{count}x</span>
                </div>""", unsafe_allow_html=True)

    if report.bucket_distribution:
        st.markdown("**Bucket ownership**")
        cols = st.columns(min(len(report.bucket_distribution), 4))
        for i, (bucket, count) in enumerate(sorted(report.bucket_distribution.items(), key=lambda x: -x[1])):
            owner = BUCKET_OWNERS.get(bucket, "Unknown")
            with cols[i % len(cols)]:
                st.markdown(f"""
                <div class="owner-card">
                    <span class="count">{count}</span>
                    <div class="name">{_h(bucket)}</div>
                    <div class="team">Owner: {_h(owner)}</div>
                </div>""", unsafe_allow_html=True)

# Full trajectory list (collapsed)
with st.expander(f"All Drifted Trajectories ({len(drifted)})", expanded=False):
    for dr in drifted:
        drift_steps = [s for s in dr.steps if s.intervene]
        bucket = drift_steps[0].bucket if drift_steps else ""
        owner = drift_steps[0].fix_owner if drift_steps else ""
        st.markdown(f"""
        <div style="background:#0d1117;border:1px solid #21262d;border-left:4px solid #da3633;
             border-radius:6px;padding:0.6rem 0.9rem;margin:0.3rem 0;font-size:0.82rem;color:#8b949e">
            <strong style="color:#e6edf3">Q{_h(dr.query_index)}</strong>: {_h(dr.query[:90])}
            <br/>Drift at step {dr.critical_step} | {dr.wasted_steps} wasted steps | ~{dr.wasted_tokens:,} tokens
            | <span style="color:#ffa198">{_h(bucket)}</span>
            <span style="color:#7ee787">{_h(owner)}</span>
        </div>""", unsafe_allow_html=True)
