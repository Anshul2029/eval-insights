# Free LLM Architecture

## Before
```
rubric_generator.py    grader.py
       |                  |
    Claude            GPT-4o-mini
       |                  |
    (Over quota)      (Over quota)
       ❌                 ❌
   FAILS                FAILS
```

## After
```
rubric_generator.py         grader.py
       |                       |
       ├─ Claude            ├─ GPT-4o
       │  (over quota)      │  (over quota)
       │       ↓             │       ↓
       ├─ GPT-4o-mini       ├─ Gemini
       │  (over quota)      │  (API key)
       │       ↓             │       ↓
       ├─ Gemini ✨         ├─ Ollama ✨
       │  (free tier)       │  (local)
       │       ↓             │       ↓
       ├─ Ollama ✨         ├─ Mock
       │  (local)           │  (deterministic)
       │       ↓             │       ↓
       └─ Mock              └─ (fallback)
          (deterministic)
```

## Execution Flow (mock=False)

```
pipeline.run_pipeline(trace, mock=False)
    ↓
    ├─ Plan Evaluation
    │   └─ plan_evaluator.evaluate_plan()
    │       └─ TRY: Claude → GPT-4o-mini → Mock
    │
    ├─ Per-Step Rubric Generation (5 steps)
    │   └─ rubric_generator.generate_rubric()
    │       └─ TRY: Claude → GPT-4o-mini → Gemini → Ollama → Mock
    │
    ├─ Per-Step Grading (5 steps)
    │   └─ grader._grade_with_quota_fallback()
    │       └─ TRY: GPT-4o → Gemini → Ollama → Mock
    │
    ├─ Context Check
    │   └─ context_checker.check_context() (deterministic)
    │
    └─ Aggregate & Return
        └─ Final result with model attribution
```

## Model Selection Priority

| Step | Provider | Status | Fallback |
|------|----------|--------|----------|
| 1 | Claude | ❌ No credit | → |
| 2 | GPT-4o-mini | ❌ Quota | → |
| 3 | Gemini | ⚠️ API key (valid but may need quota) | → |
| 4 | Ollama | ✅ Local (FREE) | → |
| 5 | Mock | ✅ Deterministic | STOP |

## Key Paths

### Path A: Ollama Available (RECOMMENDED)
```
generate_rubric()
  → Claude fails
  → GPT fails
  → Gemini fails
  → Ollama succeeds ✅
  → Returns {"_model": "ollama", "criteria": [...]}
```

### Path B: Ollama Not Available
```
generate_rubric()
  → Claude fails
  → GPT fails
  → Gemini fails
  → Ollama fails
  → Mock succeeds ✅
  → Returns {"_model": "mock", "criteria": [...]}
```

### Path C: Gemini Works (If API restored)
```
generate_rubric()
  → Claude fails
  → GPT fails
  → Gemini succeeds ✅
  → Returns {"_model": "gemini", "criteria": [...]}
```

## Cost Analysis

| Provider | Cost | Setup | Reliability |
|----------|------|-------|-------------|
| Claude | $0 (exhausted) | Key provided | ⚠️ |
| GPT-4o-mini | $0 (exhausted) | Key provided | ⚠️ |
| Gemini | FREE tier: 60/min | 2 min setup | 🟡 (if quota ok) |
| Ollama | FREE | 5 min setup | ✅ (local, no limits) |
| Mock | FREE | 0 setup | ✅ (always works) |

## Deployment Scenarios

### Scenario 1: Dev (Ollama)
```bash
# Terminal 1
ollama serve

# Terminal 2
python run_eval.py trace_001
```
- ✅ Free
- ✅ No quotas
- ✅ Private (runs locally)
- ⚠️ Slower than cloud (depends on hardware)

### Scenario 2: Cloud with Fallback
```bash
# AWS Lambda / Railway / etc.
python run_eval.py
```
- Falls back through chain: Claude → GPT → Gemini → Ollama → Mock
- If Gemini API key is valid: uses Gemini (60 req/min free)
- If not: uses Mock (always available)
- Cost: $0 or whatever Gemini quota costs

### Scenario 3: Dashboard
```bash
# Terminal 1: Ollama
ollama serve

# Terminal 2: Flask
cd api && python app.py

# Terminal 3: Vite
cd ui && npm run dev

# Terminal 4: Browser
http://localhost:3000
```
- All `/evaluate` endpoint calls use the free LLM chain
- Results stored in `results/all_results.json`
- Models shown in dashboard: `"_model": "ollama"` or `"_model": "gemini"` or `"_model": "mock"`

---

## Code Example: Fallback Chain

```python
# rubric_generator.py
def generate_rubric(step, user_prompt, source_data_summary, dataset_file):
    # Try Claude
    try:
        return call_claude(...)  # ❌ Fails: no credit
    except ...
    
    # Try GPT-4o-mini
    try:
        return call_openai(...)  # ❌ Fails: quota exceeded
    except ...
    
    # Try Gemini
    try:
        import gemini_llm
        rubric = gemini_llm.generate_rubric_gemini(...)
        if rubric:
            return rubric  # ✅ Or ❌ if API key invalid
    except ...
    
    # Try Ollama
    try:
        import ollama_llm
        rubric = ollama_llm.generate_rubric_ollama(...)
        if rubric:
            return rubric  # ✅ Success (if Ollama running)
    except ...
    
    # Fallback to Mock
    import mock_llm
    return mock_llm.generate_mock_rubric(...)  # ✅ Always succeeds
```

---

## Status Check Commands

```bash
# Check Ollama
curl http://localhost:11434/api/tags

# Check Gemini key is set
python -c "import os; from dotenv import load_dotenv; load_dotenv(); print('Gemini key:', 'SET' if os.getenv('GOOGLE_GEMINI_API_KEY') else 'MISSING')"

# Run test evaluation (will use fallback chain automatically)
python run_eval.py trace_001

# Check which models were used in results
python -c "import json; r = json.load(open('results/all_results.json')); [print(f\"Step {s['step_number']}: {s.get('rubric',{}).get('_model')}/{s.get('grade',{}).get('_model')}\") for r in r for s in r.get('step_results', [])]"
```

