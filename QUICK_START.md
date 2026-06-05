# Quick Start: Free LLM Evaluator

## 🎯 TL;DR - Get Started in 5 Minutes

### Option 1: Use Mock (Instant, No Setup)
```bash
cd c:\Users\t-ashende\Documents\evaluator
python run_eval.py --mock
```
✅ Done. Works immediately, uses deterministic grading.

---

### Option 2: Use Ollama (Free, Recommended for Production)

#### Step 1: Install Ollama (3 min)
- Download: https://ollama.ai/download/windows
- Run installer
- Reboot if prompted

#### Step 2: Download Model (2 min)
```bash
ollama pull mistral
```

#### Step 3: Start Ollama (Terminal 1)
```bash
ollama serve
# Output: Listening on 127.0.0.1:11434
```
Keep this running.

#### Step 4: Run Evaluations (Terminal 2)
```bash
cd c:\Users\t-ashende\Documents\evaluator
python run_eval.py trace_001
```

**That's it!** The pipeline will automatically use Ollama.

---

### Option 3: Use Gemini (Free Tier, Alternative)

Already configured with your API key!

```bash
cd c:\Users\t-ashende\Documents\evaluator
python run_eval.py trace_001
```

It will try Gemini automatically. If Gemini fails, falls back to Ollama or Mock.

---

## 🚀 Run the Full Dashboard

### Terminal 1: Start Ollama (if using)
```bash
ollama serve
```

### Terminal 2: Start Flask API
```bash
cd c:\Users\t-ashende\Documents\evaluator\api
python app.py
# → http://localhost:5000
```

### Terminal 3: Start Vite Dev Server
```bash
cd c:\Users\t-ashende\Documents\evaluator\ui
npm run dev
# → http://localhost:3000
```

### Terminal 4: Open Browser
```
http://localhost:3000
```

Upload traces, view evaluations, see which LLM model was used!

---

## ✅ Verify Everything Works

```bash
# Check Ollama is running
curl http://localhost:11434/api/tags

# Check Gemini key is configured
python -c "import os; from dotenv import load_dotenv; load_dotenv(); print(os.getenv('GOOGLE_GEMINI_API_KEY')[:20])"

# Run a test eval
python run_eval.py trace_001

# Check which models were used
python -c "
import json
result = json.load(open('results/all_results.json'))[0]
for sr in result.get('step_results', []):
    print(f\"Step {sr['step_number']}: {sr.get('rubric',{}).get('_model')} / {sr.get('grade',{}).get('_model')}\")
"
```

---

## 🎁 Free vs Paid

| Mode | Cost | Setup | Speed | Quota |
|------|------|-------|-------|-------|
| Mock | $0 | 0 min | ⚡⚡⚡ Fast | ∞ Unlimited |
| Ollama | $0 | 5 min | ⚡⚡ Medium | ∞ Unlimited |
| Gemini | $0 | 0 min | ⚡⚡⚡ Fast | 60 req/min free |
| Claude | $15+ | Setup | ⚡⚡⚡ Fast | Quota limited |
| GPT-4o | $15+ | Setup | ⚡⚡⚡ Fast | Quota limited |

---

## 📊 What Gets Evaluated

Each trace gets:
- ✅ **Plan Score** (0-1.0) — agent plan quality
- ✅ **Step Scores** (5 steps × 3 criteria each)
- ✅ **Trajectory Score** (weighted average)
- ✅ **Failure Attribution** (root cause analysis)
- ✅ **Context Manifest** (fact tracking Excel→Word)

All visible in the dashboard with model attribution.

---

## 🔧 Troubleshooting

| Problem | Fix |
|---------|-----|
| "Cannot connect to Ollama" | Make sure `ollama serve` is running in another terminal |
| "Gemini API not found" | Your key may not have the right API enabled. Use Ollama instead. |
| Dashboard shows all "mock" models | Claude/OpenAI/Gemini all failed → Mock is fallback (still works!) |
| Slow evaluations | Mock mode is fastest. Ollama depends on your CPU. |
| "google.generativeai not installed" | Already installed. If not: `pip install google-generativeai` |

---

## 📝 What Changed From Before

- ❌ Removed: Hard dependency on Anthropic Claude
- ❌ Removed: Hard dependency on OpenAI GPT-4o
- ✅ Added: Gemini support (free tier)
- ✅ Added: Ollama support (local, free)
- ✅ Added: Intelligent fallback chain (Claude → GPT → Gemini → Ollama → Mock)
- ✅ Changed: `api/app.py` runs `mock=False` by default (attempts real LLM calls)

All evaluations still work, just with free LLMs or mock as fallback.

---

## 🎯 Recommended Setup

For best results:
1. **Install Ollama** (5 min, one-time)
2. **Start `ollama serve`** before running evaluations
3. **Run dashboard** (see above)
4. Use the UI to upload traces and evaluate

Cost: **$0**  
Quality: **LLM-based** (if Ollama running) or **mock-based** (fallback)  
Reliability: **100%** (always has a fallback)

---

## More Info

- `SETUP_FREE_LLM.md` — Detailed setup for Gemini + Ollama
- `FREE_LLM_ARCHITECTURE.md` — Technical architecture & fallback chain
- `INTEGRATION_SUMMARY.md` — What was changed

