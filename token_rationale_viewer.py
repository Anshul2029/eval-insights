"""
token_rationale_viewer.py — Display token consumption rationale per step.

Shows WHY each step consumed tokens:
- What was in the prompt
- What was computed
- Why the LLM was called at that point
"""

import json


def print_token_rationale(token_rationale: dict, token_per_step: list):
    """
    Print detailed rationale for token usage at each step.
    
    Args:
        token_rationale: Dict from agent_runner_real output
        token_per_step: List of per-step token dicts
    """
    print("\n" + "=" * 80)
    print("  TOKEN CONSUMPTION RATIONALE")
    print("=" * 80)
    
    for step_data in token_per_step:
        step_num = step_data["step_number"]
        action = step_data["action_type"]
        inp_toks = step_data["input_tokens"]
        out_toks = step_data["output_tokens"]
        think_toks = step_data["thinking_tokens"]
        total_toks = step_data["total_tokens"]
        
        rationale = token_rationale.get(step_num, {})
        
        print(f"\n  Step {step_num}: {action.upper()}")
        print(f"  {'-' * 76}")
        print(f"  Input tokens:     {inp_toks:>6}  |  Output tokens:     {out_toks:>6}")
        print(f"  Thinking tokens:  {think_toks:>6}  |  Total tokens:      {total_toks:>6}")
        
        if rationale:
            print(f"\n  Why tokens were used:")
            reason = rationale.get("reason", "N/A")
            print(f"    • {reason}")
            
            if "data_points" in rationale:
                print(f"    • Data processed: {rationale['data_points']}")
            
            if "computation" in rationale:
                print(f"    • Computation: {rationale['computation']}")
            
            if "facts_handled" in rationale:
                print(f"    • Facts handled: {rationale['facts_handled']}")
            
            if "sections_designed" in rationale:
                print(f"    • Sections designed: {rationale['sections_designed']}")
            
            if "document_generated" in rationale:
                print(f"    • Document generated: {rationale['document_generated']}")
            
            llm_rationale = rationale.get("rationale", {})
            if llm_rationale:
                model = llm_rationale.get("model")
                if model:
                    print(f"    • Model: {model}")
                
                if "system_len" in llm_rationale:
                    print(f"    • System prompt: {llm_rationale['system_len']} bytes")
                if "user_len" in llm_rationale:
                    print(f"    • User prompt: {llm_rationale['user_len']} bytes")
        
        print()


def export_rationale_json(token_rationale: dict, token_per_step: list, output_file: str):
    """Export token rationale as JSON for further analysis."""
    
    rationale_report = {
        "summary": {
            "total_steps": len(token_per_step),
            "total_tokens": sum(s["total_tokens"] for s in token_per_step),
        },
        "per_step": []
    }
    
    for step_data in token_per_step:
        step_num = step_data["step_number"]
        step_rationale = token_rationale.get(step_num, {})
        
        rationale_report["per_step"].append({
            "step_number": step_num,
            "action_type": step_data["action_type"],
            "tokens": {
                "input": step_data["input_tokens"],
                "output": step_data["output_tokens"],
                "thinking": step_data["thinking_tokens"],
                "total": step_data["total_tokens"],
            },
            "rationale": step_rationale,
        })
    
    with open(output_file, "w") as f:
        json.dump(rationale_report, f, indent=2, default=str)
    
    print(f"\n  Token rationale exported to: {output_file}")


if __name__ == "__main__":
    # Example usage
    sample_rationale = {
        1: {"reason": "Data validation", "data_points": "1000 rows × 10 cols", "rationale": {"model": "groq"}},
        2: {"reason": "Statistical analysis", "computation": "10 columns, 50 anomalies", "rationale": {}},
    }
    
    sample_tokens = [
        {"step_number": 1, "action_type": "data_parsing", "input_tokens": 200, "output_tokens": 150, "thinking_tokens": 0, "total_tokens": 350},
        {"step_number": 2, "action_type": "computation", "input_tokens": 300, "output_tokens": 250, "thinking_tokens": 100, "total_tokens": 650},
    ]
    
    print_token_rationale(sample_rationale, sample_tokens)
