# Eval Insights Platform — Trajectory-Level Failure Analysis

## Goal (WHY)

Excel Copilot eval runs produce pass/fail verdicts — but **not why** something failed, **how** the agent behaved during execution, or **what patterns** repeat across hundreds of queries. Teams reviewing eval results were manually reading JSON trajectories or relying on aggregate pass rates that hide critical failure modes.

**The goal**: Turn raw eval output into actionable intelligence — so the team knows exactly what to fix, what's working, and where effort will have the highest ROI — without reading a single trajectory manually.

---

## Approach (HOW)

### 1. Trajectory Parsing & Signal Extraction
- Parse raw `playOutput` JSON files — extract every agent step (user query, script execution, script response, agent message) into a structured timeline
- Compute trajectory-level signals: step count, error positions, error types, execution time, script similarity groups (retry loops), success claims vs actual results

### 2. LLM-Powered Failure Classification (Two-Axis)
- **Trajectory Axis** — *where* in the agent's process it went wrong (e.g., Misguided Strategy, Failed Error Recovery, Premature Completion)
- **Outcome Axis** — *what's* wrong with the final output (e.g., Wrong Values, Incomplete Output, Correct Logic Wrong Execution)
- Each failure gets a root cause explanation and suggested fix, not just a category label

### 3. Multi-Dimensional Analysis
- **Success Analysis**: Golden trajectories (zero errors, <8 steps), recovery patterns (hit errors but self-corrected), passing operations
- **Failure Analysis**: Error-triggering operations, actual error messages from console output, step efficiency gap, wasted steps after first error, retry loop detection
- **Root Cause Analysis**: LLM synthesizes failure patterns into grouped root causes — not restating error types, but interpreting *why* they happen
- **False Claim Detection**: Queries where the agent claims success but the grader says it failed — a self-evaluation gap
- **Recovery Analysis**: Cross-references errors the agent recovers from vs the same errors that cause failures elsewhere

### 4. Cross-Run Evolution Tracking
- Upload multiple runs → track per-query status changes (Pass→Fail, Fail→Pass, Consistent)
- Categorize query evolution patterns with LLM analysis — why did regressions happen, what improved
- Trajectory diff: how the agent's step-by-step behavior changes across runs for the same query

### 5. Exportable Reports
- Self-contained HTML report covering all analysis tabs — shareable offline with stakeholders
- Includes: Executive Summary, Dashboard, Deep Analysis, and per-query detail with categories and root causes

---

## Impact (WHAT Gains)

### Previously Not Possible → Now Unlocked

| Capability | Before | Now |
|---|---|---|
| **Why a query failed** | Manual trajectory reading | Automated two-axis classification with root cause |
| **Failure pattern grouping** | Ad-hoc, per-person | Systematic: trajectory + outcome categories across all failures |
| **Recovery intelligence** | Not tracked | Identifies which errors are recoverable, which aren't, and what differs |
| **False confidence detection** | Hidden in pass/fail | Flags queries where agent claims success incorrectly — quantifies self-evaluation gap |
| **Wasted effort quantification** | Unknown | Counts exact steps wasted after first error — directly maps to latency/cost savings |
| **Retry loop detection** | Not visible | Identifies queries where agent repeats near-identical failing scripts |
| **Cross-run regression tracking** | Manual diff of reports | Automated per-query status change tracking with trajectory-level diff |
| **Intent-level performance** | Aggregate pass rate only | Pass rate by query intent, specificity, complexity — shows where the agent is strong vs weak |
| **Actionable recommendations** | Generic "improve error handling" | LLM-generated, specific to the data: names error types, operations, batch patterns |
| **Shareable analysis** | Screenshots or raw JSON | One-click HTML report with full analysis for stakeholder review |

### Key Insights Surfaced (Examples)

- **Success Analysis** reveals that golden trajectories cluster in specific batches and intent types — these are the agent's strength zones and optimization benchmarks
- **Failure Analysis** shows that certain operations (e.g., conditional formatting, multi-sheet references) trigger disproportionate errors — prioritizable fix targets
- **Root Cause Analysis** groups failures by underlying cause (not just error type) — e.g., "agent miscalculates because it doesn't handle null cells" vs "agent targets wrong sheet"
- **Recovery vs Failure cross-reference** identifies errors the agent *can* recover from — the recovery strategy can be extracted and applied to currently-failing queries
- **False claim rate** quantifies the agent's self-evaluation blind spot — a direct input for adding verification steps
- **Wasted steps metric** translates directly to potential latency/cost reduction — "X steps could be saved if the agent stopped after first error"

---

*Platform: Streamlit + LLM (Groq/Gemini) | Input: evalVNext playOutput JSON | Output: Interactive dashboard + downloadable HTML report*
