"""
generate_traces.py — CLI to generate agent trajectory traces via Groq.

Usage:
  python generate_traces.py                                              # 1 good_agent per Excel file
  python generate_traces.py --file 02_single_anomaly_subtle.xlsx --scenario lazy_agent
  python generate_traces.py --count 5                                    # 5 random combos
  python generate_traces.py --no-llm                                     # Skip Groq, use fallback prose
  python generate_traces.py --start-id 100                               # Start numbering at 100
"""

import argparse
import json
import os
import random
import sys

from trace_generator import Scenario, generate_trace


EXCEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "temp_excel")
DATASET_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dataset")


def get_excel_files():
    return sorted(f for f in os.listdir(EXCEL_DIR) if f.endswith(".xlsx"))


def main():
    parser = argparse.ArgumentParser(description="Generate agent trajectory traces")
    parser.add_argument("--file", help="Specific Excel file to use")
    parser.add_argument("--scenario", choices=[s.value for s in Scenario],
                        default="good_agent", help="Agent behavior scenario")
    parser.add_argument("--count", type=int, help="Number of random traces to generate")
    parser.add_argument("--all-files", action="store_true", help="Generate one trace per Excel file")
    parser.add_argument("--start-id", type=int, default=1, help="Starting trace ID number")
    parser.add_argument("--no-llm", action="store_true", help="Skip Groq, use fallback prose")
    args = parser.parse_args()

    scenario = Scenario(args.scenario)
    plans = []

    if args.file:
        filepath = os.path.join(EXCEL_DIR, args.file)
        if not os.path.exists(filepath):
            print(f"Error: {filepath} not found")
            sys.exit(1)
        plans.append((filepath, scenario))
    elif args.count:
        files = get_excel_files()
        scenarios = list(Scenario)
        for _ in range(args.count):
            f = random.choice(files)
            s = random.choice(scenarios)
            plans.append((os.path.join(EXCEL_DIR, f), s))
    elif args.all_files:
        for f in get_excel_files():
            plans.append((os.path.join(EXCEL_DIR, f), scenario))
    else:
        for f in get_excel_files():
            plans.append((os.path.join(EXCEL_DIR, f), scenario))

    token_est = len(plans) * 2500
    print(f"Generating {len(plans)} trace(s) (~{token_est:,} tokens estimated)")
    if token_est > 90000 and not args.no_llm:
        print("WARNING: May exceed Groq daily token limit (100k)")
    print()

    trace_id_num = args.start_id
    for filepath, scen in plans:
        trace_id = f"trace_groq_{trace_id_num:03d}"
        filename = os.path.basename(filepath)

        print(f"  {trace_id} | {filename} | {scen.value} ...", end=" ", flush=True)

        trace = generate_trace(filepath, scen, trace_id, use_llm=not args.no_llm)

        out_path = os.path.join(DATASET_DIR, f"{trace_id}.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(trace, f, indent=2, ensure_ascii=False)

        print(f"-> {out_path}")
        trace_id_num += 1

    print(f"\nDone. Generated {len(plans)} trace(s) in {DATASET_DIR}/")
    print(f"Run: python run_eval.py --mock=false  to evaluate all traces")


if __name__ == "__main__":
    main()
