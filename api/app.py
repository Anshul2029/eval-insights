"""
api/app.py — Flask REST API.

Endpoints:
  GET  /traces              → list of all trace IDs with summary scores
  GET  /trace/<trace_id>    → full evaluation result for one trace
  POST /evaluate            → upload a raw trace JSON and run the pipeline
  GET  /health              → liveness check

Run:
    cd api && python app.py
  or from project root:
    python -m api.app
"""

import json
import os
import sys
from datetime import datetime, timezone
from dotenv import load_dotenv
from flask import Flask, jsonify, abort, request, send_file
from flask_cors import CORS

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

# Allow importing pipeline and its dependencies from the project root
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

app = Flask(__name__)
CORS(app)

from api.streamlit_proxy import streamlit_proxy
app.register_blueprint(streamlit_proxy)

RESULTS_FILE = os.path.join(
    os.path.dirname(__file__), "..", "results", "all_results.json"
)
RESULTS_DIR = os.path.join(
    os.path.dirname(__file__), "..", "results"
)
DATASET_DIR = os.path.join(
    os.path.dirname(__file__), "..", "dataset"
)


def _load_results() -> list:
    if not os.path.exists(RESULTS_FILE):
        return []
    with open(RESULTS_FILE, encoding="utf-8") as f:
        return json.load(f)


def _save_results(all_results: list) -> None:
    os.makedirs(os.path.dirname(RESULTS_FILE), exist_ok=True)
    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2, default=str)


def _score_colour(score: float) -> str:
    if score >= 0.85:
        return "green"
    return "red"


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/traces")
def get_traces():
    results = _load_results()
    summary = []
    for r in results:
        fa = r.get("failure_attribution", {})
        required_actions = {"data_parsing", "computation", "context_handoff", "report_structuring", "narrative_generation"}
        present_actions = {sr.get("action_type", "") for sr in r.get("step_results", [])}
        summary.append(
            {
                "trace_id": r.get("trace_id"),
                "dataset_file": r.get("dataset_file", ""),
                "user_prompt": r.get("user_prompt", "")[:80],
                "trajectory_score": r.get("trajectory_score", 0.0),
                "plan_score": r.get("plan_score", 0.0),
                "avg_step_score": r.get("avg_step_score", 0.0),
                "colour": _score_colour(r.get("trajectory_score", 0.0)),
                "failure_transition_step": fa.get("failure_transition_step"),
                "failure_type": fa.get("failure_type"),
                "evaluated_at": r.get("evaluated_at", ""),
                "error": r.get("error"),
                "step_completeness_failed": not required_actions.issubset(present_actions),
            }
        )
    summary.sort(key=lambda x: x["trace_id"] or "")
    return jsonify(summary)


@app.route("/trace/<trace_id>")
def get_trace(trace_id: str):
    results = _load_results()
    for r in results:
        if r.get("trace_id") == trace_id:
            for sr in r.get("step_results", []):
                score = sr.get("grade", {}).get("step_score", 0.0)
                sr["colour"] = _score_colour(score)
            return jsonify(r)
    abort(404, description=f"Trace '{trace_id}' not found in results.")


@app.route("/evaluate", methods=["POST"])
def evaluate_trace():
    """Accept a raw trace JSON (file upload or JSON body), run the pipeline, save result."""
    # Support both multipart file upload and raw JSON body
    if request.files.get("file"):
        raw = request.files["file"].read()
        try:
            trace = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            return jsonify({"error": f"Invalid JSON file: {e}"}), 400
    elif request.is_json:
        trace = request.get_json()
    else:
        return jsonify({"error": "Send a JSON file (multipart 'file' field) or a JSON body"}), 400

    if not isinstance(trace, dict):
        return jsonify({"error": "Trace must be a JSON object"}), 400

    trace_id = trace.get("trace_id")
    if not trace_id:
        return jsonify({"error": "Trace JSON must have a 'trace_id' field"}), 400

    if not trace.get("steps"):
        return jsonify({"error": "Trace JSON must have a 'steps' array"}), 400

    # Optionally save the raw trace to dataset/ for future re-runs
    try:
        os.makedirs(DATASET_DIR, exist_ok=True)
        trace_path = os.path.join(DATASET_DIR, f"{trace_id}.json")
        with open(trace_path, "w", encoding="utf-8") as f:
            json.dump(trace, f, indent=2)
    except Exception:
        pass  # Don't fail if we can't save the raw trace

    # Run the pipeline with real LLM
    try:
        from pipeline import run_pipeline
        result = run_pipeline(trace, verbose=False, mock=False)
    except Exception as e:
        return jsonify({"error": f"Pipeline error: {e}"}), 500

    result["evaluated_at"] = datetime.now(timezone.utc).isoformat()

    # Merge into all_results.json
    existing_list = _load_results()
    existing_map = {r["trace_id"]: r for r in existing_list}
    existing_map[trace_id] = result
    _save_results(list(existing_map.values()))

    # Enrich step results with colour before returning
    for sr in result.get("step_results", []):
        score = sr.get("grade", {}).get("step_score", 0.0)
        sr["colour"] = _score_colour(score)

    return jsonify(result), 201


@app.route("/comparisons")
def get_comparisons():
    import glob
    files = sorted(glob.glob(os.path.join(RESULTS_DIR, "comparison_*.json")))
    out = []
    for f in files:
        try:
            with open(f, encoding="utf-8") as fh:
                data = json.load(fh)
            llms = data.get("llms_compared", [])
            entry = {
                "comparison_id": data.get("comparison_id"),
                "generated_at":  data.get("generated_at", ""),
                "llms_compared": llms,
                "scores": {
                    llm: {
                        "trajectory_score": data.get(llm, {}).get("trajectory_score", 0),
                        "total_tokens":     data.get(llm, {}).get("token_usage", {}).get("total", {}).get("total_tokens", 0),
                    }
                    for llm in llms
                },
            }
            out.append(entry)
        except Exception:
            pass
    return jsonify(out)


@app.route("/comparison/<trace_id>")
def get_comparison(trace_id: str):
    path = os.path.join(RESULTS_DIR, f"comparison_{trace_id}.json")
    if not os.path.exists(path):
        abort(404, description=f"No comparison found for '{trace_id}'")
    with open(path, encoding="utf-8") as f:
        return jsonify(json.load(f))


@app.route("/comparison/<trace_id>/docx/<llm>")
def download_comparison_docx(trace_id: str, llm: str):
    path = os.path.join(RESULTS_DIR, f"comparison_{trace_id}.json")
    if not os.path.exists(path):
        abort(404, description=f"No comparison found for '{trace_id}'")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if llm not in data:
        abort(404, description=f"LLM '{llm}' not in comparison")
    docx_path = data[llm].get("trace", {}).get("word_doc_path", "")
    if not docx_path or not os.path.exists(docx_path):
        abort(404, description=f"Word document not found for {llm}")
    filename = f"report_{trace_id}_{llm}.docx"
    return send_file(docx_path, as_attachment=True, download_name=filename,
                     mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document")


if os.environ.get("FLASK_SERVE_STATIC"):
    _STATIC_DIR = os.path.join(os.path.dirname(__file__), "..", "ui", "dist")
    if os.path.isdir(_STATIC_DIR):
        from flask import send_from_directory

        @app.route("/", defaults={"path": ""})
        @app.route("/<path:path>")
        def serve_frontend(path):
            full = os.path.join(_STATIC_DIR, path)
            if path and os.path.isfile(full):
                return send_from_directory(_STATIC_DIR, path)
            return send_from_directory(_STATIC_DIR, "index.html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
