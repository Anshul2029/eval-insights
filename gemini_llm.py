"""
gemini_llm.py - Google Gemini integration for rubric generation and grading.

Uses google-generativeai library to call Gemini API (free tier).
"""

import os
import json
from typing import Optional

def _get_gemini_model():
    """Lazy import to avoid import errors if not installed."""
    try:
        import google.generativeai as genai
        api_key = os.getenv("GOOGLE_GEMINI_API_KEY")
        if not api_key:
            return None
        genai.configure(api_key=api_key)
        # Use gemini-1.5-flash (free tier model)
        return genai.GenerativeModel("gemini-1.5-flash")
    except ImportError:
        print("    [gemini] google-generativeai not installed, skipping")
        return None
    except Exception as e:
        print(f"    [gemini] Error: {e}")
        return None


def generate_rubric_gemini(step: dict, user_prompt: str, source_data_summary: dict, dataset_file: str) -> Optional[dict]:
    """Generate rubric using Gemini."""
    model = _get_gemini_model()
    if not model:
        return None

    action_type = step.get("action_type", "unknown")
    
    prompt = f"""You are an expert evaluator. Generate a rubric for evaluating this agent step.

Action type: {action_type}
User prompt: {user_prompt}
Dataset: {dataset_file}
Source data: {json.dumps(source_data_summary, indent=2)[:500]}

Generate 3 evaluation criteria (C1, C2, C3) as a JSON object with this structure:
{{
  "criteria": [
    {{"id": "C1", "description": "..."}},
    {{"id": "C2", "description": "..."}},
    {{"id": "C3", "description": "..."}}
  ]
}}

Only output valid JSON."""

    try:
        response = model.generate_content(prompt)
        text = response.text
        
        # Extract JSON from response
        start = text.find('{')
        end = text.rfind('}') + 1
        if start >= 0 and end > start:
            json_str = text[start:end]
            rubric = json.loads(json_str)
            rubric["_model"] = "gemini"
            return rubric
    except Exception as e:
        print(f"    [gemini] Rubric generation failed: {e}")
    
    return None


def grade_step_gemini(step: dict, rubric: dict, prior_context: str, source_data_summary: dict) -> Optional[dict]:
    """Grade a step using Gemini."""
    model = _get_gemini_model()
    if not model:
        return None

    criteria = rubric.get("criteria", [])
    criteria_text = "\n".join([f"{c['id']}: {c['description']}" for c in criteria])
    key_facts = step.get("key_facts_produced", [])
    
    prompt = f"""You are an expert evaluator. Grade this step against the criteria.

Step: {step.get('action_type')}
Output: {step.get('output', '')[:300]}
Key facts: {json.dumps(key_facts)[:300]}

Criteria to evaluate:
{criteria_text}

For each criterion, assign a score (0.0-1.0) and indicate pass (>= 0.5).
Return a JSON object:
{{
  "criterion_grades": [
    {{"id": "C1", "pass": true, "score": 0.9, "rationale": "..."}},
    {{"id": "C2", "pass": true, "score": 0.9, "rationale": "..."}},
    {{"id": "C3", "pass": true, "score": 0.85, "rationale": "..."}}
  ]
}}

Only output valid JSON."""

    try:
        response = model.generate_content(prompt)
        text = response.text
        
        start = text.find('{')
        end = text.rfind('}') + 1
        if start >= 0 and end > start:
            json_str = text[start:end]
            grades = json.loads(json_str)
            
            # Calculate step score
            scores = [g.get("score", 0.0) for g in grades.get("criterion_grades", [])]
            step_score = sum(scores) / len(scores) if scores else 0.0
            step_pass = step_score >= 0.5
            
            return {
                "criterion_grades": grades.get("criterion_grades", []),
                "step_score": step_score,
                "step_pass": step_pass,
                "failure_type": None if step_pass else "reasoning_error",
                "_model": "gemini"
            }
    except Exception as e:
        print(f"    [gemini] Grading failed: {e}")
    
    return None
