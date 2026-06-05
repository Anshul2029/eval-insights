"""
metrics_config.py — Workflow-specific metric definitions.

Add a new entry to WORKFLOWS to support a different pipeline without
touching any existing evaluation code.
"""

WORKFLOWS = {
    "excel_word_report": {
        "name": "Excel → Word Report Workflow",
        "description": "Agent reads Excel, computes stats/anomalies, writes Word report.",
        "step_sequence": [
            {"action_type": "data_parsing",        "app": "Excel"},
            {"action_type": "computation",          "app": "Excel"},
            {"action_type": "context_handoff",      "app": "Excel→Word"},
            {"action_type": "report_structuring",   "app": "Word"},
            {"action_type": "narrative_generation", "app": "Word"},
        ],
        "plan_metrics": {
            "app_coverage":         "App Coverage (Excel + Word present)",
            "sequence_validity":    "Sequence Valid (parse→compute→handoff)",
            "anomaly_recall":       "Anomaly Recall (planted anomaly in Word output)",
            "false_positive_check": "False Positive Check (no fabrications on clean data)",
            "plan_quality":         "Plan Quality (LLM-assessed)",
        },
        "plan_metric_weights": {
            "app_coverage":         0.20,
            "sequence_validity":    0.20,
            "anomaly_recall":       0.30,
            "false_positive_check": 0.15,
            "plan_quality":         0.15,
        },
        "trajectory_weights": {
            "plan_score":     0.30,
            "avg_step_score": 0.70,
        },
    }
}

DEFAULT_WORKFLOW = "excel_word_report"


def get_workflow(name: str = None) -> dict:
    return WORKFLOWS.get(name or DEFAULT_WORKFLOW, WORKFLOWS[DEFAULT_WORKFLOW])
