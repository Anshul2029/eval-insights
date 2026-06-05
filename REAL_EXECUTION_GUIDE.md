# Real Execution Agent — Quick Reference

## What Changed

**New files (safe—originals untouched):**
- `agent_runner_real.py` — Real execution version
- `run_comparison_real.py` — Real comparison runner
- `token_rationale_viewer.py` — Display token rationale

**Original files (unchanged):**
- `agent_runner.py` — Still there (simulated)
- `run_comparison.py` — Still there (simulated)

---

## Key Differences

| Aspect | Simulated | Real |
|--------|-----------|------|
| **Data Loading** | LLM describes it | Actually loads Excel |
| **Analysis** | LLM claims results | Pandas/NumPy compute real stats |
| **Anomalies** | LLM invents them | Z-score detection on actual data |
| **Word Doc** | JSON placeholder | Actual .docx file generated |
| **Token Count** | From LLM calls with simulated prompts | From LLM calls with real context |

---

## How to Use

### 1. Run Real Comparison
```bash
python run_comparison_real.py trace_002
python run_comparison_real.py trace_002 trace_004
```

### 2. View Token Rationale
The runner automatically prints:
- **Why** each step used tokens
- What was processed at each step
- Which LLM called at which point

Output includes:
```
Step 2 (computation): 2500 tokens
  Why: LLM interpretation of real statistical analysis
  Computation: 10 columns analyzed, 8 anomalies
```

### 3. Access Generated Word Documents
```
temp_real_reports/report_trace_002_groq.docx
temp_real_reports/report_trace_002_qwen.docx
```

---

## Token Rationale Explained

Each step's token count is broken down by:

**Step 0 (Planning)**
- Reason: Task understanding from user prompt + dataset info

**Step 1 (Data Parsing)**
- Reason: LLM validates actual row/column counts
- Data: "1000 rows × 10 cols analyzed"

**Step 2 (Computation)**
- Reason: LLM interprets real statistical analysis results
- Computation: "10 columns analyzed, 8 anomalies detected"

**Step 3 (Context Handoff)**
- Reason: LLM prepares facts for Word document
- Facts: Number of facts passed

**Step 4 (Report Structuring)**
- Reason: LLM designs document sections
- Sections: Number and names of sections

**Step 5 (Narrative Generation)**
- Reason: LLM writes actual narrative content
- Document: Whether .docx was generated

---

## How Tokens Flow

```
User Prompt + Dataset Info
         ↓
Step 1: Load Excel → Get row/col counts → LLM validates (tokens)
         ↓
Step 2: Compute stats/anomalies → LLM interprets results (tokens)
         ↓
Step 3: Prepare context → LLM finalizes for Word (tokens)
         ↓
Step 4: Structure document → LLM designs sections (tokens)
         ↓
Step 5: Generate narrative → LLM writes content → Actual Word doc (tokens)
         ↓
Result: Real .docx + Token counts + Rationale
```

---

## Comparison: Real vs Simulated Tokens

**Same LLM, different workflows:**

- **Simulated:** ~3k tokens (LLM describes what it would do)
- **Real:** ~5-8k tokens (LLM works with actual data context)

**Why higher:**
- Prompts include real data summaries (more context)
- LLM must interpret actual results (more processing)
- More nuanced task (reduces repetition)

---

## Reverting to Simulated

Both versions coexist. Use simulated when:
- Speed matters more than realism
- Testing pipeline logic
- Comparing LLM descriptions

Use real when:
- Measuring true token efficiency
- Comparing LLM performance on actual tasks
- Generating real reports

---

## Output Files

### Comparison Report
```
results/comparison_trace_002_real.json
```

Includes:
- `token_usage` (per_step + total)
- `token_rationale` (why each step used tokens)
- `trajectory_score`, `plan_score`, etc.
- `trace` (full execution trace)

### Generated Documents
```
temp_real_reports/report_trace_002_groq.docx
temp_real_reports/report_trace_002_qwen.docx
```

Real Word documents with:
- Executive Summary
- Analysis Findings
- Detected Anomalies
- Recommendations

---

## Dependencies Required

```
pip install pandas numpy python-docx groq google-generativeai
```

(Most likely already installed; check requirements.txt)

---

## Troubleshooting

**"ModuleNotFoundError: No module named 'pandas'"**
- Run: `pip install pandas numpy`

**"No module named 'docx'"**
- Run: `pip install python-docx`

**Dataset file not found**
- Check dataset path in trace JSON
- Ensure Excel file exists

**LLM API errors**
- Check `.env` credentials
- Verify API keys are set

---

## Next Steps

1. Run: `python run_comparison_real.py trace_002`
2. Check `temp_real_reports/` for generated .docx files
3. Review token rationale in console output
4. Compare with `python run_comparison.py trace_002` (simulated)
5. See token difference and understand why

---
