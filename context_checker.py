"""
context_checker.py — Pure Python, no LLM.

Reads context_manifest from a trace and computes:
  - context_loss_detected (bool)
  - facts_lost (list)
  - facts_produced (list)
  - score (0-1): (facts_produced - facts_lost) / facts_produced
"""


def check_context(trace: dict) -> dict:
    manifest = trace.get("context_manifest", {})

    facts_produced = manifest.get("facts_produced_in_excel_step2", [])
    facts_lost = manifest.get("facts_lost_at_boundary", [])
    facts_present_in_word = manifest.get("facts_present_in_word_output", [])
    context_loss_detected = manifest.get("context_loss_detected", False)
    boundary = manifest.get("boundary", "Excel→Word (Step 3)")

    n_produced = len(facts_produced)
    n_lost = len(facts_lost)

    if n_produced == 0:
        score = 1.0
    else:
        score = (n_produced - n_lost) / n_produced

    return {
        "context_loss_detected": context_loss_detected,
        "facts_produced": facts_produced,
        "facts_present_in_word": facts_present_in_word,
        "facts_lost": facts_lost,
        "boundary": boundary,
        "n_produced": n_produced,
        "n_lost": n_lost,
        "score": round(score, 4),
        "note": manifest.get("note", ""),
    }
