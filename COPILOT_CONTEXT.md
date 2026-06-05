# Copilot Eval — Complete Project Context

This document is a full handoff context for continuing work on this project.
Working directory: `C:\Users\t-ashende\Documents\evaluator`

---

## What This Project Is

A **trajectory evaluator** for a Microsoft Copilot agent that processes Excel files and writes Word reports. The agent does a 5-step workflow: parse data → compute KPIs → hand off context to Word → structure the report → write the narrative. The evaluator scores each step with an LLM-generated rubric, detects failure root causes, and shows results in a React dashboard.

**Current use case:** Sales data analysis. Agent reads an `.xlsx` file, finds anomalies (e.g., a 41% revenue drop in a specific region/product/month), and writes a Word leadership report. Ground truth is "planted" — a known anomaly is embedded in the dataset and the agent must catch it.

---

## Tech Stack

| Layer | Tech |
|---|---|
| Frontend | React 18 + Vite 5, plain CSS (no Tailwind), axios |
| Backend API | Flask + flask-cors, port **5000** |
| Vite dev server | port **3000** (config says 3000, was running on 3001 at some point — check) |
| Python modules | pipeline.py, mock_llm.py, rubric_generator.py, grader.py, plan_evaluator.py, context_checker.py |
| LLM | Anthropic + OpenAI — **BOTH OVER QUOTA**. All evals run in `mock=True` mode via `mock_llm.py` |
| Fonts | Inter (UI), JetBrains Mono (monospace/code) — loaded via Google Fonts in `index.html` |
| Design tokens | Dark theme CSS variables in `:root` — `--bg`, `--bg2..4`, `--border`, `--border2`, `--text`, `--text2`, `--muted`, `--muted2`, `--green`, `--amber`, `--red`, `--blue`, `--purple`, and `-dim` / `-bd` variants |

---

## Directory Structure

```
evaluator/
├── api/
│   ├── __init__.py
│   └── app.py                  ← Flask REST API (3 endpoints)
├── dataset/
│   ├── trace_001.json .. trace_015.json   ← raw traces to evaluate
│   ├── trace_fail_001.json                ← uploaded failing trace (partial — wrong schema)
│   ├── trace_fail_002.json                ← correct failing trace (deterministic fail)
│   └── prev-traces/            ← older versions
├── results/
│   └── all_results.json        ← pipeline output, read by Flask API
├── temp_excel/                 ← xlsx datasets (01-10)
├── temp_raw_traces/            ← raw text traces (rawtrace1-7.txt)
├── pipeline.py                 ← orchestrates full eval of one trace
├── run_eval.py                 ← CLI entry point (batch eval)
├── mock_llm.py                 ← deterministic rubric + grading (no API calls)
├── rubric_generator.py         ← LLM rubric generation (over quota)
├── grader.py                   ← LLM grading (over quota)
├── plan_evaluator.py           ← plan-level scoring
├── context_checker.py          ← Excel→Word context loss detection
├── requirements.txt
├── .env                        ← ANTHROPIC_API_KEY, OPENAI_API_KEY
└── ui/
    ├── vite.config.js          ← proxy: /traces, /trace, /evaluate, /health → :5000
    ├── src/
    │   ├── App.jsx             ← root component, all state
    │   ├── App.css             ← ALL styles (single file, ~570 lines)
    │   ├── main.jsx
    │   └── components/
    │       ├── Sidebar.jsx         ← trace list + "Evaluate Trace" upload button
    │       ├── Overview.jsx        ← landing stats + table (shown before any trace selected)
    │       ├── TraceHeader.jsx     ← trace ID, prompt block, score ring, sub-scores
    │       ├── PlanBreakdown.jsx   ← 5 plan metrics with expandable "What is this?" descriptions
    │       ├── StepTimeline.jsx    ← horizontal step cards with criterion dots
    │       ├── StepDetail.jsx      ← tabbed: Step Evaluation | Agent Trajectory
    │       ├── FailureCard.jsx     ← root cause + contaminated steps
    │       └── ContextManifest.jsx ← Excel→Word fact tracking
```

---

## How to Run

```bash
# Terminal 1 — Flask API
cd C:\Users\t-ashende\Documents\evaluator\api
python app.py
# → http://localhost:5000

# Terminal 2 — Vite dev server
cd C:\Users\t-ashende\Documents\evaluator\ui
npm run dev
# → http://localhost:3000

# CLI eval (processes all traces in dataset/, saves to results/all_results.json)
cd C:\Users\t-ashende\Documents\evaluator
python run_eval.py --mock
```

---

## Flask API — `api/app.py`

Three endpoints. All read/write `results/all_results.json`.

### `GET /traces`
Returns summary list for sidebar. Each item:
```json
{
  "trace_id": "trace_001",
  "dataset_file": "01_clean_baseline.xlsx",
  "user_prompt": "...",
  "trajectory_score": 0.9119,
  "plan_score": 0.84,
  "avg_step_score": 0.94,
  "colour": "green",
  "failure_transition_step": null,
  "failure_type": null,
  "evaluated_at": "2026-04-28T...",
  "error": null
}
```

### `GET /trace/<trace_id>`
Returns full result object (see Data Schemas section). Also adds `colour` field to each step_result.

### `POST /evaluate`
Accepts a raw trace JSON as multipart file upload (`file` field) OR raw JSON body.
- Validates `trace_id` and `steps` fields exist
- Saves raw trace to `dataset/<trace_id>.json`
- Runs `pipeline.run_pipeline(trace, verbose=False, mock=True)`
- Merges result into `results/all_results.json`
- Returns the full result with 201

**Important:** The Vite proxy must include `/evaluate` → `:5000`. It already does as of latest config.

---

## Python Pipeline — `pipeline.py`

`run_pipeline(trace, verbose, mock)` → result dict

Flow:
1. **Plan evaluation** — `plan_evaluator.evaluate_plan(trace)` or mock path
2. **Per-step loop** — for each step: `generate_rubric` → `grade_step`
3. **Context check** — `context_checker.check_context(trace)`
4. **Aggregate** — `trajectory_score = plan_score × 0.30 + avg_step_score × 0.70`
5. **Failure attribution** — first failing step is root cause; subsequent failing steps are contaminated

Failure type mapping (set in `grade.failure_type`):
- `data_parsing` → `parsing_error`
- `computation` → `computation_error`
- `context_handoff` → `context_loss`
- `report_structuring` / `narrative_generation` → `inherited`

---

## Mock LLM — `mock_llm.py`

**Critical: all evals run through this.** No API calls.

### Rubric templates (per `action_type`):
- `data_parsing`: C1=row/column validation, C2=missing values, C3=month ordering
- `computation`: C1=KPI accuracy, C2=granularity (region×product×month), C3=anomaly detection
- `context_handoff`: C1=all facts transferred, C2=no errors passed, C3=charts included
- `report_structuring`: C1=anomaly section present, C2=required sections, C3=logical order
- `narrative_generation`: C1=anomaly named with numbers, C2=actionable recs, C3=no false clean verdict

### Grading heuristics (what triggers FAIL):

**Step 2 — computation:**
- C2 scores **0.3** if `key_facts_produced` contains `"granularity_used: aggregate"` or `"no region"`
- C3 scores **0.1** if `key_facts_produced` contains `"planted_anomaly_caught: false"` AND `source_data_summary.ground_truth_anomalies.planted` is non-empty

**Step 5 — narrative_generation:**
- C1 scores **0.1** if `key_facts_produced` contains `"planted_anomaly_in_narrative: false"` or `"south_productb_explicitly_named_in_narrative: false"` AND planted anomaly exists
- C3 scores **0.0** if `key_facts_produced` contains `"false_clean_verdict_stated: true"` AND planted anomaly exists

**Step pass threshold:** `step_score >= 0.5` (average of criterion scores)

### Plan quality — `evaluate_mock_plan(trace)`:
Searches `agent_plan` text for signal words: `["pandas", "python-docx", "word", "anomal", "z-score", "chart", "matplotlib"]`
- 0 hits → score 0.5
- 1-2 hits → score 0.65
- 3+ hits → score 0.9

---

## Raw Trace JSON Schema (input to pipeline)

```json
{
  "trace_id": "trace_fail_002",
  "run_date": "2026-04-29",
  "dataset_file": "02_single_anomaly_subtle.xlsx",
  "user_prompt": "Analyse this sales data and find any anomalies, create a Word report for leadership",

  "source_data_summary": {
    "rows": 216,
    "columns": ["Month", "Region", "Product", "Revenue", "Units_Sold"],
    "ground_truth_anomalies": {
      "planted": "South/Product_B March Revenue dropped from 37655 (Feb) to 22216 (Mar) — 41% decline"
    }
  },

  "agent_plan": "Load the file, detect anomalies using pandas z-score, create python-docx Word report with charts.",

  "steps": [
    {
      "step_number": 1,
      "app": "Excel",
      "action_type": "data_parsing",
      "latency_observed": "approx 3 seconds",
      "tools_called": ["pandas.read_excel"],
      "output": "216 rows loaded.",
      "key_facts_produced": ["row_count: 216", "null_values: none"]
    }
  ],

  "context_manifest": {
    "facts_produced_in_excel_step2": ["total_revenue: 8330077"],
    "facts_present_in_word_output": ["total_revenue: PRESENT"],
    "facts_lost_at_boundary": [],
    "boundary": "Excel to Word (Step 3)",
    "context_loss_detected": false
  },

  "word_output_actual_text": {
    "executive_summary": "...",
    "anomaly_section": "NOT PRESENT",
    "recommendations": ["..."]
  }
}
```

**Critical schema notes:**
- Ground truth MUST be at `source_data_summary.ground_truth_anomalies.planted` (NOT `source_data_summary.ground_truth`)
- Mock grader reads `key_facts_produced` as a list of strings and does substring matching (case-insensitive)
- `action_type` must exactly match: `data_parsing`, `computation`, `context_handoff`, `report_structuring`, `narrative_generation`

---

## Full Evaluation Result Schema (output of pipeline / returned by API)

```json
{
  "trace_id": "trace_001",
  "dataset_file": "01_clean_baseline.xlsx",
  "user_prompt": "...",
  "trajectory_score": 0.9119,
  "plan_score": 0.84,
  "avg_step_score": 0.94,
  "evaluated_at": "2026-04-28T12:00:00+00:00",

  "plan_result": {
    "plan_score": 0.84,
    "issues": [],
    "metric_breakdown": {
      "app_coverage": 1.0,
      "sequence_validity": 1.0,
      "anomaly_recall": 0.8,
      "false_positive_check": 0.8,
      "plan_quality": 0.65,
      "plan_quality_rationale": "Plan mentions specific tools and approach"
    }
  },

  "step_results": [
    {
      "step_number": 1,
      "app": "Excel",
      "action_type": "data_parsing",
      "step": { /* original step object from trace */ },
      "rubric": {
        "criteria": [
          { "id": "C1", "description": "...", "rationale": "mock rubric" }
        ],
        "_model": "mock"
      },
      "grade": {
        "criterion_grades": [
          { "id": "C1", "pass": true, "score": 0.9, "rationale": "Step appears to have completed successfully" }
        ],
        "step_score": 0.9,
        "step_pass": true,
        "failure_type": "null",
        "_model": "mock"
      },
      "colour": "green"
    }
  ],

  "context_result": {
    "facts_produced": ["total_revenue: 8330077"],
    "facts_present_in_word": ["total_revenue: PRESENT"],
    "facts_lost": [],
    "score": 1.0,
    "context_loss_detected": false,
    "boundary": "Excel to Word",
    "note": null
  },

  "failure_attribution": {
    "failure_transition_step": 2,
    "root_cause_app": "Excel",
    "root_cause_action": "computation",
    "failure_type": "computation_error",
    "fix_recommendation": "Fix Excel computation layer -- wrong granularity or method logic",
    "contaminated_steps": [5]
  }
}
```

---

## React Components — Current State

### `App.jsx` (root)
State: `traces`, `loadingList`, `selectedId`, `detail`, `loadingDetail`, `selectedStep`, `uploading`, `uploadError`, `stepDetailRef`

Key behavior:
- `loadTraceList()` — GET /traces, updates sidebar
- `selectTrace(id)` — GET /trace/:id, sets detail
- `uploadTrace(file)` — POST /evaluate (multipart), then refreshes list and auto-selects new trace
- `useEffect` on `selectedStep` — auto-scrolls `stepDetailRef` into view 50ms after selection

Layout: topbar → `app-shell` (sidebar + main-panel)
Main panel renders: `TraceHeader` → `PlanBreakdown` → `StepTimeline` → `div[ref=stepDetailRef]` (either `StepDetail` or placeholder) → `FailureCard` → `ContextManifest`

### `Sidebar.jsx`
Props: `traces, selectedId, onSelect, loading, onUpload, uploading, uploadError`
Contains hidden `<input type="file" accept=".json">` triggered by "Evaluate Trace" button.
Shows `uploadError` inline below button in red box.

### `Overview.jsx`
Shown when no trace is selected. 4 stat cards (total, pass rate, avg score, fail rate) + score distribution bar + full traces table. Clicking a table row calls `onSelect`.

### `TraceHeader.jsx`
- `ScoreRing` SVG component — animated stroke-dasharray circle showing trajectory score (0-100)
- `SubScore` — shows Plan Score and Avg Step Score with color coding
- `prompt-block` — blue left-border box showing user prompt prominently
- Shows `run_date`, `dataset_file` tag, issues list

### `PlanBreakdown.jsx`
5 metrics rendered via `MetricRow` component. Each has a "What is this?" toggle.

Backend key → UI label mapping:
- `anomaly_recall` → Step Completeness
- `plan_quality` → Goal Adherence
- `sequence_validity` → Sequence Validity
- `app_coverage` → Source Completeness
- `false_positive_check` → Hallucination at Plan Level

All weighted 0.20. Formula shown at bottom.

### `StepTimeline.jsx`
Horizontal step cards. Each card shows: step number, app, action type (abbreviated), score, PASS/FAIL chip, criterion dots.

`CriterionDots` — renders a small colored dot (green=pass, red=fail) per rubric criterion on the card face with hover tooltip. This is the key discoverability feature — users can see rubric status without clicking.

Clicking a card calls `onSelectStep`. Selected card gets blue border. Blue border also shows even if `StepDetail` is below viewport.

`ACTION_SHORT` map: `data_parsing`→Parsing, `computation`→Computation, `context_handoff`→Handoff, `report_structuring`→Structuring, `narrative_generation`→Narrative

Root cause step gets "ROOT CAUSE" red badge. Contaminated steps get "INHERITED" amber badge.

### `StepDetail.jsx`
Two tabs: "Step Evaluation" and "Agent Trajectory".

**Step Evaluation tab:**
- LLM attribution row — "Rubric generated by" / "Graded by" / criteria count (shown as `mock` tag in gray when mock mode)
- `CriterionCard` per criterion — pass/fail dot, criterion ID badge, description, score bar, score number, "See rationale" toggle
- Failure type label if step failed

**Agent Trajectory tab:**
- Step output text
- Tools called (blue monospace chips)
- Key facts produced (monospace chips)

### `FailureCard.jsx`
Only renders if `failure_attribution.failure_transition_step` is non-null.
Shows: failure type with color dot + chip, root cause step/app/type grid, description paragraph, fix recommendation box, contaminated steps pills.

Failure type colors: `computation_error`→red, `context_loss`→amber, `reasoning_error`→purple, `parsing_error`→blue, `inherited`→amber

### `ContextManifest.jsx`
Shows facts produced in Excel Step 2, facts present in Word output, facts lost at boundary. Each fact row has OK (green) or LOST (red) badge. Footer shows context score and loss detection chip.

---

## CSS Design System — `App.css`

Single file, ~570 lines. All dark theme.

Key CSS custom properties:
```css
--bg: #0a0c10       /* page background */
--bg2: #0f1117
--bg3: #161b22      /* card background */
--bg4: #1c2128      /* inset/secondary background */
--border: #21262d
--border2: #30363d
--green: #3fb950   --green-dim: #0d2e1f   --green-bd: #238636
--amber: #f0883e   --amber-dim: #2e1f0d   --amber-bd: #9e6a03
--red: #f85149     --red-dim: #2e0d0d     --red-bd: #da3633
--blue: #58a6ff    --blue-dim: #0d2239
--purple: #bc8cff
--radius: 10px     --radius-sm: 6px
```

Key component CSS classes (not exhaustive, but the ones most likely to need extension):
- `.step-card` — timeline step cards (sc-green/sc-amber/sc-red, selected)
- `.criterion-card` — rubric criterion cards (c-pass/c-fail)
- `.plan-metric-block` — plan metric row with expandable description
- `.prompt-block` — blue-bordered user prompt display
- `.llm-attribution-row` — model attribution bar in StepDetail
- `.upload-btn` / `.upload-error` — sidebar upload button states
- `.step-detail-placeholder` — dashed placeholder when no step selected
- `.rubric-model-tag` — model name pill (`.mock` variant = gray)
- `.criterion-dot` / `.criterion-dot-wrap` / `.criterion-dot-label` — timeline rubric dots
- `.root-cause-badge` / `.inherited-badge` — absolute-positioned badges on step cards
- `.pass-fail-chip` with `.badge-pass` / `.badge-fail`

---

## Known Constraints & Gotchas

1. **LLM APIs are over quota.** All pipeline runs use `mock=True`. The mock grader (`mock_llm.py`) uses exact substring matching on `key_facts_produced` strings — the exact strings matter. See Mock LLM section above for what triggers failures.

2. **Edit tool can fail on App.css** if the CSS contains Unicode arrow characters (→). If Edit tool says "string not found", rewrite the whole file with the Write tool instead.

3. **Vite proxy** — any new Flask endpoint must be added to `ui/vite.config.js` proxy config AND requires a Vite dev server restart. Current proxied paths: `/traces`, `/trace`, `/evaluate`, `/health`.

4. **`trace_fail_001.json`** in dataset/ has wrong schema (`source_data_summary.ground_truth` instead of `source_data_summary.ground_truth_anomalies.planted`) so it won't trigger mock grader failures correctly.

5. **`selectTrace` has `if (id === selectedId) return` guard** — if you need to force-reload the same trace (e.g., after re-evaluation), you must reset `selectedId` to null first.

6. **Auto-scroll** uses a 50ms `setTimeout` to let React render `StepDetail` before scrolling. Don't remove this — without it the ref element isn't in the DOM yet.

7. **`run_eval.py`** saves to `results/all_results.json` as a **list** (not a dict). The Flask API reads it as a list and searches by `trace_id`. The `/evaluate` endpoint merges by converting to a dict keyed by trace_id then back to list.

---

## What Has Been Implemented

All of the following are complete and working:

1. Plan evaluation with 5 named metrics (Step Completeness, Goal Adherence, Sequence Validity, Source Completeness, Hallucination at Plan Level), each with expandable "What is this?" description
2. Step-wise rubric display — criteria generated per step visible in StepDetail
3. LLM attribution — which model generated rubric, which graded, shown per step
4. No emojis anywhere in the codebase
5. User prompt displayed prominently in TraceHeader with blue left-border accent
6. Agent Trajectory vs Step Evaluation clearly differentiated via tab switcher in StepDetail
7. Criterion dots on each timeline step card for at-a-glance rubric status
8. Auto-scroll to StepDetail when a step card is clicked
9. "Evaluate Trace" upload button in sidebar — uploads raw trace JSON, runs pipeline, auto-selects result
10. `POST /evaluate` Flask endpoint with multipart file upload support
11. `/evaluate` added to Vite proxy config

---

## Potential Next Steps (not yet started)

- **Real LLM integration** — when API quota is restored, swap `mock=True` to `mock=False` in `/evaluate` endpoint
- **Re-evaluate button** — re-run pipeline on an already-loaded trace without re-uploading
- **Trace comparison view** — show two traces side by side
- **Filter/search** in sidebar — filter by score range, failure type, dataset
- **Export** — download evaluation result as PDF or CSV
- **Step detail for context_handoff** — currently shows generic trajectory; could show the specific facts passed/dropped
- **Editable rubrics** — allow user to edit generated criteria before grading
- **Dataset upload** — upload the actual `.xlsx` file alongside the trace (currently dataset files are pre-loaded in `temp_excel/`)
