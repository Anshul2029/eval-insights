"""
ollama_llm.py - Ollama integration for rubric generation and grading.

Uses Ollama API running locally (http://localhost:11434).
Models: mistral, neural-chat, etc.
"""

import os
import json
from typing import Optional
import requests
import time

OLLAMA_BASE = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "mistral")  # Can be "neural-chat", "mistral", etc.
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "10"))
OLLAMA_RETRIES = int(os.getenv("OLLAMA_RETRIES", "1"))


def _check_ollama() -> bool:
    """Check if Ollama is running."""
    try:
        resp = requests.get(f"{OLLAMA_BASE}/api/tags", timeout=2)
        return resp.status_code == 200
    except Exception:
        return False


def _call_ollama(prompt: str) -> Optional[str]:
    """Call Ollama API with retries and backoff. Returns response text or None.
    Adds robust logging for why a call failed so higher-level callers can decide.
    """
    backoff = 1.0
    for attempt in range(1, OLLAMA_RETRIES + 1):
        try:
            response = requests.post(
                f"{OLLAMA_BASE}/api/generate",
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": prompt,
                    "stream": False,
                },
                timeout=OLLAMA_TIMEOUT,
            )
            if response.status_code == 200:
                try:
                    result = response.json()
                except Exception:
                    text = response.text if hasattr(response, "text") else ""
                    print(f"    [ollama] JSON decode failed on attempt {attempt}; raw response: {text[:500]}")
                    return None
                # Ollama may return structured payloads; prefer 'response' field
                if isinstance(result, dict) and "response" in result:
                    return result.get("response", "")
                # Fallback: if API directly returns text
                if isinstance(result, str):
                    return result
                # Unexpected structure
                print(f"    [ollama] Unexpected JSON structure on attempt {attempt}: keys={list(result.keys())}")
                return None
            else:
                text = response.text[:500] if hasattr(response, "text") else ""
                print(f"    [ollama] HTTP {response.status_code} on attempt {attempt}; body: {text}")
        except requests.exceptions.Timeout:
            print(f"    [ollama] Timeout on attempt {attempt} (timeout={OLLAMA_TIMEOUT}s)")
        except requests.exceptions.ConnectionError as ce:
            print(f"    [ollama] Connection error on attempt {attempt}: {ce}")
        except Exception as e:
            print(f"    [ollama] Error on attempt {attempt}: {e}")

        # backoff before retrying
        if attempt < OLLAMA_RETRIES:
            time.sleep(backoff)
            backoff *= 2
    print(f"    [ollama] All {OLLAMA_RETRIES} attempts failed; falling back")
    return None


def generate_rubric_ollama(step: dict, user_prompt: str, source_data_summary: dict, dataset_file: str) -> Optional[dict]:
    """Generate rubric using Ollama."""
    if not _check_ollama():
        return None

    action_type = step.get("action_type", "unknown")
    
    prompt = f"""Generate a JSON rubric for evaluating an agent step.

Action: {action_type}
User prompt: {user_prompt}
Dataset: {dataset_file}

Create 3 criteria (C1, C2, C3). Return ONLY valid JSON:
{{
  "criteria": [
    {{"id": "C1", "description": "criterion 1"}},
    {{"id": "C2", "description": "criterion 2"}},
    {{"id": "C3", "description": "criterion 3"}}
  ]
}}"""

    text = _call_ollama(prompt)
    if not text:
        return None

    try:
        # Extract JSON
        start = text.find('{')
        end = text.rfind('}') + 1
        if start >= 0 and end > start:
            json_str = text[start:end]
            rubric = json.loads(json_str)
            rubric["_model"] = "ollama"
            return rubric
    except Exception as e:
        print(f"    [ollama] JSON parse error: {e}")
    
    return None


def grade_step_ollama(step: dict, rubric: dict, prior_context: str, source_data_summary: dict) -> Optional[dict]:
    """Grade a step using Ollama."""
    if not _check_ollama():
        return None

    criteria = rubric.get("criteria", [])
    criteria_text = "\n".join([f"{c['id']}: {c['description']}" for c in criteria])
    key_facts = step.get("key_facts_produced", [])
    
    prompt = f"""Grade this step against the criteria. Return ONLY JSON.

Step type: {step.get('action_type')}
Key facts: {json.dumps(key_facts)[:200]}

Criteria:
{criteria_text}

For each criterion, give score (0.0-1.0) and pass (true if >= 0.5).
Return ONLY JSON:
{{
  "criterion_grades": [
    {{"id": "C1", "pass": true, "score": 0.9, "rationale": "ok"}},
    {{"id": "C2", "pass": true, "score": 0.9, "rationale": "ok"}},
    {{"id": "C3", "pass": true, "score": 0.85, "rationale": "ok"}}
  ]
}}"""

    text = _call_ollama(prompt)
    if not text:
        return None

    try:
        start = text.find('{')
        end = text.rfind('}') + 1
        if start >= 0 and end > start:
            json_str = text[start:end]
            grades = json.loads(json_str)
            
            scores = [g.get("score", 0.0) for g in grades.get("criterion_grades", [])]
            step_score = sum(scores) / len(scores) if scores else 0.0
            any_zero = any(s == 0 for s in scores)
            step_pass = (step_score >= 0.5) and not any_zero
            failure_type = None
            if not step_pass:
                failure_type = "computation_error" if any_zero else "reasoning_error"
            
            return {
                "criterion_grades": grades.get("criterion_grades", []),
                "step_score": round(step_score, 4),
                "step_pass": step_pass,
                "failure_type": failure_type,
                "_model": "ollama"
            }
    except Exception as e:
        print(f"    [ollama] JSON parse error: {e}")
    
    return None
