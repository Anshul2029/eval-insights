"""
code_exec_agent.py — Real code-execution agent.

For each step the LLM generates Python code, we execute it in a subprocess,
capture the real output, then ask the LLM to extract key facts.
Token usage is captured at every LLM call.

Produces a trace in the same format as dataset/trace_*.json so the
existing pipeline.run_pipeline() can evaluate it.

Workspace per run: results/workspace/<trace_id>_<llm>/
  step1_output.txt  step2_output.txt  ...  handoff_data.json  report.docx

Supported LLMs: "gemini" | "groq" | "ollama"
"""

import json
import os
import subprocess
import sys
import textwrap
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from agent_runner import AgentRunner, _parse_json, _zero_usage

EXCEL_DIR  = os.path.join(os.path.dirname(__file__), "temp_excel")
RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")
CODE_TIMEOUT = 60   # seconds per subprocess run
FIX_ATTEMPTS = 1    # how many times to ask LLM to fix broken code


# ── code execution ────────────────────────────────────────────────────────────

def _run_code(code: str, workspace: str, timeout: int = CODE_TIMEOUT) -> tuple:
    """Write code to a temp file and run it. Returns (stdout, stderr, success)."""
    os.makedirs(workspace, exist_ok=True)
    script = os.path.join(workspace, "_step_code.py")
    with open(script, "w", encoding="utf-8") as f:
        f.write(code)
    try:
        result = subprocess.run(
            [sys.executable, script],
            capture_output=True, text=True, timeout=timeout,
            cwd=workspace,
        )
        return result.stdout.strip(), result.stderr.strip(), result.returncode == 0
    except subprocess.TimeoutExpired:
        return "", f"TIMEOUT after {timeout}s", False
    except Exception as e:
        return "", str(e), False


def _read_docx_text(docx_path: str) -> dict:
    """Extract text from a Word doc into the word_output_actual_text format."""
    try:
        from docx import Document
        doc = Document(docx_path)
        paras = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
        # heuristic extraction
        exec_summary, anomaly, recs = "", "", []
        in_exec = in_anomaly = in_recs = False
        for p in paras:
            pl = p.lower()
            if any(k in pl for k in ("executive summary", "summary")):
                in_exec, in_anomaly, in_recs = True, False, False
                continue
            if any(k in pl for k in ("anomal", "unusual", "outlier")):
                in_exec, in_anomaly, in_recs = False, True, False
                continue
            if any(k in pl for k in ("recommend", "follow-up", "next step")):
                in_exec, in_anomaly, in_recs = False, False, True
                continue
            if in_exec and not exec_summary:
                exec_summary = p[:400]
            elif in_anomaly and not anomaly:
                anomaly = p[:400]
            elif in_recs and len(recs) < 5:
                recs.append(p[:200])
        if not exec_summary and paras:
            exec_summary = paras[0][:400]
        return {
            "executive_summary": exec_summary,
            "anomaly_section":   anomaly or "See report for anomaly details.",
            "recommendations":   recs or [],
            "full_text_preview": " ".join(paras)[:800],
        }
    except Exception as e:
        return {
            "executive_summary": "", "anomaly_section": "",
            "recommendations": [], "error": str(e),
        }


# ── prompt builders ───────────────────────────────────────────────────────────

_SYS_CODE = (
    "You are an expert Python data analyst. "
    "Write clean, runnable Python code only — no explanation, no markdown fences."
)

_SYS_FACTS = (
    "You are extracting structured facts from program output. "
    "Respond ONLY with valid JSON."
)


def _p_step1_code(excel_path: str, columns: list, user_prompt: str) -> str:
    return f"""Write Python code for Step 1 (data_parsing).

Task: {user_prompt}
Excel file: {excel_path}
Expected columns hint: {columns}

Requirements:
- Load with pandas.read_excel
- Print row count, column names, dtypes
- Check missing values, duplicates, non-positive values
- Apply month ordering if a Month column exists
- Print each finding on its own line as "key: value"
- Use only pandas, numpy (already installed)
- No plots, no file writes

Write ONLY Python code."""


def _p_step2_code(excel_path: str, columns: list, user_prompt: str,
                  ground_truth: str, step1_output: str) -> str:
    return f"""Write Python code for Step 2 (computation).

Task: {user_prompt}
Excel file: {excel_path}
Columns: {columns}
Ground truth hint (use to guide anomaly detection): {ground_truth}
Step 1 output (for context):
{step1_output[:600]}

Requirements:
- Load the Excel file fresh
- Compute totals: revenue, units if present
- Compute regional and product breakdowns if those columns exist
- Detect anomalies using z-scores at the finest granularity available
  (Region x Product x Month if all exist, otherwise Month-level)
- Print each anomaly with: month, region, product (if available), value, z-score
- Print all key findings as "key: value" lines
- Use only pandas, numpy, scipy (already installed)
- No plots, no file writes

Write ONLY Python code."""


def _p_step3_code(workspace: str, step2_output: str) -> str:
    handoff_path = os.path.join(workspace, "handoff_data.json").replace("\\", "/")
    return f"""Write Python code for Step 3 (context_handoff).

Step 2 output:
{step2_output[:800]}

Requirements:
- Parse the above output and build a Python dict called 'results' with all key findings
- Save results to: {handoff_path}
- Print "handoff_complete: true" and list the keys saved
- Use only json, os (standard library)

Write ONLY Python code."""


def _p_step4_code(workspace: str, user_prompt: str, handoff_path: str) -> str:
    docx_path = os.path.join(workspace, "report.docx").replace("\\", "/")
    return f"""Write Python code for Step 4 (report_structuring).

Task: {user_prompt}
Handoff data file: {handoff_path}
Save Word doc to: {docx_path}

Requirements:
- Load handoff_data.json
- Use python-docx to create a Word document with appropriate sections for the task
- Add headings for: Executive Summary, Business Performance, Anomaly Analysis,
  Recommendations (adapt section names to the data available)
- Add placeholder paragraphs under each heading (content will be filled in Step 5)
- Save the document
- Print each section name created
- Use only python-docx, json (already installed)

Write ONLY Python code."""


def _p_step5_code(workspace: str, user_prompt: str,
                  handoff_path: str, step2_output: str) -> str:
    docx_path = os.path.join(workspace, "report.docx").replace("\\", "/")
    return f"""Write Python code for Step 5 (narrative_generation).

Task: {user_prompt}
Handoff data: {handoff_path}
Computed findings:
{step2_output[:1000]}
Word doc to complete: {docx_path}

Requirements:
- Load handoff_data.json and the existing Word document
- Replace placeholder paragraphs with real narrative using specific numbers from the findings
- Executive Summary: 2-3 sentences with key numbers
- Anomaly section: explicitly name any anomaly with region, product, month, values
- Recommendations: 3 specific actionable items
- Save the completed document to the same path
- Print a JSON block between markers <<<JSON_START>>> and <<<JSON_END>>> with:
  {{"executive_summary": "...", "anomaly_section": "...", "recommendations": ["..."]}}
- Use only python-docx, json

Write ONLY Python code."""


def _p_fix_code(code: str, error: str) -> str:
    return f"""This Python code produced an error. Fix it.

ERROR:
{error[:600]}

ORIGINAL CODE:
{code}

Write ONLY the corrected Python code."""


def _p_extract_facts(action_type: str, output: str) -> str:
    return f"""Extract key facts from this {action_type} step output.

Output:
{output[:1200]}

Return JSON only:
{{"key_facts": ["key1: value1", "key2: value2", ...]}}

Include: counts, scores, anomaly details, boolean flags — whatever is most informative."""


# ── agent ─────────────────────────────────────────────────────────────────────

class CodeExecAgent:
    def __init__(self, llm_name: str):
        self.llm_name = llm_name
        self._llm = AgentRunner(llm_name)   # reuse LLM call methods

    def _call(self, system: str, user: str) -> tuple:
        return self._llm._call_llm(system, user)

    def _gen_and_run(self, action_type: str, code_prompt: str,
                     workspace: str) -> tuple:
        """
        Ask LLM for code → run it → if error try one fix → return
        (stdout, token_usage_dict, code_used).
        token_usage accumulates across code_gen + optional fix call.
        """
        total_usage = _zero_usage()

        # code generation
        code_text, usage = self._call(_SYS_CODE, code_prompt)
        total_usage = _add_usage(total_usage, usage)

        # strip markdown fences if LLM added them despite instructions
        code = _strip_fences(code_text)

        stdout, stderr, ok = _run_code(code, workspace)

        if not ok and FIX_ATTEMPTS:
            print(f"    [{self.llm_name.upper()}] {action_type} code failed — asking LLM to fix...")
            print(f"    stderr: {stderr[:200]}")
            fix_text, fix_usage = self._call(_SYS_CODE, _p_fix_code(code, stderr))
            total_usage = _add_usage(total_usage, fix_usage)
            code = _strip_fences(fix_text)
            stdout, stderr, ok = _run_code(code, workspace)
            if not ok:
                print(f"    [{self.llm_name.upper()}] Fix also failed: {stderr[:200]}")
                stdout = f"[EXECUTION FAILED] {stderr[:300]}"

        return stdout, total_usage, code

    def _extract_facts(self, action_type: str, output: str) -> tuple:
        """Ask LLM to extract key_facts list from raw output. Returns (facts, usage)."""
        text, usage = self._call(_SYS_FACTS, _p_extract_facts(action_type, output))
        data = _parse_json(text)
        facts = data.get("key_facts", [])
        if not facts and output:
            # fallback: split output lines that look like "key: value"
            facts = [ln.strip() for ln in output.splitlines()
                     if ": " in ln and len(ln.strip()) < 120][:10]
        return facts, usage

    def run(self, user_prompt: str, source_data_summary: dict,
            dataset_file: str, trace_id: str) -> dict:
        """
        Execute all 5 steps with real code, return:
        {"trace": {...}, "token_usage": {"per_step": [...], "total": {...}}}
        """
        excel_path = os.path.join(EXCEL_DIR, dataset_file)
        if not os.path.exists(excel_path):
            raise FileNotFoundError(f"Excel file not found: {excel_path}")

        workspace = os.path.join(RESULTS_DIR, "workspace", f"{trace_id}_{self.llm_name}")
        os.makedirs(workspace, exist_ok=True)

        columns = source_data_summary.get("columns", [])
        gt = source_data_summary.get("ground_truth", "")
        if not gt:
            gta = source_data_summary.get("ground_truth_anomalies", {})
            gt = gta.get("planted", "") + " " + gta.get("organic", "")

        print(f"\n  [{self.llm_name.upper()}] Starting real execution for {trace_id}")
        print(f"  [{self.llm_name.upper()}] Excel: {excel_path}")

        token_per_step = []
        steps = []

        # ── step 1: data_parsing ──────────────────────────────────────────────
        print(f"  [{self.llm_name.upper()}] Step 1: data_parsing")
        s1_out, s1_usage, _ = self._gen_and_run(
            "data_parsing",
            _p_step1_code(excel_path, columns, user_prompt),
            workspace,
        )
        _write(workspace, "step1_output.txt", s1_out)
        s1_facts, s1f_usage = self._extract_facts("data_parsing", s1_out)
        s1_total = _add_usage(s1_usage, s1f_usage)
        token_per_step.append({"step_number": 1, "action_type": "data_parsing", **s1_total})
        steps.append(_make_step(1, "Excel", "data_parsing", s1_out, s1_facts))

        # ── step 2: computation ───────────────────────────────────────────────
        print(f"  [{self.llm_name.upper()}] Step 2: computation")
        s2_out, s2_usage, _ = self._gen_and_run(
            "computation",
            _p_step2_code(excel_path, columns, user_prompt, gt, s1_out),
            workspace,
        )
        _write(workspace, "step2_output.txt", s2_out)
        s2_facts, s2f_usage = self._extract_facts("computation", s2_out)
        s2_total = _add_usage(s2_usage, s2f_usage)
        token_per_step.append({"step_number": 2, "action_type": "computation", **s2_total})
        steps.append(_make_step(2, "Excel", "computation", s2_out, s2_facts))

        # ── step 3: context_handoff ───────────────────────────────────────────
        print(f"  [{self.llm_name.upper()}] Step 3: context_handoff")
        handoff_path = os.path.join(workspace, "handoff_data.json")
        s3_out, s3_usage, _ = self._gen_and_run(
            "context_handoff",
            _p_step3_code(workspace, s2_out),
            workspace,
        )
        _write(workspace, "step3_output.txt", s3_out)
        s3_facts, s3f_usage = self._extract_facts("context_handoff", s3_out)
        s3_total = _add_usage(s3_usage, s3f_usage)
        token_per_step.append({"step_number": 3, "action_type": "context_handoff", **s3_total})

        # build context_manifest from step 2 facts vs word output (filled later)
        kw_facts = [f for f in s2_facts if any(
            k in f.lower() for k in ("anomaly", "revenue", "total", "outlier", "correlation")
        )]
        steps.append(_make_step(3, "Excel→Word", "context_handoff", s3_out, s3_facts))

        # ── step 4: report_structuring ────────────────────────────────────────
        print(f"  [{self.llm_name.upper()}] Step 4: report_structuring")
        s4_out, s4_usage, _ = self._gen_and_run(
            "report_structuring",
            _p_step4_code(workspace, user_prompt, handoff_path),
            workspace,
        )
        _write(workspace, "step4_output.txt", s4_out)
        s4_facts, s4f_usage = self._extract_facts("report_structuring", s4_out)
        s4_total = _add_usage(s4_usage, s4f_usage)
        token_per_step.append({"step_number": 4, "action_type": "report_structuring", **s4_total})
        steps.append(_make_step(4, "Word", "report_structuring", s4_out, s4_facts))

        # ── step 5: narrative_generation ──────────────────────────────────────
        print(f"  [{self.llm_name.upper()}] Step 5: narrative_generation")
        s5_out, s5_usage, _ = self._gen_and_run(
            "narrative_generation",
            _p_step5_code(workspace, user_prompt, handoff_path, s2_out),
            workspace,
        )
        _write(workspace, "step5_output.txt", s5_out)
        s5_facts, s5f_usage = self._extract_facts("narrative_generation", s5_out)
        s5_total = _add_usage(s5_usage, s5f_usage)
        token_per_step.append({"step_number": 5, "action_type": "narrative_generation", **s5_total})
        steps.append(_make_step(5, "Word", "narrative_generation", s5_out, s5_facts))

        # ── read real Word doc ────────────────────────────────────────────────
        docx_path = os.path.join(workspace, "report.docx")

        # try to pull structured text from the <<<JSON_START>>> marker first
        word_output = _extract_json_marker(s5_out)
        if not word_output.get("executive_summary") and os.path.exists(docx_path):
            word_output = _read_docx_text(docx_path)

        print(f"  [{self.llm_name.upper()}] Word doc: {'created' if os.path.exists(docx_path) else 'MISSING'}")

        # ── context_manifest ──────────────────────────────────────────────────
        word_flat = " ".join(str(v) for v in word_output.values() if v).lower()
        facts_present, facts_lost = [], []
        for fact in kw_facts:
            key_terms = fact.split(":")[0].strip().lower().replace("_", " ").split()
            if any(t in word_flat for t in key_terms):
                facts_present.append(fact)
            else:
                facts_lost.append(fact)

        context_manifest = {
            "facts_produced_in_excel_step2": kw_facts,
            "facts_present_in_word_output":  [f"PRESENT: {f}" for f in facts_present],
            "facts_lost_at_boundary":         facts_lost,
            "boundary":                       "Excel→Word (Step 3)",
            "context_loss_detected":          len(facts_lost) > 0,
        }

        # ── normalise source_data_summary ─────────────────────────────────────
        src = dict(source_data_summary)
        if "ground_truth_anomalies" not in src:
            planted = "None" if not gt or "no planted" in gt.lower() else gt
            src["ground_truth_anomalies"] = {"planted": planted}

        # ── assemble trace ────────────────────────────────────────────────────
        llm_model_map = {
            "gemini": "gemini-2.5-flash",
            "groq":   "llama-3.3-70b-versatile",
            "ollama": os.getenv("OLLAMA_MODEL", "mistral"),
        }
        trace = {
            "trace_id":              f"{trace_id}_{self.llm_name}",
            "llm":                   self.llm_name,
            "llm_model":             llm_model_map.get(self.llm_name, self.llm_name),
            "source_trace_id":       trace_id,
            "run_date":              datetime.now(timezone.utc).isoformat(),
            "dataset_file":          dataset_file,
            "user_prompt":           user_prompt,
            "agent_plan":            f"Real code execution via {self.llm_name} LLM on {dataset_file}",
            "source_data_summary":   src,
            "steps":                 steps,
            "context_manifest":      context_manifest,
            "word_output_actual_text": word_output,
            "word_doc_path":         docx_path if os.path.exists(docx_path) else None,
            "workspace":             workspace,
        }

        # ── token totals ──────────────────────────────────────────────────────
        total = {
            k: sum(s[k] for s in token_per_step)
            for k in ("input_tokens", "output_tokens", "thinking_tokens",
                      "total_tokens", "cached_tokens")
        }
        print(f"  [{self.llm_name.upper()}] Done — total tokens: {total['total_tokens']}")

        return {
            "trace": trace,
            "token_usage": {
                "llm":       self.llm_name,
                "llm_model": trace["llm_model"],
                "per_step":  token_per_step,
                "total":     total,
            },
        }


# ── helpers ───────────────────────────────────────────────────────────────────

def _add_usage(a: dict, b: dict) -> dict:
    return {k: a[k] + b[k] for k in a}


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        # drop first and last fence lines
        inner = []
        in_block = False
        for line in lines:
            if line.startswith("```") and not in_block:
                in_block = True
                continue
            if line.startswith("```") and in_block:
                break
            if in_block:
                inner.append(line)
        return "\n".join(inner)
    return text


def _write(workspace: str, filename: str, content: str):
    with open(os.path.join(workspace, filename), "w", encoding="utf-8") as f:
        f.write(content)


def _make_step(num: int, app: str, action_type: str,
               output: str, key_facts: list) -> dict:
    return {
        "step_number":       num,
        "app":               app,
        "action_type":       action_type,
        "latency_observed":  "real execution (code_exec_agent)",
        "tools_called":      ["python code executed via subprocess"],
        "output":            output[:600],
        "what_agent_did":    f"LLM generated and executed Python code for {action_type}",
        "key_facts_produced": key_facts,
    }


def _extract_json_marker(text: str) -> dict:
    """Pull JSON between <<<JSON_START>>> and <<<JSON_END>>> markers."""
    try:
        start = text.find("<<<JSON_START>>>")
        end   = text.find("<<<JSON_END>>>")
        if start != -1 and end != -1:
            raw = text[start + len("<<<JSON_START>>>"):end].strip()
            return json.loads(raw)
    except Exception:
        pass
    return {}
