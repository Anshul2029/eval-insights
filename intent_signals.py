"""
intent_signals.py — Extract intent flags from user_prompt via keyword matching.

Pure function, no LLM, no side effects.
"""

from __future__ import annotations

import re

_SIGNAL_PATTERNS = [
    ("anomaly",    [r"anomal", r"outlier", r"unusual", r"abnormal"]),
    ("trends",     [r"trend", r"pattern", r"over time", r"seasonal"]),
    ("issues",     [r"issue", r"problem", r"error", r"quality"]),
    ("report",     [r"report", r"document", r"word"]),
    ("leadership", [r"leadership", r"executive", r"management", r"stakeholder"]),
]


def extract_intents(user_prompt: str) -> dict[str, bool]:
    prompt_lower = user_prompt.lower() if user_prompt else ""
    intents = {}
    for flag, patterns in _SIGNAL_PATTERNS:
        intents[flag] = any(re.search(p, prompt_lower) for p in patterns)
    intents["general"] = not any(
        intents[k] for k in ("anomaly", "trends", "issues")
    )
    return intents
