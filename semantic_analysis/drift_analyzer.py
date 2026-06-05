"""
Offline semantic drift analyzer for agent trajectories.

Detects where the agent drifted from the user's stated goal using
keyword-based scoring — no API key needed. Classifies drift into
7 failure buckets with team ownership.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# Domain categories (keyword lists for classification)
# ---------------------------------------------------------------------------
CATEGORIES = {
    "analysis":    ["analyze", "compare", "trend", "insight", "revenue", "growth",
                    "performance", "q3", "q2", "q1", "balance", "sheet", "profit",
                    "loss", "sales", "report", "summary", "breakdown", "metric",
                    "average", "highest", "lowest", "top", "bottom", "rank"],
    "calculation": ["calculate", "compute", "sum", "average", "formula", "total",
                    "count", "aggregate", "sumifs", "vlookup", "max", "min",
                    "multiply", "divide", "percentage", "ratio", "countifs",
                    "sumproduct", "index", "match"],
    "data_read":   ["read", "get", "fetch", "load", "extract", "values", "rows",
                    "columns", "table", "range", "cell", "data", "find", "lookup",
                    "retrieve", "select", "filter", "sort", "getrange",
                    "getselectedrange", "getvalues"],
    "data_write":  ["write", "set", "update", "insert", "create", "add", "save",
                    "output", "apply", "put", "fill", "enter", "populate",
                    "setvalue", "setformula"],
    "formatting":  ["format", "width", "color", "font", "style", "alignment",
                    "border", "resize", "autofit", "bold", "italic", "highlight",
                    "column width", "cell format", "background", "numberformat"],
    "navigation":  ["sheet", "worksheet", "activate", "select", "switch",
                    "navigate", "goto", "activesheet", "getactivesheet",
                    "getworksheet"],
    "error":       ["error", "retry", "failed", "try again", "different approach",
                    "couldn't", "unable", "exception", "wrong", "incorrect", "fix"],
}

STOP_WORDS = frozenset({
    "the", "a", "an", "is", "are", "will", "to", "for", "in", "on", "at", "of",
    "i", "me", "my", "we", "it", "this", "that", "be", "was", "by", "with",
    "from", "have", "has", "do", "does", "can", "let", "im", "and", "or", "but",
    "not", "all", "so", "if", "then", "need", "want", "make", "use", "should",
})

# Normal category transitions — these are NOT drift
NORMAL_TRANSITIONS = {
    ("data_write", "data_read"),     # reading before writing is normal
    ("data_write", "navigation"),    # navigating to target sheet before writing
    ("data_write", "calculation"),   # computing values before writing
    ("calculation", "data_read"),    # reading data before computing
    ("analysis", "data_read"),       # reading data before analyzing
    ("analysis", "calculation"),     # computing during analysis
    ("analysis", "navigation"),      # switching sheets for analysis
    ("calculation", "navigation"),   # switching sheets for computation
    ("calculation", "analysis"),     # analysis as part of computation
    ("data_read", "navigation"),     # navigating to find data
    ("data_read", "data_write"),     # writing results after reading
    ("data_read", "calculation"),    # computing after reading
    ("data_read", "analysis"),       # analyzing after reading
    ("data_write", "analysis"),      # analyzing before writing
    ("formatting", "data_read"),     # reading before formatting
    ("formatting", "calculation"),   # computing during formatting
    ("formatting", "data_write"),    # writing is part of formatting workflow
}

# 7-bucket classification with team owners
BUCKET_OWNERS = {
    "Intent/Planning":          "Prompt Team",
    "Tool Selection":           "Agent Engineering",
    "Reasoning & Enumeration":  "Model Behavior",
    "Verification":             "Agent Architecture",
    "Synthesis/Output":         "Output Team",
    "Display/Rendering":        "UI Team",
    "Grader/Dataset":           "Dataset Team",
}

BUCKET_KEYWORDS = {
    "Intent/Planning":          ["instead", "misunderstand", "wrong goal", "different task",
                                 "not what", "misinterpret", "confused about"],
    "Tool Selection":           ["api", "method", "function", "getrange", "setvalue",
                                 "script", "wrong function", "wrong api"],
    "Reasoning & Enumeration":  ["enumerate", "count", "iterate", "loop", "each",
                                 "every", "all rows", "missed", "skipped", "forgot"],
    "Verification":             ["verify", "check", "confirm", "validate", "test",
                                 "assert", "make sure", "double check"],
    "Synthesis/Output":         ["output", "response", "answer", "result", "final",
                                 "summary", "conclude", "present"],
    "Display/Rendering":        ["format", "display", "render", "chart", "visual",
                                 "width", "style", "color", "font", "alignment"],
    "Grader/Dataset":           ["grader", "dataset", "expected", "benchmark",
                                 "ground truth", "test case"],
}

DRIFT_THRESHOLD    = 0.55
CRITICAL_THRESHOLD = 0.35


# ---------------------------------------------------------------------------
# Scoring functions
# ---------------------------------------------------------------------------
def _extract_keywords(text: str) -> set[str]:
    words = re.findall(r"[a-z][a-z0-9_]+", text.lower())
    return {w for w in words if w not in STOP_WORDS and len(w) > 2}


def _get_category(text: str) -> tuple[str, int]:
    text_lower = text.lower()
    scores = {cat: sum(1 for k in kws if k in text_lower)
              for cat, kws in CATEGORIES.items()}
    best = max(scores, key=scores.get)
    return best, scores[best]


def _relevance_score(query: str, thought: str, q_cat: str) -> float:
    q_words = _extract_keywords(query)
    t_words = _extract_keywords(thought)

    overlap = len(q_words & t_words) / max(len(q_words), 1)

    t_cat, t_score = _get_category(thought)

    if q_cat == t_cat:
        cat_match = 1.0
    elif (q_cat, t_cat) in NORMAL_TRANSITIONS:
        cat_match = 0.8
    else:
        cat_match = 0.3

    q_is_analytical = q_cat in ("analysis", "calculation", "data_read")
    t_is_formatting = t_cat == "formatting" and t_score > 0
    formatting_penalty = 0.4 if (q_is_analytical and t_is_formatting) else 1.0

    score = (overlap * 0.4 + cat_match * 0.6) * formatting_penalty
    return round(min(1.0, max(0.05, score)), 2)


# ---------------------------------------------------------------------------
# Priority step detection
# ---------------------------------------------------------------------------
_DECISION_WORDS = ["instead", "could", "let me try", "rather than",
                   "alternatively", "try again", "different"]
_WRITE_WORDS    = ["write", "set value", "update", "save", "create chart",
                   "insert", "add", "setvalue", "setformula"]
_ERROR_WORDS    = ["error", "failed", "retry", "couldn't", "try again",
                   "exception", "unable"]


def _get_priority_indices(thoughts: list[str]) -> list[int]:
    priority = set()
    for i, t in enumerate(thoughts):
        tl = t.lower()
        if i == 0:
            priority.add(i)
        if any(w in tl for w in _DECISION_WORDS):
            priority.add(i)
        if any(w in tl for w in _WRITE_WORDS):
            priority.add(i)
        if any(w in tl for w in _ERROR_WORDS):
            priority.add(i)
        if i == len(thoughts) - 1:
            priority.add(i)
    return sorted(priority)


# ---------------------------------------------------------------------------
# Bucket classification
# ---------------------------------------------------------------------------
def _classify_bucket(thought_text: str, q_cat: str, t_cat: str) -> tuple[str, str]:
    text_lower = thought_text.lower()
    scores = {}
    for bucket, keywords in BUCKET_KEYWORDS.items():
        scores[bucket] = sum(1 for k in keywords if k in text_lower)

    if t_cat == "formatting" and q_cat in ("analysis", "calculation"):
        scores["Display/Rendering"] += 2
    if t_cat == "error":
        scores["Tool Selection"] += 1
    if q_cat != t_cat and scores.get("Intent/Planning", 0) == 0:
        scores["Intent/Planning"] += 1

    best = max(scores, key=scores.get)
    if scores[best] == 0:
        best = "Intent/Planning"
    return best, BUCKET_OWNERS[best]


# ---------------------------------------------------------------------------
# Synthesis gap detection
# ---------------------------------------------------------------------------
_NUMBER_RE = re.compile(r"\b\d[\d,.]+\b")


def _extract_entities(text: str) -> set[str]:
    numbers = set(_NUMBER_RE.findall(text))
    keywords = _extract_keywords(text)
    return numbers | keywords


@dataclass
class SynthesisGap:
    computed_not_reported: set[str] = field(default_factory=set)
    gap_severity: float = 0.0


def check_synthesis_gap(thoughts_text: str, final_response: str) -> SynthesisGap:
    if not thoughts_text or not final_response:
        return SynthesisGap()

    thought_entities = _extract_entities(thoughts_text)
    response_entities = _extract_entities(final_response)

    numbers_in_thoughts = set(_NUMBER_RE.findall(thoughts_text))
    numbers_in_response = set(_NUMBER_RE.findall(final_response))
    dropped_numbers = numbers_in_thoughts - numbers_in_response

    noise = {"true", "false", "null", "none", "undefined", "error", "success"}
    dropped_numbers = {n for n in dropped_numbers
                       if len(n) > 2 and n not in noise}

    if not numbers_in_thoughts:
        severity = 0.0
    else:
        severity = len(dropped_numbers) / max(len(numbers_in_thoughts), 1)

    return SynthesisGap(
        computed_not_reported=dropped_numbers,
        gap_severity=round(min(1.0, severity), 2),
    )


# ---------------------------------------------------------------------------
# Per-case analysis
# ---------------------------------------------------------------------------
@dataclass
class StepResult:
    step: int
    priority: bool
    thought_preview: str
    score: Optional[float]
    status: str
    intervene: bool
    q_cat: str
    t_cat: str
    shared_keywords: list[str]
    bucket: str = ""
    fix_owner: str = ""


@dataclass
class DriftResult:
    query_index: str
    query: str
    query_cat: str
    passed: bool
    steps: list[StepResult]
    critical_step: Optional[int]
    total_steps: int
    wasted_steps: int
    wasted_tokens: int
    approx_tokens: int
    exec_time: float
    error_count: int
    synthesis_gap: SynthesisGap = field(default_factory=SynthesisGap)


def analyze_case(case) -> Optional[DriftResult]:
    """Analyze a ParsedEvalCase for semantic drift. Returns None if no thoughts."""
    query = case.query_text or ""
    query_id = str(case.query_index)

    thoughts = []
    for s in case.steps:
        if s.step_type == "Thoughts" and s.content:
            segments = s.content.replace("<|im_sep|>", "\n").strip()
            for chunk in segments.split("\n"):
                chunk = chunk.strip()
                if chunk and len(chunk) > 10:
                    thoughts.append(chunk)

    if not thoughts:
        return None

    total_chars = sum(len(s.content or "") + len(s.result or "") +
                      len(s.script_full or "") + len(s.text or "")
                      for s in case.steps)
    approx_tokens = total_chars // 4

    priority_idx = _get_priority_indices(thoughts)
    q_cat, _ = _get_category(query)
    q_keywords = _extract_keywords(query)

    step_results = []
    for i, thought in enumerate(thoughts):
        is_priority = i in priority_idx
        t_cat, _ = _get_category(thought)
        score = _relevance_score(query, thought, q_cat) if is_priority else None
        shared_kw = list(q_keywords & _extract_keywords(thought))[:4]

        if score is not None:
            if score < CRITICAL_THRESHOLD:
                status, intervene = "DRIFT CONFIRMED", True
            elif score < DRIFT_THRESHOLD:
                status, intervene = "POTENTIAL DRIFT", True
            else:
                status, intervene = "ON TRACK", False
        else:
            status, intervene = "skipped", False

        bucket, owner = ("", "")
        if intervene:
            bucket, owner = _classify_bucket(thought, q_cat, t_cat)

        step_results.append(StepResult(
            step=i + 1, priority=is_priority,
            thought_preview=thought[:150], score=score,
            status=status, intervene=intervene,
            q_cat=q_cat, t_cat=t_cat,
            shared_keywords=shared_kw,
            bucket=bucket, fix_owner=owner,
        ))

    critical_step = next((s.step for s in step_results if s.intervene), None)
    wasted = len(step_results) - critical_step + 1 if critical_step else 0
    wasted_tokens = int(approx_tokens * wasted / len(step_results)) if step_results else 0

    all_thoughts_text = " ".join(thoughts)
    final_resp = case.assistant_response or ""
    syn_gap = check_synthesis_gap(all_thoughts_text, final_resp)

    return DriftResult(
        query_index=query_id, query=query, query_cat=q_cat,
        passed=case.passed, steps=step_results,
        critical_step=critical_step, total_steps=len(step_results),
        wasted_steps=wasted, wasted_tokens=wasted_tokens,
        approx_tokens=approx_tokens,
        exec_time=case.execution_time_sec or 0.0,
        error_count=case.error_count,
        synthesis_gap=syn_gap,
    )


# ---------------------------------------------------------------------------
# Batch analysis
# ---------------------------------------------------------------------------
@dataclass
class BatchDriftReport:
    total_trajectories: int
    drifted_count: int
    total_wasted_tokens: int
    step_drift_counts: dict[int, int]
    category_mismatches: dict[str, int]
    bucket_distribution: dict[str, int]
    owner_distribution: dict[str, int]
    most_common_drift_step: Optional[int]
    results: list[DriftResult]


def analyze_batch(cases) -> BatchDriftReport:
    results = []
    for c in cases:
        r = analyze_case(c)
        if r:
            results.append(r)

    step_counts = defaultdict(int)
    cat_mismatches = defaultdict(int)
    bucket_dist = defaultdict(int)
    owner_dist = defaultdict(int)

    for r in results:
        for s in r.steps:
            if s.intervene:
                step_counts[s.step] += 1
                if s.q_cat != s.t_cat:
                    cat_mismatches[f"{s.q_cat} -> {s.t_cat}"] += 1
                if s.bucket:
                    bucket_dist[s.bucket] += 1
                    owner_dist[s.fix_owner] += 1

    drifted = [r for r in results if r.critical_step]
    total_wasted = sum(r.wasted_tokens for r in drifted)
    most_common = max(step_counts, key=step_counts.get) if step_counts else None

    return BatchDriftReport(
        total_trajectories=len(results),
        drifted_count=len(drifted),
        total_wasted_tokens=total_wasted,
        step_drift_counts=dict(step_counts),
        category_mismatches=dict(cat_mismatches),
        bucket_distribution=dict(bucket_dist),
        owner_distribution=dict(owner_dist),
        most_common_drift_step=most_common,
        results=results,
    )
