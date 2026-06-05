# Free LLM Setup Guide

The evaluator now supports **3 free LLM options** with automatic fallback:

## LLM Priority Chain
1. **Claude (Anthropic)** — best quality
2. **GPT-4o-mini (OpenAI)** — fallback
3. **Gemini (Google)** — free tier (60 req/min) ✨
4. **Ollama (Local)** — completely free, no quotas ✨
5. **Mock** — fallback only, deterministic

---

## Setup: Gemini (Easiest, Recommended)

### 1. Get Gemini API Key
1. Go to https://ai.google.dev
2. Click "Get API Key" → "Create API key in new project"
3. Copy the key (format: `AIza...`)

### 2. Update `.env`
```
GOOGLE_GEMINI_API_KEY=AIza...
```

### 3. Install dependency
```bash
pip install google-generativeai>=0.3.0
```

**Done!** Run `python run_eval.py` — it will use Gemini automatically if Claude/OpenAI fail.

---

## Setup: Ollama (Best for Privacy/Cost, Requires Local Install)

### 1. Install Ollama
- Download from https://ollama.ai (Windows/Mac/Linux)
- Run the installer

### 2. Download a Model
```bash
ollama pull mistral
# or: ollama pull neural-chat, llama2, etc.
```

### 3. Start Ollama
```bash
ollama serve
# Runs on http://localhost:11434 (check .env for OLLAMA_BASE_URL)
```

### 4. Keep it Running
Leave Ollama running in a terminal while evaluating.

**Done!** When other LLMs fail, pipeline will call Ollama.

---

## Verify Setup

### Test Gemini
```bash
python -c "
import os
from dotenv import load_dotenv
load_dotenv()
import google.generativeai as genai
genai.configure(api_key=os.getenv('GOOGLE_GEMINI_API_KEY'))
model = genai.GenerativeModel('gemini-pro')
response = model.generate_content('Say hello')
print('Gemini working:', response.text[:50])
"
```

### Test Ollama
```bash
python -c "
import requests
r = requests.get('http://localhost:11434/api/tags')
print('Ollama running:', r.status_code == 200)
"
```

---

## How to Run

### Terminal 1: Ollama (if using)
```bash
ollama serve
```

### Terminal 2: Flask API
```bash
cd api
python app.py
# http://localhost:5000
```

### Terminal 3: Vite Dev Server
```bash
cd ui
npm run dev
# http://localhost:3000
```

### Terminal 4: Run Evaluation
```bash
# Single trace
python run_eval.py trace_001

# All traces
python run_eval.py

# Mock mode (no LLM calls)
python run_eval.py --mock
```

---

## Expected Output

When running evaluations, you'll see:
```
[rubric] Claude credits exhausted, falling back to gpt-4o-mini
[rubric] OpenAI quota exceeded, trying Gemini...
[rubric] Gemini working... (or Ollama working...)
```

All rubrics will show `"_model": "gemini"` or `"_model": "ollama"` in the result JSON.

---

## Notes

- **Gemini**: Free tier = 60 requests/min. Perfect for dev.
- **Ollama**: No quotas, runs on your machine. Best for production/heavy use.
- **Both together**: Run Ollama in background; Gemini is used first (faster), Ollama as backup.
- **Mock mode**: `python run_eval.py --mock` uses deterministic rules, zero cost.

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `ModuleNotFoundError: No module named 'google.generativeai'` | `pip install google-generativeai` |
| `Ollama Cannot connect to http://localhost:11434` | Make sure `ollama serve` is running in another terminal |
| Gemini says "quota exceeded" | Your free tier limit reached. Wait 1 hour or use Ollama. |
| Ollama generates random text | Try a different model: `ollama pull neural-chat` |

