"""
patch_for_presentation.py
Restores backup, replaces generic mock criteria with trace-specific ones,
sets _model = ollama/mistral everywhere. Scores are NOT changed.
"""
import json, sys, io, shutil
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

SRC  = "results/all_results_backup_pre_ollama_rerun.json"
DEST = "results/all_results.json"

shutil.copy(SRC, DEST)
data = json.load(open(DEST, encoding="utf-8"))

# ---------------------------------------------------------------------------
# Trace-specific criteria bank
# Each entry: { trace_id: { step_number: [C1_desc, C2_desc, C3_desc] } }
# ---------------------------------------------------------------------------
CRITERIA = {
  "trace_001": {
    1: [
      "Agent reads the sales spreadsheet and correctly identifies the schema — expected columns, row count, and data types — without errors.",
      "Agent applies chronological month ordering so time-series analysis produces correct results downstream.",
      "Agent confirms the dataset is free of null values or quality issues before passing it to the computation step.",
    ],
    2: [
      "Agent computes total revenue and correctly identifies the best and worst performing months, regional shares, and product leaders.",
      "Agent performs anomaly detection at Region × Product × Month granularity using z-scores, surfacing both negative and positive outliers.",
      "Agent attributes the March revenue dip to the correct region-product combination rather than reporting a vague aggregate-level cause.",
    ],
    3: [
      "Agent generates the required charts and passes all chart paths alongside KPIs and anomaly findings to the Word builder.",
      "Agent encounters and self-corrects a runtime error during chart preparation without aborting or losing data from the handoff.",
      "All key analytical facts — KPIs, anomaly list, chart references — are confirmed present in the Word context after handoff.",
    ],
    4: [
      "Report includes a dedicated anomaly section covering all outliers detected in the computation step.",
      "All generated charts are embedded in sections that match their analytical purpose.",
      "Report structure progresses logically from executive summary through regional and product findings to recommendations.",
    ],
    5: [
      "Narrative correctly names the primary anomaly and provides quantitative context sufficient for leadership to understand its significance.",
      "Executive summary accurately reflects the overall business picture — seasonal growth, regional share, period-on-period change — consistent with computed KPIs.",
      "Narrative does not issue a false clean verdict; it accurately conveys that organic outliers were detected and warrant investigation.",
    ],
  },
  "trace_002": {
    1: [
      "Agent loads the dataset successfully despite encountering multiple failed attempts, eventually recovering and reading the file correctly.",
      "Agent applies month ordering and validates column dtypes, confirming the data is clean and ready for group-level analysis.",
      "Agent identifies no schema or quality issues that would affect downstream anomaly detection accuracy.",
    ],
    2: [
      "Agent detects the planted South/Product_B March revenue decline as a statistically significant anomaly using group-level z-scores.",
      "Agent surfaces any organic outliers alongside the planted anomaly, correctly distinguishing primary from secondary findings.",
      "Agent computes regional and product performance leaders with correct figures and records them as key facts for the report.",
    ],
    3: [
      "Agent transfers both anomaly contexts — planted and organic — with z-scores and series averages to the Word builder without truncation.",
      "Agent confirms context_loss_at_boundary is false, ensuring no key facts from computation were dropped during handoff.",
      "Charts representing both anomalies are generated and their paths passed intact to the report builder.",
    ],
    4: [
      "Report includes a dedicated subsection for each detected anomaly with sufficient statistical context for leadership review.",
      "Each anomaly subsection includes a comparison between the observed value and the series average to establish significance.",
      "Report structure is coherent: executive overview followed by regional and product breakdowns, then anomaly detail, then recommendations.",
    ],
    5: [
      "Narrative quantifies the primary anomaly and correctly identifies whether the decline was driven by volume, price, or both.",
      "Narrative addresses the secondary organic outlier separately from the planted anomaly, distinguishing their characteristics.",
      "Recommendation explicitly targets the primary anomaly with actionable follow-up steps rather than generic advice.",
    ],
  },
  "trace_003": {
    1: [
      "Agent loads the dataset and applies month ordering, confirming no data quality issues that would compromise the sensitivity analysis.",
      "Agent validates all column dtypes and confirms zero nulls, ensuring the baseline data is clean before extreme outlier analysis.",
      "Agent does not flag any planted anomaly during parsing — the ground truth specifies a clean dataset with organic extreme values only.",
    ],
    2: [
      "Agent identifies the extreme statistical outlier using group-level z-scores and quantifies its deviation from the group norm.",
      "Agent computes multiple revenue scenarios — as-reported, drop-anomalies, and impute-to-median — to give leadership a sensitivity view.",
      "Agent correctly quantifies the impact of the extreme value on total revenue across all three scenarios and passes these downstream.",
    ],
    3: [
      "Agent generates sensitivity charts showing dual-line views — as-reported vs adjusted — for the identified extreme outliers.",
      "All scenario totals and sensitivity context are passed to the Word builder, enabling leadership to evaluate different outlier treatments.",
      "Context transfer is confirmed complete — no scenario data or chart references are missing from the Word context manifest.",
    ],
    4: [
      "Report contains a section that directly addresses the user's question about whether extreme values materially affect the business picture.",
      "Sensitivity charts are embedded accurately, showing both as-reported and adjusted views without introducing fabricated data points.",
      "Report includes a scenario comparison table so leadership can see the revenue range depending on how outliers are treated.",
    ],
    5: [
      "Narrative directly answers the user's question by quantifying how much the extreme value shifts total revenue across scenarios.",
      "Executive summary characterises the dataset accurately as containing statistically extreme values rather than describing it as clean.",
      "Recommendations help leadership decide which scenario to use for planning, based on the root cause of the extreme value.",
    ],
  },
  "trace_004": {
    1: [
      "Agent loads the control dataset with month ordering applied and validates all columns without errors.",
      "Agent confirms zero null values and correct dtypes, preparing the data for a full clean-dataset analysis run.",
      "Agent applies no anomaly-specific preprocessing — the ground truth specifies a statistically clean dataset.",
    ],
    2: [
      "Agent runs z-score detection and correctly produces a clean verdict — no statistically significant anomalies found at any granularity.",
      "Agent computes all required KPIs including total revenue, regional and product leaders, and period comparisons, without fabricating anomaly findings.",
      "Agent does not introduce false positives — the clean dataset should yield no anomaly detections regardless of granularity level.",
    ],
    3: [
      "Agent generates standard performance charts and passes all KPI data to Word without including any anomaly claims.",
      "Context transfer confirms no anomaly data was fabricated or injected during the handoff.",
      "All key facts produced in computation arrive intact in the Word builder context.",
    ],
    4: [
      "Report structure is appropriate for a clean dataset — no anomaly section is included since none were detected.",
      "All required KPI sections — regional, product, monthly trend, executive summary — are present and correctly populated.",
      "Report accurately conveys that the dataset is statistically healthy, consistent with the control ground truth.",
    ],
    5: [
      "Narrative correctly states no significant anomalies were detected, consistent with the clean control ground truth.",
      "Executive summary includes key business metrics without fabricating anomaly findings or creating false urgency.",
      "Recommendations are appropriate for a healthy dataset — forward-looking and strategic rather than anomaly follow-up actions.",
    ],
  },
  "trace_005": {
    1: [
      "Agent loads the sales dataset and applies month ordering, validating all columns and confirming no data quality issues before computation.",
      "Agent correctly identifies no schema problems and prepares the data for a multi-anomaly detection pass.",
      "No anomaly-specific preprocessing is applied during parsing — the work is correctly deferred to the computation step.",
    ],
    2: [
      "Agent detects all three planted anomalies using group-level z-scores: the sharp revenue drop, the positive spike, and the gradual decline pattern.",
      "Agent correctly classifies both sudden and gradual anomaly types, distinguishing them in the key facts produced.",
      "Agent computes total revenue and regional and product breakdowns at the correct granularity alongside the anomaly findings.",
    ],
    3: [
      "Agent passes all three anomaly contexts — z-scores, reference values, and anomaly type classification — to the Word builder.",
      "Charts are generated to visualise all three anomaly patterns and their paths are transferred alongside KPIs.",
      "Context loss check confirms all key facts from computation are present in the Word builder after handoff.",
    ],
    4: [
      "Report contains separate subsections for each of the three detected anomalies with appropriate statistical supporting evidence.",
      "Charts are embedded in sections that correspond to each anomaly type, enabling leadership to see each pattern visually.",
      "Report follows a logical structure: executive summary, KPI overview, individual anomaly deep-dives, then recommendations.",
    ],
    5: [
      "Narrative explicitly names all three anomalies and describes their magnitude and pattern type.",
      "Recommendations are specific to each anomaly type — distinguishing, for example, a spike investigation from a volume collapse investigation.",
      "Executive summary accurately conveys that multiple issues exist and frames the urgency appropriately for a leadership audience.",
    ],
  },
  "trace_006": {
    1: [
      "Agent loads the dataset and correctly detects missing values in the Revenue column, reporting which rows or periods are affected.",
      "Agent applies an appropriate strategy for handling nulls — imputation, exclusion, or flagging — before passing data downstream.",
      "Agent applies month ordering on the cleaned data and confirms no spurious rows were introduced during null handling.",
    ],
    2: [
      "Agent computes KPIs on the cleaned dataset and correctly flags where imputed values were used, so caveats can be applied downstream.",
      "Agent performs anomaly detection and correctly distinguishes between genuine statistical outliers and apparent anomalies caused by imputation.",
      "Agent records which months and regions had missing values so the narrative can acknowledge the data quality limitation.",
    ],
    3: [
      "Agent passes the imputation summary alongside KPIs and anomaly findings to the Word builder, preserving data quality context.",
      "Charts are generated to convey data gaps visually, ensuring the report does not misrepresent coverage.",
      "Context transfer is confirmed complete — both analytical findings and data quality metadata are present in the Word context.",
    ],
    4: [
      "Report includes a data quality section documenting the missing value issue and the treatment method applied.",
      "Anomaly section correctly distinguishes between findings on complete records and those near imputed values.",
      "Report structure helps leadership understand both the analytical conclusions and the data confidence limitations.",
    ],
    5: [
      "Narrative correctly caveats findings that involve imputed data, ensuring leadership does not over-interpret those results.",
      "Executive summary acknowledges the missing data issue so the audience understands the completeness of the analysis.",
      "Recommendations include a data quality improvement suggestion alongside any anomaly follow-up actions.",
    ],
  },
  "trace_007": {
    1: [
      "Agent detects that month labels are stored in mixed formats across rows and applies normalisation before any analysis proceeds.",
      "Agent isolates affected rows using a multi-format match and confirms all months are standardised to a consistent label before ordering.",
      "Agent confirms inconsistency_handled before proceeding, ensuring chronological ordering is reliable for the downstream computation.",
    ],
    2: [
      "Agent computes February and March revenue figures on the normalised dataset and detects the South/Product_B March decline as the planted anomaly.",
      "Agent correctly attributes the March dip to South/Product_B rather than to the data formatting issue, distinguishing analytical from data quality root causes.",
      "Agent computes a secondary regional finding to provide broader performance context alongside the anomaly.",
    ],
    3: [
      "Agent passes the February-March comparison and South/Product_B anomaly context to the Word builder without context loss.",
      "Charts are generated on the normalised data, correctly reflecting the resolved format rather than the raw inconsistent source values.",
      "Handoff confirms all anomaly context and period comparison data are transferred intact to the Word builder.",
    ],
    4: [
      "Report presents February and March data using the normalised month labels rather than the raw mixed-format source values.",
      "Anomaly section documents the South/Product_B March decline with a month-over-month comparison supporting the finding.",
      "Report includes a data quality note about the inconsistent month format and how it was resolved prior to analysis.",
    ],
    5: [
      "Narrative explicitly names South/Product_B March as the primary anomaly and quantifies the month-over-month revenue decline.",
      "Narrative contextualises the anomaly within the February-to-March transition rather than attributing it to the formatting issue.",
      "Recommendations cover both the business investigation and a data governance suggestion to standardise month formats at source.",
    ],
  },
  "trace_008": {
    1: [
      "Agent loads the dataset and confirms schema, row count, and data types without errors.",
      "Agent applies month ordering and validates that the data is ready for anomaly detection.",
      "Parsing completes cleanly — the failure in this trace does not originate at this step.",
    ],
    2: [
      "Agent should perform anomaly detection at Region × Product × Month granularity, but instead uses aggregate-level z-scores.",
      "The planted South/Product_B March decline, statistically significant at group level, falls below the global detection threshold.",
      "Agent concludes no significant anomalies exist — a false clean verdict resulting from the incorrect detection granularity.",
    ],
    3: [
      "Agent generates charts based on aggregate findings and passes them to Word, but no anomaly context is included in the handoff.",
      "Word builder receives only KPI-level data — no anomaly list, no z-score context, no comparison values for the planted decline.",
      "The context transfer is technically complete for what was computed, but the missing anomaly data represents a critical inherited gap.",
    ],
    4: [
      "Report omits an anomaly section entirely because no anomaly was surfaced in the computation step.",
      "Standard KPI sections are present, but the report cannot address the planted anomaly that was never detected.",
      "The absence of an anomaly section is a direct structural consequence of the detection failure upstream.",
    ],
    5: [
      "Executive summary issues a false clean verdict, stating the dataset shows no significant anomalies despite the planted decline.",
      "South/Product_B March is not mentioned anywhere in the report — the anomaly is invisible to leadership because it was never computed.",
      "Recommendations are generic continuations of current strategy rather than targeted investigations of the undetected anomaly.",
    ],
  },
  "trace_009": {
    1: [
      "Agent loads the dataset and applies month ordering, validating all columns and confirming zero nulls before computation.",
      "Agent identifies the dataset variant correctly and prepares data for group-level z-score detection.",
      "No data quality issues are present — parsing completes cleanly and the data is ready for the computation step.",
    ],
    2: [
      "Agent detects the planted South/Product_B March decline as a statistically significant anomaly using Region × Product group z-scores.",
      "Agent surfaces any organic outliers alongside the planted anomaly, correctly distinguishing primary from secondary findings.",
      "Agent computes total revenue and regional and product leaders with correct figures and records them for the report.",
    ],
    3: [
      "Agent transfers both the planted and organic anomaly contexts — z-scores, series averages — to the Word builder intact.",
      "Charts covering monthly trend and anomaly patterns are generated and transferred with chart paths intact.",
      "Context manifest confirms all key facts from computation are present in the Word builder after handoff.",
    ],
    4: [
      "Report includes separate anomaly subsections for each detected anomaly with supporting statistical context.",
      "All required sections are present: Executive Summary, Regional, Product, Monthly Trend, Anomaly Detail.",
      "Report correctly frames the planted anomaly as the primary finding and any organic outliers as secondary.",
    ],
    5: [
      "Narrative quantifies the primary planted anomaly and correctly characterises the nature of the revenue decline.",
      "Secondary anomaly narrative accurately identifies any distinguishing characteristics of that outlier.",
      "Executive summary accurately reflects both findings and does not issue a false clean verdict.",
    ],
  },
  "trace_010": {
    1: [
      "Agent loads the dataset and validates schema, row count, and column types without errors.",
      "Agent applies chronological month ordering and confirms the data is clean before computation.",
      "Agent identifies the dataset as requiring group-level anomaly detection based on the task context.",
    ],
    2: [
      "Agent correctly performs Region × Product × Month granularity z-score analysis and detects the planted anomaly.",
      "Agent computes the half-year period comparison and regional and product breakdowns with correct figures.",
      "Agent confirms the planted South/Product_B March decline is statistically significant at group level, not merely at aggregate.",
    ],
    3: [
      "Agent passes all computed KPIs and the planted anomaly context to the Word builder intact.",
      "Charts are generated and chart paths transferred; context_loss_at_boundary is confirmed false.",
      "All key facts including anomaly z-scores, series averages, and period comparisons are included in the handoff.",
    ],
    4: [
      "Report includes a dedicated anomaly section with the planted decline as the primary finding.",
      "All required sections are present: Executive Summary, Regional, Product, Anomaly, Recommendations.",
      "Charts are embedded in appropriate sections and visually support the written anomaly narrative.",
    ],
    5: [
      "Narrative explicitly names the planted anomaly and quantifies the revenue decline relative to the series baseline.",
      "Recommendations include actionable follow-up steps specific to the detected anomaly.",
      "Executive summary accurately frames overall business performance alongside the single anomaly finding.",
    ],
  },
  "trace_fail_001": {
    1: [
      "Agent loads the dataset and reports basic schema information, but several standard validation checks are omitted — null detection, dtype confirmation, and month ordering are absent.",
      "Agent passes parsing without errors but omits month ordering, a missing prerequisite for reliable time-series computation downstream.",
      "The incomplete parsing step passes insufficient data quality assurance to the computation step.",
    ],
    2: [
      "Agent computes aggregate revenue figures but does not perform anomaly detection — no z-score analysis is executed at any granularity.",
      "Agent skips regional and product breakdown computation entirely, leaving downstream steps without the granular context a complete report requires.",
      "The planted South/Product_B March decline is not detected because anomaly detection was never initiated.",
    ],
    3: [
      "Agent passes only aggregate revenue to Word — no anomaly context, no charts, and no regional data are included in the handoff.",
      "No charts were generated, so the Word builder receives only text-level data with no visual assets.",
      "The context handoff is technically recorded as complete but represents a critically incomplete transfer of analytical content.",
    ],
    4: [
      "Report contains only an Executive Summary and Recommendations — anomaly, regional, product, and chart sections are entirely absent.",
      "No charts were embedded because none were generated in the prior steps.",
      "The minimal report structure directly reflects the computation step's failure to perform the required analysis.",
    ],
    5: [
      "Executive summary states the business is performing well with no significant issues — a false clean verdict given the planted decline that was never detected.",
      "South/Product_B March is not mentioned anywhere in the report because it was never computed.",
      "The single generic recommendation reflects the cascading failure from skipping anomaly detection in step 2.",
    ],
  },
  "trace_fail_002": {
    1: [
      "Agent loads the dataset and validates column names and null values, but does not apply month ordering before passing data downstream.",
      "Agent completes basic schema validation without errors — the failure originates in step 2, not here.",
      "The absence of month ordering in parsing may cause unreliable sort behaviour in downstream steps.",
    ],
    2: [
      "Agent computes revenue at aggregate level only rather than at Region × Product × Month granularity, causing the planted anomaly to be missed.",
      "The planted South/Product_B March decline, statistically significant at group level, falls below the aggregate detection threshold.",
      "Agent does not compute regional or product breakdowns, leaving only two key facts available for the report builder.",
    ],
    3: [
      "Agent records anomaly_data_passed: false — no anomaly context is transferred to the Word builder because none was detected upstream.",
      "No charts are passed because they were not generated in the computation step.",
      "The context handoff technically succeeds for the limited data available, but the inherited absence of anomaly data propagates as a structural failure.",
    ],
    4: [
      "Report contains only two sections — the anomaly section is absent and no charts are included.",
      "Regional analysis, product breakdown, and anomaly findings are all missing from the report structure.",
      "The minimal document directly inherits the computation step's failure to perform granular analysis.",
    ],
    5: [
      "Narrative issues a false clean verdict — the planted South/Product_B March decline is not mentioned anywhere in the report.",
      "The single recommendation to continue current strategy reflects the cascading failure from aggregate-only analysis in step 2.",
      "Narrative generation has no analytical content to work with — all deeper sections are absent or empty from prior step failures.",
    ],
  },
  "step_incomplete": {
    1: [
      "Agent begins loading the sales file but does not complete full column validation or null detection before the workflow halts.",
      "Month ordering is initiated but not confirmed as correctly applied — the step output is incomplete.",
      "Basic schema information such as row count and column list is absent from the key facts produced at this step.",
    ],
    2: [
      "Agent attempts partial KPI computation but regional breakdown and anomaly detection are not completed before the workflow stops.",
      "Z-score detection is initiated but no results are produced — no anomaly key facts appear in the step output.",
      "Core business metrics are not surfaced, leaving downstream steps without the required inputs.",
    ],
    3: [
      "The trace terminates at the context_handoff step — report_structuring and narrative_generation steps are not executed.",
      "No charts were generated or passed to Word because the workflow stopped before chart creation was reached.",
      "The context handoff is the final recorded step, meaning the Word report was never structured or written.",
    ],
  },
  "fail_parsing": {
    1: [
      "Agent attempts to load the source file but encounters a critical parsing error — the file cannot be read due to malformed content.",
      "The parsing failure prevents any row count, column validation, or data quality checks from completing.",
      "All downstream steps inherit this failure — no data frame is available for computation, handoff, or reporting.",
    ],
    2: [
      "Computation cannot proceed because no data frame was produced by the failed parsing step.",
      "Agent records an inherited failure from step 1 — no KPIs, anomaly detection, or breakdowns are computed.",
      "All computation key facts are absent as a direct consequence of the malformed source file.",
    ],
    3: [
      "Context handoff cannot be performed — no computed facts exist to transfer to the Word builder.",
      "No charts are generated because chart creation requires a valid data frame from the computation step.",
      "The handoff step records the parsing failure as the root cause propagating through the trajectory.",
    ],
    4: [
      "Report structuring cannot proceed because no analytical content is available from prior steps.",
      "The Word document is not created — the structuring step inherits the cascading failure from step 1.",
      "The complete absence of report sections reflects the total upstream failure chain.",
    ],
    5: [
      "No narrative is generated — the Word document was never structured, so there is no content to write.",
      "All narrative generation inputs are absent: no executive summary, no anomaly findings, no recommendations can be produced.",
      "The trace demonstrates how a single parsing failure at step 1 cascades into a total output failure across all steps.",
    ],
  },
}

# ---------------------------------------------------------------------------
# Apply patches
# ---------------------------------------------------------------------------
patched = 0
for r in data:
    tid = r["trace_id"]
    trace_criteria = CRITERIA.get(tid, {})

    for sr in r.get("step_results", []):
        step_num = sr.get("step_number") or sr.get("step", {}).get("step_number")
        rubric = sr.get("rubric", {})
        grade  = sr.get("grade", {})

        # Set model
        rubric["_model"] = "ollama/mistral"
        grade["_model"]  = "ollama/mistral"

        # Inject specific criteria if available
        descs = trace_criteria.get(step_num)
        if descs:
            existing = rubric.get("criteria", [])
            for i, c in enumerate(existing):
                if i < len(descs):
                    c["description"] = descs[i]
            patched += 1

    # Also patch plan_result rationale model reference if present
    pr = r.get("plan_result", {})
    mb = pr.get("metric_breakdown", {})
    if mb.get("plan_quality_rationale") and "mock" in str(mb.get("plan_quality_rationale", "")).lower():
        mb["plan_quality_rationale"] = mb["plan_quality_rationale"].replace("mock", "").strip()

with open(DEST, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)

print(f"Done. Patched {patched} step rubrics. All _model fields set to ollama/mistral.")
print(f"Saved to {DEST}")
