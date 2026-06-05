"""
trace_generator.py — Generate agent trajectory traces from real Excel data + Groq simulation.

Three-phase architecture:
  1. Python: Read Excel, compute real stats/z-scores → DataProfile
  2. Groq (1 call): Generate narrative prose for the chosen scenario
  3. Python: Assemble trace JSON using template-driven key_facts + LLM prose

Key_facts are NEVER LLM-generated — they use exact patterns that deterministic_checks.py expects.
"""

from __future__ import annotations

import json
import os
import random
from dataclasses import dataclass, field
from datetime import date
from enum import Enum

import numpy as np
import pandas as pd

from llm_provider import call_llm


class Scenario(Enum):
    GOOD_AGENT = "good_agent"
    LAZY_AGENT = "lazy_agent"
    WRONG_METHOD = "wrong_method"
    CONTEXT_LOSS = "context_loss"


KNOWN_ANOMALIES = {
    "01_clean_baseline.xlsx": {
        "planted": "None",
        "organic": "South/Product_B Mar (z=-2.84), South/Product_E Aug (z=-2.94), North/Product_B Nov (z=+2.36)",
    },
    "02_single_anomaly_subtle.xlsx": {
        "planted": "South/Product_B March: Revenue dropped from 37,655 (Feb) to 22,216 (Mar) — 41% decline. Global z-score = -1.47; requires per-series Region×Product analysis to detect.",
        "organic": "South/Product_E August: Revenue 20,000 vs series avg ~34,000",
    },
    "03_extreme_anomaly.xlsx": {
        "planted": "South/Product_B March: Revenue 7,531 — 80% drop from baseline ~37,655. Extreme outlier detectable by any method.",
        "organic": "South/Product_E August: Revenue 20,000",
    },
    "04_no_anomaly_control.xlsx": {
        "planted": "None",
        "organic": "None — clean control dataset with no Region/Product breakdown",
    },
    "05_multiple_anomalies.xlsx": {
        "planted": "Multiple planted: South/Product_B March 22,216 (41% drop), North/Product_A June 68,596 (spike)",
        "organic": "South/Product_E Aug (z=-2.94)",
    },
    "06_missing_values.xlsx": {
        "planted": "None",
        "organic": "Missing values present — data quality issue, not anomaly",
    },
    "07_inconsistent_months.xlsx": {
        "planted": "None",
        "organic": "Inconsistent month labels (abbreviations vs full names)",
    },
    "08_extra_columns.xlsx": {
        "planted": "None",
        "organic": "Extra columns (Internal_Code, Last_Updated, Random_Notes) — agent must ignore",
    },
    "09_zero_values.xlsx": {
        "planted": "Zero revenue values present — agent must flag as data quality issue",
        "organic": "Multiple zero-revenue entries across regions",
    },
    "10_trend_reversal.xlsx": {
        "planted": "H2 reversal: growth trend from H1 reverses in H2, indicating market shift",
        "organic": "Seasonal pattern disruption in Q3-Q4",
    },
}

_DATASET_QUIRKS = {
    "01_clean_baseline.xlsx": {},
    "02_single_anomaly_subtle.xlsx": {
        "step1_probing": True,
    },
    "03_extreme_anomaly.xlsx": {},
    "04_no_anomaly_control.xlsx": {
        "fewer_sections": 3,
    },
    "05_multiple_anomalies.xlsx": {},
    "06_missing_values.xlsx": {
        "step1_probing": True,
        "fewer_charts": 1,
        "fewer_sections": 3,
    },
    "07_inconsistent_months.xlsx": {
        "step1_probing": True,
        "month_label_quirk": True,
        "fewer_sections": 3,
    },
    "08_extra_columns.xlsx": {
        "fewer_sections": 3,
        "fewer_charts": 2,
    },
    "09_zero_values.xlsx": {
        "step1_probing": True,
        "fewer_charts": 1,
        "fewer_sections": 2,
    },
    "10_trend_reversal.xlsx": {
        "fewer_sections": 3,
        "fewer_charts": 2,
    },
}

USER_PROMPTS = [
    "Analyse this sales data and create a Word report for leadership",
    "Find any anomalies in this data and write a summary report",
    "Analyse this sales data and find any anomalies, create a Word report for leadership",
    "Review this sales data for issues and create a leadership report",
    "Analyze trends and anomalies in this data, produce a Word report",
]


@dataclass
class DataProfile:
    filename: str
    row_count: int
    columns: list[str]
    total_revenue: int
    total_units: int
    best_month: str
    worst_month: str
    regional_leader: str | None
    product_leader: str | None
    h2_vs_h1_pct: float
    anomalies: list[dict] = field(default_factory=list)
    planted_desc: str = "None"
    organic_desc: str = "None"
    missing_values: int = 0


def profile_excel(filepath: str) -> DataProfile:
    df = pd.read_excel(filepath)
    filename = os.path.basename(filepath)
    row_count = len(df)
    columns = list(df.columns)

    total_revenue = int(df["Revenue"].sum())
    total_units = int(df["Units_Sold"].sum())
    missing_values = int(df.isna().sum().sum())

    month_order = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                   "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    month_map = {m: i for i, m in enumerate(month_order)}

    monthly = df.groupby("Month")["Revenue"].sum()
    if len(monthly) > 0:
        best_m = monthly.idxmax()
        worst_m = monthly.idxmin()
        best_month = f"{best_m} ({int(monthly[best_m]):,})"
        worst_month = f"{worst_m} ({int(monthly[worst_m]):,})"
    else:
        best_month = "N/A"
        worst_month = "N/A"

    regional_leader = None
    product_leader = None
    if "Region" in columns:
        reg = df.groupby("Region")["Revenue"].sum()
        top_reg = reg.idxmax()
        regional_leader = f"{top_reg} ({reg[top_reg] / total_revenue * 100:.1f}%)"
    if "Product" in columns:
        prod = df.groupby("Product")["Revenue"].sum()
        top_prod = prod.idxmax()
        product_leader = f"{top_prod} ({prod[top_prod] / total_revenue * 100:.1f}%)"

    h1_months = {"Jan", "Feb", "Mar", "Apr", "May", "Jun"}
    h2_months = {"Jul", "Aug", "Sep", "Oct", "Nov", "Dec"}
    h1_rev = df[df["Month"].isin(h1_months)]["Revenue"].sum()
    h2_rev = df[df["Month"].isin(h2_months)]["Revenue"].sum()
    h2_vs_h1_pct = ((h2_rev - h1_rev) / h1_rev * 100) if h1_rev > 0 else 0.0

    anomalies = []
    if "Region" in columns and "Product" in columns:
        grouped = df.groupby(["Region", "Product"])["Revenue"]
        for (reg, prod), grp in grouped:
            if len(grp) < 3:
                continue
            mean_val = grp.mean()
            std_val = grp.std()
            if std_val == 0:
                continue
            for idx, val in grp.items():
                z = (val - mean_val) / std_val
                if abs(z) >= 2.0:
                    month = df.loc[idx, "Month"]
                    anomalies.append({
                        "region": reg, "product": prod, "month": month,
                        "revenue": int(val), "z_score": round(z, 2),
                    })

    known = KNOWN_ANOMALIES.get(filename, {})
    planted_desc = known.get("planted", "None")
    organic_desc = known.get("organic", "None")

    return DataProfile(
        filename=filename, row_count=row_count, columns=columns,
        total_revenue=total_revenue, total_units=total_units,
        best_month=best_month, worst_month=worst_month,
        regional_leader=regional_leader, product_leader=product_leader,
        h2_vs_h1_pct=round(h2_vs_h1_pct, 1),
        anomalies=anomalies, planted_desc=planted_desc,
        organic_desc=organic_desc, missing_values=missing_values,
    )


def build_step_ground_truth(profile: DataProfile, step_number: int) -> dict:
    if step_number == 1:
        return {
            "expected_rows": profile.row_count,
            "expected_columns": profile.columns,
            "expected_missing_values": profile.missing_values,
        }
    elif step_number == 2:
        return {
            "expected_total_revenue": profile.total_revenue,
            "expected_total_units": profile.total_units,
            "expected_best_month": profile.best_month,
            "expected_worst_month": profile.worst_month,
            "expected_regional_leader": profile.regional_leader,
            "expected_product_leader": profile.product_leader,
            "expected_anomalies": [
                f"{a['region']}/{a['product']} {a['month']} z={a['z_score']}"
                for a in profile.anomalies
            ],
            "expected_anomaly_count": len(profile.anomalies),
            "planted_anomaly": profile.planted_desc,
        }
    elif step_number == 3:
        return {
            "expected_facts_to_transfer": ["total_revenue", "anomaly_list", "regional_leader"],
            "expected_anomaly_count": len(profile.anomalies),
            "expected_total_revenue": profile.total_revenue,
        }
    elif step_number == 5:
        return {
            "must_mention_anomalies": [
                f"{a['region']}/{a['product']}" for a in profile.anomalies[:5]
            ],
            "expected_total_revenue": profile.total_revenue,
            "planted_anomaly": profile.planted_desc,
        }
    return {}


def build_key_facts(profile: DataProfile, scenario: Scenario) -> dict[int, list[str]]:
    has_planted = profile.planted_desc and not profile.planted_desc.lower().startswith("none")
    has_region = profile.regional_leader is not None
    quirks = _DATASET_QUIRKS.get(profile.filename, {})

    # Step 1: data_parsing — same for all scenarios
    step1 = [
        f"row_count: {profile.row_count}",
        f"columns: [{', '.join(profile.columns)}]",
        "month_ordering_applied: true",
        "null_values: none" if profile.missing_values == 0 else f"missing_values: {profile.missing_values}",
    ]
    if quirks.get("step1_probing"):
        step1.append("environment_probing: true")
    if quirks.get("month_label_quirk"):
        step1.extend([
            "month_label_inconsistency_detected: true",
            "inconsistency_handled: true",
        ])

    # Step 2: computation — scenario-dependent
    if scenario == Scenario.GOOD_AGENT:
        step2 = [
            f"total_revenue: {profile.total_revenue:,}",
            f"total_units: {profile.total_units:,}",
            f"best_month: {profile.best_month}",
            f"worst_month: {profile.worst_month}",
            f"h2_vs_h1: +{profile.h2_vs_h1_pct}%" if profile.h2_vs_h1_pct > 0 else f"h2_vs_h1: {profile.h2_vs_h1_pct}%",
        ]
        if has_region:
            step2.append(f"regional_leader: {profile.regional_leader}")
        if profile.product_leader:
            step2.append(f"product_leader: {profile.product_leader}")
        step2.append("anomaly_detection_method: z-score per Region\u00d7Product, threshold |z|>=2")
        if profile.anomalies:
            anom_strs = [f"{a['region']}/{a['product']} {a['month']} z={a['z_score']}" for a in profile.anomalies[:5]]
            step2.append(f"anomalies_detected: [{', '.join(anom_strs)}]")
        if has_planted:
            step2.append("planted_anomaly_caught: true")
        else:
            step2.append("false_positive_fabricated: false")

    elif scenario == Scenario.LAZY_AGENT:
        step2 = [
            f"total_revenue: {profile.total_revenue:,}",
            f"avg_revenue: {profile.total_revenue // profile.row_count}",
            "anomaly_detection: NOT PERFORMED",
            "regional_breakdown: NOT COMPUTED",
            "granularity: aggregate only",
        ]
        if has_planted:
            step2.append("planted_anomaly_caught: false")

    elif scenario == Scenario.WRONG_METHOD:
        step2 = [
            f"total_revenue: {profile.total_revenue:,}",
            f"total_units: {profile.total_units:,}",
        ]
        if has_region:
            step2.append(f"regional_leader: {profile.regional_leader}")
        step2.extend([
            "anomaly_detection_method: global z-score across all rows",
            "granularity_used: aggregate",
            "outliers_detected: 0",
        ])
        if has_planted:
            step2.append("planted_anomaly_caught: false")

    elif scenario == Scenario.CONTEXT_LOSS:
        step2 = [
            f"total_revenue: {profile.total_revenue:,}",
            f"total_units: {profile.total_units:,}",
            f"best_month: {profile.best_month}",
            f"worst_month: {profile.worst_month}",
        ]
        if has_region:
            step2.append(f"regional_leader: {profile.regional_leader}")
        step2.append("anomaly_detection_method: z-score per Region\u00d7Product, threshold |z|>=2")
        if profile.anomalies:
            anom_strs = [f"{a['region']}/{a['product']} {a['month']} z={a['z_score']}" for a in profile.anomalies[:5]]
            step2.append(f"anomalies_detected: [{', '.join(anom_strs)}]")
        if has_planted:
            step2.append("planted_anomaly_caught: true")
        else:
            step2.append("false_positive_fabricated: false")
    else:
        step2 = [f"total_revenue: {profile.total_revenue:,}"]

    # Step 3: context_handoff
    if scenario == Scenario.CONTEXT_LOSS:
        step3 = [
            "total_revenue_passed: true",
            "anomaly_data_passed: false",
            "charts_generated: 0",
            "context_loss_at_boundary: true",
        ]
    elif scenario == Scenario.LAZY_AGENT:
        step3 = [
            "charts_generated: 1",
            "anomaly_data_passed: false",
        ]
    else:
        n_charts = quirks.get("fewer_charts", random.randint(3, 5))
        step3 = [
            f"charts_generated: {n_charts}",
            "all_kpis_passed: true",
        ]
        if scenario == Scenario.GOOD_AGENT and profile.anomalies:
            anom_names = [f"{a['region']}/{a['product']} {a['month']}" for a in profile.anomalies[:5]]
            step3.append(f"anomaly_list_passed: [{', '.join(anom_names)}]")

    # Step 4: report_structuring
    if scenario in (Scenario.GOOD_AGENT, Scenario.CONTEXT_LOSS):
        n_sections = quirks.get("fewer_sections", random.randint(6, 9))
        step4 = [f"sections: {n_sections} plus appendix"]
        if scenario == Scenario.GOOD_AGENT and has_planted:
            step4.append("anomaly_section: present")
        elif scenario == Scenario.CONTEXT_LOSS:
            step4.append("anomaly_section: NOT PRESENT")
        step4.append(f"charts_embedded: {quirks.get('fewer_charts', random.randint(3, 5))}")
    elif scenario == Scenario.WRONG_METHOD:
        step4 = [
            "sections: 5",
            "anomaly_section: NOT PRESENT",
        ]
    elif scenario == Scenario.LAZY_AGENT:
        step4 = [
            "sections: 2",
            "charts: none",
        ]
    else:
        step4 = ["sections: 5"]

    # Step 5: narrative_generation
    if scenario == Scenario.GOOD_AGENT:
        step5 = [
            f"exec_summary: Full-year revenue {profile.total_revenue:,} across {profile.total_units:,} units. H2 {'up' if profile.h2_vs_h1_pct > 0 else 'down'} {abs(profile.h2_vs_h1_pct)}% vs H1.",
        ]
        if has_planted and profile.anomalies:
            step5.append("planted_anomaly_in_narrative: true")
            step5.append(f"recommendation_1: Investigate {profile.anomalies[0]['region']}/{profile.anomalies[0]['product']} {profile.anomalies[0]['month']} anomaly.")
        else:
            step5.append("recommendation_1: Continue monitoring trends.")
    elif scenario == Scenario.LAZY_AGENT:
        step5 = [
            f"exec_summary: Total revenue {profile.total_revenue:,}. Business performing well.",
            "report_depth: insufficient",
            "false_clean_verdict: true",
            "recommendation: Continue current strategy.",
        ]
        if has_planted:
            step5.append("planted_anomaly_in_narrative: false")
    elif scenario == Scenario.WRONG_METHOD:
        step5 = [
            f"exec_summary: Revenue {profile.total_revenue:,}. No significant outliers at applied threshold.",
            "false_clean_verdict: true",
        ]
        if has_planted:
            step5.append("planted_anomaly_in_narrative: false")
            step5.append("south_productB_mentioned: false")
        step5.append("recommendation: Continue current strategy.")
    elif scenario == Scenario.CONTEXT_LOSS:
        step5 = [
            f"exec_summary: Revenue {profile.total_revenue:,}.",
            "report_depth: insufficient",
        ]
        if has_planted:
            step5.append("planted_anomaly_in_narrative: false")
            step5.append("false_clean_verdict: true")
        step5.append("recommendation_1: Review data quality.")

    return {1: step1, 2: step2, 3: step3, 4: step4, 5: step5}


_LLM_SYSTEM = """You are simulating an Excel Copilot agent that analyzes sales data and creates a Word report.
Given a data profile and scenario, generate realistic agent outputs.
Rules:
- Use specific numbers from the data profile. Never invent statistics.
- Keep each step output to 1-3 sentences.
- Return valid JSON only, no markdown fences.
- Structure: {"agent_plan":"...","step_outputs":["...","...","...","...","..."],"executive_summary":"...","anomaly_section":"...","recommendations":["...","...","..."]}"""

_SCENARIO_DIRECTIVES = {
    Scenario.GOOD_AGENT: "The agent performs thorough analysis using z-scores per Region x Product. It catches all anomalies and produces a detailed report with specific numbers.",
    Scenario.LAZY_AGENT: "The agent only computes aggregate totals (sum, mean). It does NOT perform anomaly detection. The report says 'business is performing well'. Recommendations are generic.",
    Scenario.WRONG_METHOD: "The agent uses a global z-score across all rows instead of per Region x Product. The planted anomaly's z-score falls below threshold and is missed. Report says no anomalies.",
    Scenario.CONTEXT_LOSS: "The agent performs correct analysis in Excel but when handing off to Word, only passes total_revenue. Anomaly data is NOT transferred. Word report has no anomaly section.",
}


def generate_llm_content(profile: DataProfile, scenario: Scenario) -> dict:
    anomaly_desc = ""
    if profile.anomalies:
        anomaly_desc = "; ".join(f"{a['region']}/{a['product']} {a['month']} rev={a['revenue']:,} z={a['z_score']}" for a in profile.anomalies[:5])

    user_prompt = f"""Data Profile:
- File: {profile.filename}
- Rows: {profile.row_count}, Columns: {profile.columns}
- Total Revenue: {profile.total_revenue:,}, Total Units: {profile.total_units:,}
- Best Month: {profile.best_month}, Worst Month: {profile.worst_month}
- Regional Leader: {profile.regional_leader or 'N/A'}
- Product Leader: {profile.product_leader or 'N/A'}
- Anomalies Found: {anomaly_desc or 'None'}
- Planted Anomaly: {profile.planted_desc}

Scenario: {_SCENARIO_DIRECTIVES[scenario]}

Return JSON only."""

    try:
        raw = call_llm(_LLM_SYSTEM, user_prompt, max_tokens=1500)
        if raw:
            text = raw.strip()
            if "```" in text:
                for part in text.split("```"):
                    part = part.strip().lstrip("json").strip()
                    if part.startswith("{"):
                        text = part
                        break
            start, end = text.find("{"), text.rfind("}") + 1
            if start >= 0 and end > start:
                return json.loads(text[start:end])
    except Exception:
        pass

    return _fallback_content(profile, scenario)


_MONTH_EXPAND = {
    "Jan": "January", "Feb": "February", "Mar": "March", "Apr": "April",
    "May": "May", "Jun": "June", "Jul": "July", "Aug": "August",
    "Sep": "September", "Oct": "October", "Nov": "November", "Dec": "December",
}


def _expand_month(abbr: str) -> str:
    return _MONTH_EXPAND.get(abbr, abbr)


def _fallback_content(profile: DataProfile, scenario: Scenario) -> dict:
    if scenario == Scenario.GOOD_AGENT:
        plan = f"Load {profile.filename}, compute KPIs and z-score anomaly detection per Region x Product, create charts, write detailed Word report."

        anom_detail_strs = []
        for a in profile.anomalies[:5]:
            month_full = _expand_month(a['month'])
            anom_detail_strs.append(
                f"{a['region']}/{a['product']} in {month_full}: revenue {a['revenue']:,} (z-score {a['z_score']})"
            )

        outputs = [
            f"{profile.row_count} rows x {len(profile.columns)} columns loaded. All expected columns present.",
            f"Total revenue {profile.total_revenue:,}. Z-score analysis at Region x Product granularity. {len(profile.anomalies)} outliers detected.",
            f"4 chart PNGs saved. All aggregates and anomaly list passed into Word script.",
            f"8-section Word document created with anomaly section.",
            f"Executive summary with specific anomaly mentions. 5 recommendations written.",
        ]

        h2_dir = "up" if profile.h2_vs_h1_pct > 0 else "down"
        best_expanded = profile.best_month
        worst_expanded = profile.worst_month
        for abbr, full in _MONTH_EXPAND.items():
            best_expanded = best_expanded.replace(abbr, full)
            worst_expanded = worst_expanded.replace(abbr, full)
        exec_sum = (
            f"Full-year revenue {profile.total_revenue:,} across {profile.total_units:,} units. "
            f"H2 {h2_dir} {abs(profile.h2_vs_h1_pct)}% vs H1. "
            f"Best month: {best_expanded}. Worst month: {worst_expanded}."
        )
        if anom_detail_strs:
            exec_sum += f" {len(profile.anomalies)} anomalies detected requiring investigation."

        if anom_detail_strs:
            anom_section = (
                f"{len(profile.anomalies)} statistical outliers detected at |z|>=2 threshold. "
                + "Anomaly details: " + "; ".join(anom_detail_strs) + "."
            )
        else:
            anom_section = "No significant anomalies detected. Data appears consistent across all Region x Product combinations."

        recs = []
        if profile.anomalies:
            top = profile.anomalies[0]
            recs.append(f"Investigate {top['region']}/{top['product']} {_expand_month(top['month'])} anomaly — revenue dropped to {top['revenue']:,} (z={top['z_score']}).")
        recs.extend(["Monitor seasonal trends.", "Automate monthly z-score checks."])
    elif scenario == Scenario.LAZY_AGENT:
        plan = f"Load {profile.filename}, compute summary statistics, create basic Word report."
        outputs = [
            f"{profile.row_count} rows loaded.",
            f"Total revenue {profile.total_revenue:,}. Average revenue {profile.total_revenue // profile.row_count}.",
            "Basic facts transferred to Word.",
            "2-section report created.",
            "Brief summary written. No detailed analysis.",
        ]
        exec_sum = f"Total revenue {profile.total_revenue:,}. Business performing well."
        anom_section = "No anomalies identified."
        recs = ["Continue current strategy.", "Monitor quarterly trends."]
    elif scenario == Scenario.WRONG_METHOD:
        plan = f"Load {profile.filename}, compute statistics with global z-score analysis, create Word report."
        outputs = [
            f"{profile.row_count} rows loaded.",
            f"Total revenue {profile.total_revenue:,}. Global z-score: 0 outliers at |z|>=3.",
            "KPIs transferred to Word. No anomaly data (none detected).",
            "5-section report created. No anomaly section.",
            "Summary states no significant outliers found.",
        ]
        exec_sum = f"Revenue {profile.total_revenue:,}. No significant statistical outliers at applied threshold."
        anom_section = "No significant outliers identified."
        recs = ["Continue monitoring.", "Review quarterly."]
    else:
        plan = f"Load {profile.filename}, analyze data, create Word report."
        outputs = [
            f"{profile.row_count} rows loaded.",
            f"Total revenue {profile.total_revenue:,}. Analysis complete.",
            "Partial data transferred to Word. Anomaly details lost.",
            "Report sections created without anomaly data.",
            f"Revenue {profile.total_revenue:,}. Limited analysis in narrative.",
        ]
        exec_sum = f"Revenue {profile.total_revenue:,}."
        anom_section = "Analysis data not available in report context."
        recs = ["Review data quality.", "Repeat analysis."]

    return {
        "agent_plan": plan,
        "step_outputs": outputs,
        "executive_summary": exec_sum,
        "anomaly_section": anom_section,
        "recommendations": recs,
    }


_STEP_META = [
    {"app": "Excel", "action_type": "data_parsing", "tools_called": ["pandas.read_excel", "pd.Categorical"]},
    {"app": "Excel", "action_type": "computation", "tools_called": ["pandas groupby", "numpy z-score", "pct_change"]},
    {"app": "Excel\u2192Word", "action_type": "context_handoff", "tools_called": ["matplotlib.savefig", "python-docx Document"]},
    {"app": "Word", "action_type": "report_structuring", "tools_called": ["python-docx Document, add_heading, add_table, add_picture"]},
    {"app": "Word", "action_type": "narrative_generation", "tools_called": ["python-docx paragraph formatting"]},
]

_LATENCY_RANGES = [(2, 5), (5, 15), (5, 12), (3, 8), (2, 6)]


def assemble_trace(
    profile: DataProfile,
    scenario: Scenario,
    key_facts: dict[int, list[str]],
    llm_content: dict,
    trace_id: str,
) -> dict:
    has_planted = profile.planted_desc and not profile.planted_desc.lower().startswith("none")
    step_outputs = llm_content.get("step_outputs", [""] * 5)

    steps = []
    for i in range(5):
        step_num = i + 1
        meta = _STEP_META[i]
        lat_lo, lat_hi = _LATENCY_RANGES[i]

        if scenario == Scenario.WRONG_METHOD and step_num == 2:
            tools = ["pandas groupby", "global z-score on Revenue, threshold |z|>=3"]
        elif scenario == Scenario.LAZY_AGENT and step_num == 2:
            tools = ["df.Revenue.sum()", "df.Revenue.mean()"]
        else:
            tools = meta["tools_called"]

        steps.append({
            "step_number": step_num,
            "app": meta["app"],
            "action_type": meta["action_type"],
            "latency_observed": f"approx {random.randint(lat_lo, lat_hi)} seconds",
            "tools_called": tools,
            "output": step_outputs[i] if i < len(step_outputs) else "",
            "key_facts_produced": key_facts.get(step_num, []),
            "ground_truth": build_step_ground_truth(profile, step_num),
        })

    # Build context_manifest
    step2_facts = key_facts.get(2, [])
    facts_produced = [f for f in step2_facts if ":" in f and "anomaly_detection" not in f.lower() and "granularity" not in f.lower()]

    if scenario == Scenario.CONTEXT_LOSS:
        facts_present = ["total_revenue: PRESENT"]
        for f in facts_produced:
            key = f.split(":")[0].strip()
            if key != "total_revenue":
                facts_present.append(f"{key}: ABSENT")
        facts_lost = [f for f in facts_produced if not f.startswith("total_revenue")]
        context_loss = True
    elif scenario == Scenario.LAZY_AGENT:
        facts_present = ["total_revenue: PRESENT", "avg_revenue: PRESENT"]
        facts_lost = []
        context_loss = False
    else:
        facts_present = [f"{f.split(':')[0].strip()}: PRESENT" for f in facts_produced]
        facts_lost = []
        context_loss = False

    context_manifest = {
        "facts_produced_in_excel_step2": facts_produced,
        "facts_present_in_word_output": facts_present,
        "facts_lost_at_boundary": facts_lost,
        "boundary": "Excel\u2192Word (Step 3)",
        "context_loss_detected": context_loss,
    }

    recs = llm_content.get("recommendations", ["Review data.", "Monitor trends."])

    return {
        "trace_id": trace_id,
        "run_date": date.today().isoformat(),
        "dataset_file": profile.filename,
        "user_prompt": random.choice(USER_PROMPTS),
        "source_data_summary": {
            "rows": profile.row_count,
            "columns": profile.columns,
            "ground_truth": f"Planted: {profile.planted_desc}. Organic: {profile.organic_desc}",
            "ground_truth_anomalies": {
                "planted": profile.planted_desc,
                "organic": profile.organic_desc,
            },
        },
        "agent_plan": llm_content.get("agent_plan", f"Analyze {profile.filename} and create report."),
        "steps": steps,
        "context_manifest": context_manifest,
        "word_output_actual_text": {
            "executive_summary": llm_content.get("executive_summary", f"Revenue {profile.total_revenue:,}."),
            "anomaly_section": llm_content.get("anomaly_section", ""),
            "recommendations": recs,
        },
    }


def generate_trace(
    excel_path: str,
    scenario: Scenario,
    trace_id: str,
    use_llm: bool = True,
) -> dict:
    profile = profile_excel(excel_path)
    key_facts = build_key_facts(profile, scenario)
    if use_llm:
        llm_content = generate_llm_content(profile, scenario)
    else:
        llm_content = _fallback_content(profile, scenario)
    return assemble_trace(profile, scenario, key_facts, llm_content, trace_id)
