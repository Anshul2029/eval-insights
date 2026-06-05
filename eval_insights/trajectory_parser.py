"""
Generic trajectory parser for evalVNext playOutput JSON files.

Extracts and normalises trajectory data from any evalVNext report,
auto-discovers all evalCases, and computes classification-relevant signals.
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Optional

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass
class TrajectoryStep:
    step_index: int
    step_type: str  # UserQuery | ScriptExecution | ScriptResponse | Thoughts | Assistant
    raw: dict = field(repr=False, default_factory=dict)

    # UserQuery
    text: str = ""

    # ScriptExecution
    script_full: str = ""
    script_len: int = 0
    script_preview: str = ""

    # ScriptResponse
    result: str = ""
    console: str = ""
    error_type: Optional[str] = None

    # Thoughts
    content: str = ""
    thought_segments: list[tuple[str, str]] = field(default_factory=list)

    def to_legacy_dict(self) -> dict:
        """Return a dict matching the _traj_full.json format."""
        d: dict[str, Any] = {"type": self.step_type}
        if self.step_type == "UserQuery":
            d["text"] = self.text
        elif self.step_type == "ScriptExecution":
            d["script_full"] = self.script_full
            d["script_len"] = self.script_len
            d["script_preview"] = self.script_preview
        elif self.step_type == "ScriptResponse":
            d["result"] = self.result
            d["console"] = self.console
        elif self.step_type == "Thoughts":
            d["content"] = self.content
        elif self.step_type == "Assistant":
            d["text"] = self.text
        return d


@dataclass
class ParsedEvalCase:
    query_index: str
    batch: str
    query_text: str
    workbook: str
    tags: list[str]
    steps: list[TrajectoryStep]
    passed: bool
    response_status: str
    execution_time_sec: float
    evaluation_results: list[dict] = field(default_factory=list)
    retry_diagnostics: Optional[list] = None
    assistant_response: str = ""
    chat_log: list[dict] = field(default_factory=list)

    # --- computed signals (populated by compute_signals) ---
    step_count: int = 0
    script_exec_count: int = 0
    script_response_count: int = 0
    error_count: int = 0
    error_types: list[str] = field(default_factory=list)
    retry_count: int = 0
    thoughts_total_chars: int = 0
    thoughts_segments: list[tuple[str, str]] = field(default_factory=list)
    script_similarity_groups: list[list[int]] = field(default_factory=list)
    has_success_claim: bool = False
    error_positions: list[int] = field(default_factory=list)

    def to_legacy_dict(self) -> dict:
        return {
            "query": self.query_text,
            "steps": [s.to_legacy_dict() for s in self.steps],
            "retryDiagnostics": self.retry_diagnostics,
        }


# ---------------------------------------------------------------------------
# Error classification for ScriptResponse
# ---------------------------------------------------------------------------

_ERROR_PATTERNS: list[tuple[str, str]] = [
    (r"dimension\s*mismatch", "DimensionMismatch"),
    (r"not\s*permitted", "NotPermitted"),
    (r"ItemNotFound", "ItemNotFound"),
    (r"InvalidOperation", "InvalidOperation"),
    (r"GeneralException", "GeneralException"),
    (r"InvalidArgument", "InvalidArgument"),
    (r"InvalidReference", "InvalidReference"),
    (r"RichApiMessageProcessingError", "RichApiError"),
    (r"error|Error|ERROR", "GenericError"),
]


def classify_script_response(result: str, console: str = "") -> Optional[str]:
    combined = f"{result} {console}"
    for pattern, label in _ERROR_PATTERNS:
        if re.search(pattern, combined):
            return label
    return None


# ---------------------------------------------------------------------------
# Thoughts parser
# ---------------------------------------------------------------------------

def split_thoughts(content: str) -> list[tuple[str, str]]:
    """Split Thoughts content on <|im_sep|> markers into (header, body) pairs."""
    parts = re.split(r"<\|im_sep\|>", content)
    segments = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        lines = p.split("\n", 1)
        header = lines[0].strip()
        body = lines[1].strip() if len(lines) > 1 else ""
        segments.append((header, body))
    return segments


# ---------------------------------------------------------------------------
# Script intent inference
# ---------------------------------------------------------------------------

def describe_script(content: str) -> str:
    """Infer a one-line intent from a script's content."""
    c = content.lower()
    keywords = [
        ("pivottable", "pivot table"),
        ("pivot", "pivot table"),
        ("chart", "chart operation"),
        ("geometricshape", "shape manipulation"),
        ("conditionalformat", "conditional formatting"),
        ("tabcolor", "tab color formatting"),
        ("tab_color", "tab color formatting"),
        ("numberformat", "number formatting"),
        (".name =", "rename operation"),
        ("rename", "rename operation"),
        ("formula", "formula operation"),
        ("filter", "data filtering"),
        ("sort", "data sorting"),
        ("merge", "cell merging"),
        ("autofit", "column auto-fit"),
        ("validation", "data validation"),
        ("freeze", "freeze panes"),
        ("protection", "sheet protection"),
        ("comment", "comment operation"),
        ("setvalues", "range write"),
        ("setvalue", "range write"),
        ("addcolumn", "add column"),
        ("addrow", "add row"),
        ("addworksheet", "add worksheet"),
        ("worksheets.add", "add worksheet"),
        (".delete()", "delete operation"),
        ("getrange", "range read"),
        ("getusedrange", "range read"),
        ("getdatabodyrange", "range read"),
        ("copy", "copy/paste"),
    ]
    for kw, desc in keywords:
        if kw in c:
            return desc
    if "worksheet" in c:
        return "worksheet operation"
    return "script execution"


# ---------------------------------------------------------------------------
# Step extraction from raw evalCase
# ---------------------------------------------------------------------------

def _extract_steps(trajectory: list[dict]) -> list[TrajectoryStep]:
    steps: list[TrajectoryStep] = []
    for idx, t in enumerate(trajectory):
        tp = t.get("type", "Unknown")
        step = TrajectoryStep(step_index=idx, step_type=tp, raw=t)

        if tp == "UserQuery":
            sig = t.get("signal") or {}
            step.text = (
                sig.get("userQuery")
                or sig.get("query")
                or t.get("query")
                or ""
            )
        elif tp == "ScriptExecution":
            content = t.get("content") or ""
            step.script_full = content
            step.script_len = len(content)
            step.script_preview = content[:800].replace("\n", " ")
        elif tp == "ScriptResponse":
            sig = t.get("signal") or {}
            if isinstance(sig, dict):
                sr = sig.get("scriptResponse") or sig.get("response") or ""
                step.result = (sr if isinstance(sr, str) else json.dumps(sr))[:2000]
                step.console = (
                    sig.get("console") or sig.get("consoleLog") or ""
                )[:1000]
            else:
                step.result = str(sig)[:2000]
            step.error_type = classify_script_response(step.result, step.console)
        elif tp == "Thoughts":
            step.content = t.get("content") or ""
            step.thought_segments = split_thoughts(step.content)
        elif tp == "Assistant":
            sig = t.get("signal") or {}
            if isinstance(sig, dict):
                step.text = (
                    sig.get("assistantMessage")
                    or sig.get("message")
                    or sig.get("text")
                    or json.dumps(sig)[:2000]
                )
            else:
                step.text = str(sig)[:2000]
        steps.append(step)
    return steps


# ---------------------------------------------------------------------------
# Signal computation
# ---------------------------------------------------------------------------

def _script_similarity(scripts: list[str], threshold: float = 0.8) -> list[list[int]]:
    """Group script indices that have >threshold text similarity."""
    n = len(scripts)
    if n < 2:
        return []
    visited = set()
    groups: list[list[int]] = []
    for i in range(n):
        if i in visited:
            continue
        group = [i]
        for j in range(i + 1, n):
            if j in visited:
                continue
            ratio = SequenceMatcher(
                None,
                scripts[i][:500],
                scripts[j][:500],
            ).ratio()
            if ratio >= threshold:
                group.append(j)
                visited.add(j)
        if len(group) >= 3:
            groups.append(group)
            visited.update(group)
    return groups


_SUCCESS_MARKERS = re.compile(r"✅|Done!|Successfully|completed successfully", re.IGNORECASE)


def compute_signals(case: ParsedEvalCase) -> None:
    """Populate computed signal fields on a ParsedEvalCase."""
    case.step_count = len(case.steps)

    scripts: list[str] = []
    errors: list[str] = []
    error_positions: list[int] = []
    all_thought_segs: list[tuple[str, str]] = []
    thoughts_chars = 0

    for s in case.steps:
        if s.step_type == "ScriptExecution":
            case.script_exec_count += 1
            scripts.append(s.script_full)
        elif s.step_type == "ScriptResponse":
            case.script_response_count += 1
            if s.error_type:
                errors.append(s.error_type)
                error_positions.append(s.step_index)
        elif s.step_type == "Thoughts":
            thoughts_chars += len(s.content)
            all_thought_segs.extend(s.thought_segments)
        elif s.step_type == "Assistant":
            if not case.assistant_response:
                case.assistant_response = s.text

    case.error_count = len(errors)
    case.error_types = errors
    case.error_positions = error_positions
    case.thoughts_total_chars = thoughts_chars
    case.thoughts_segments = all_thought_segs
    case.script_similarity_groups = _script_similarity(scripts)

    # retry count from diagnostics
    if case.retry_diagnostics:
        case.retry_count = len(case.retry_diagnostics)

    # detect success claim in final assistant response
    final_asst = ""
    for s in reversed(case.steps):
        if s.step_type == "Assistant":
            final_asst = s.text
            break
    if not final_asst:
        # fall back to chatLog
        for entry in case.chat_log:
            if entry.get("type") == "finalText":
                final_asst = entry.get("content", "")
    case.has_success_claim = bool(_SUCCESS_MARKERS.search(final_asst))
    if final_asst and not case.assistant_response:
        case.assistant_response = final_asst


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _determine_pass(raw_case: dict) -> bool:
    """Determine pass/fail from evaluationResults.

    evaluationSuccess means the eval pipeline ran — not that the case passed.
    The actual grading result is a Boolean entry in evaluationResults.
    Different evaluator types use different names:
      - 'grader' (LLM judge, cell comparison)
      - 'officejs_assertion_outcome' (Office.js deterministic)
      - 'vllm_assertion_outcome' (VLLM vision judge)
    """
    boolean_result_names = {
        "grader",
        "officejs_assertion_outcome",
        "vllm_assertion_outcome",
    }
    for er in raw_case.get("evaluationResults", []):
        if er.get("kind") == "Boolean" and er.get("name") in boolean_result_names:
            return bool(er.get("value", True))
    return raw_case.get("evaluationSuccess", True)


def parse_report_file(
    json_path: str | Path,
    batch_name: Optional[str] = None,
    *,
    failures_only: bool = False,
) -> list[ParsedEvalCase]:
    """Parse a single evalVNext playOutput report JSON and return all cases."""
    json_path = Path(json_path)
    if not json_path.exists():
        raise FileNotFoundError(f"Report not found: {json_path}")

    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)

    raw_cases = data.get("evalCases", data if isinstance(data, list) else [])

    if batch_name is None:
        batch_name = _infer_batch_name(json_path)

    cases: list[ParsedEvalCase] = []
    for rc in raw_cases:
        passed = _determine_pass(rc)
        if failures_only and passed:
            continue

        traj = rc.get("collectedArtifacts", {}).get("trajectory", [])
        steps = _extract_steps(traj)

        timings = rc.get("collectedArtifacts", {}).get("executionTimings", {})
        exec_time = timings.get("workflowExecutionInSec", 0.0)

        chat_entries = []
        chat_log = rc.get("chatLog", {})
        if isinstance(chat_log, dict):
            chat_entries = chat_log.get("copilotResponse", [])
        elif isinstance(chat_log, list):
            chat_entries = chat_log

        case = ParsedEvalCase(
            query_index=str(rc.get("query_index", rc.get("id", ""))),
            batch=batch_name,
            query_text=rc.get("query", ""),
            workbook=rc.get("workbook", ""),
            tags=rc.get("tags", []),
            steps=steps,
            passed=passed,
            response_status=rc.get("responseStatus", ""),
            execution_time_sec=exec_time,
            evaluation_results=rc.get("evaluationResults", []),
            retry_diagnostics=rc.get("collectedArtifacts", {}).get("retryDiagnostics"),
            chat_log=chat_entries,
        )
        compute_signals(case)
        cases.append(case)

    return cases


def parse_batch_folder(
    folder_path: str | Path,
    *,
    failures_only: bool = False,
) -> list[ParsedEvalCase]:
    """Auto-discover all playOutput report JSONs in a batch folder."""
    folder = Path(folder_path)
    eval_dir = folder / "evalReport"
    if not eval_dir.exists():
        eval_dir = folder

    all_cases: list[ParsedEvalCase] = []
    for json_file in sorted(eval_dir.glob("playOutput_*-report.json")):
        batch = _infer_batch_name(json_file)
        cases = parse_report_file(json_file, batch, failures_only=failures_only)
        all_cases.extend(cases)
    return all_cases


def auto_discover_batches(
    base_dir: str | Path,
    *,
    failures_only: bool = False,
) -> dict[str, list[ParsedEvalCase]]:
    """Walk base_dir for RegressionBench_* folders and parse all batches."""
    base = Path(base_dir)
    result: dict[str, list[ParsedEvalCase]] = {}

    for folder in sorted(base.iterdir()):
        if not folder.is_dir():
            continue
        name_lower = folder.name.lower()
        if not (folder.name.startswith("RegressionBench_") or "regressionbench" in name_lower):
            continue
        cases = parse_batch_folder(folder, failures_only=failures_only)
        for c in cases:
            result.setdefault(c.batch, []).append(c)
    return result


def parse_single_json(json_path: str | Path) -> dict[str, list[ParsedEvalCase]]:
    """Load a single playOutput JSON file directly (no folder structure needed)."""
    json_path = Path(json_path)
    cases = parse_report_file(json_path)
    result: dict[str, list[ParsedEvalCase]] = {}
    for c in cases:
        result.setdefault(c.batch, []).append(c)
    return result


def _infer_batch_name(json_path: Path) -> str:
    """Infer batch name from filename like playOutput_regression-bench-formula-hard-report.json."""
    name = json_path.stem
    name = name.replace("playOutput_", "").replace("-report", "")
    name = name.replace("regression-bench-", "")
    return name or "unknown"


# ---------------------------------------------------------------------------
# Legacy export (backwards compat with _traj_full.json format)
# ---------------------------------------------------------------------------

def export_legacy_format(
    cases_by_batch: dict[str, list[ParsedEvalCase]],
    output_path: str | Path,
) -> None:
    out: dict[str, dict[str, dict]] = {}
    for batch, cases in cases_by_batch.items():
        out[batch] = {}
        for c in cases:
            out[batch][c.query_index] = c.to_legacy_dict()
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Parse evalVNext trajectory data")
    parser.add_argument("path", help="Report JSON file, batch folder, or base dir with RegressionBench_* folders")
    parser.add_argument("--failures-only", action="store_true")
    parser.add_argument("--export-legacy", help="Export to _traj_full.json format")
    parser.add_argument("--summary", action="store_true", help="Print per-case summary")
    args = parser.parse_args()

    p = Path(args.path)
    if p.is_file():
        all_cases = parse_report_file(p, failures_only=args.failures_only)
        batches = {}
        for c in all_cases:
            batches.setdefault(c.batch, []).append(c)
    elif p.is_dir():
        # check if this is a single batch folder or a base dir
        has_regression = any(d.name.startswith("RegressionBench_") for d in p.iterdir() if d.is_dir())
        if has_regression:
            batches = auto_discover_batches(p, failures_only=args.failures_only)
        else:
            all_cases = parse_batch_folder(p, failures_only=args.failures_only)
            batches = {}
            for c in all_cases:
                batches.setdefault(c.batch, []).append(c)
    else:
        print(f"Path not found: {p}")
        sys.exit(1)

    total = sum(len(v) for v in batches.values())
    failed = sum(1 for v in batches.values() for c in v if not c.passed)
    print(f"Parsed {total} cases across {len(batches)} batches ({failed} failures)")

    if args.summary:
        for batch, cases in batches.items():
            for c in cases:
                status = "PASS" if c.passed else "FAIL"
                print(
                    f"  {batch} Q{c.query_index}: {status} | "
                    f"steps={c.step_count} SE={c.script_exec_count} "
                    f"SR={c.script_response_count} errors={c.error_count} "
                    f"thoughts={c.thoughts_total_chars}ch "
                    f"sim_groups={len(c.script_similarity_groups)} "
                    f"success_claim={c.has_success_claim}"
                )

    if args.export_legacy:
        export_legacy_format(batches, args.export_legacy)
        print(f"Exported legacy format to {args.export_legacy}")
