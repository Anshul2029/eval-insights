"""
llm_provider.py — Unified LLM dispatcher: Groq → Gemini → Ollama.

Caches rate-limit status to avoid hammering exhausted APIs.
"""

import os
import re
import time

from dotenv import load_dotenv
load_dotenv()
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

if os.environ.get("GOOGLE_GEMINI_API_KEY") and not os.environ.get("GEMINI_API_KEY"):
    os.environ["GEMINI_API_KEY"] = os.environ["GOOGLE_GEMINI_API_KEY"]

_OLLAMA_BASE = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
_OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "mistral")

_groq_client = None
_active_provider = None

_groq_blocked_until = 0
_gemini_blocked_until = 0
_ollama_blocked_until = 0


def _get_groq():
    global _groq_client
    if _groq_client is None:
        from groq import Groq
        _groq_client = Groq(api_key=os.environ["GROQ_API_KEY"])
    return _groq_client


_last_groq_call = 0
_GROQ_MIN_INTERVAL = 2.5


def call_llm(system: str, user: str, max_tokens: int = 2000, temp: float = 0.0) -> str:
    global _active_provider, _groq_blocked_until, _gemini_blocked_until, _ollama_blocked_until, _last_groq_call

    now = time.time()

    groq_key = os.environ.get("GROQ_API_KEY", "")
    if groq_key and now > _groq_blocked_until:
        wait = _GROQ_MIN_INTERVAL - (now - _last_groq_call)
        if wait > 0:
            time.sleep(wait)
        try:
            _last_groq_call = time.time()
            client = _get_groq()
            r = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                temperature=temp,
                max_tokens=max_tokens,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
            text = r.choices[0].message.content or ""
            text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
            if text:
                _active_provider = "groq/llama-3.3-70b"
                return text
        except Exception as e:
            err_str = str(e)
            if "429" in err_str or "rate_limit" in err_str:
                _groq_blocked_until = now + 65
                print(f"    [llm_provider] Groq rate-limited, retrying in ~1min")
            else:
                print(f"    [llm_provider] Groq error: {err_str[:100]}")

    gem_key = os.environ.get("GEMINI_API_KEY", "")
    if gem_key and now > _gemini_blocked_until:
        try:
            import google.generativeai as genai
            genai.configure(api_key=gem_key)
            model = genai.GenerativeModel("gemini-2.5-flash")
            resp = model.generate_content(f"{system}\n\n{user}")
            if resp and resp.text:
                _active_provider = "gemini/gemini-2.5-flash"
                return resp.text.strip()
        except Exception as e:
            err_str = str(e)
            if "429" in err_str or "quota" in err_str.lower():
                _gemini_blocked_until = now + 300
                print(f"    [llm_provider] Gemini rate-limited, skipping for 5min")
            else:
                print(f"    [llm_provider] Gemini error: {err_str[:100]}")

    if now > _ollama_blocked_until:
        try:
            import requests
            r = requests.post(
                f"{_OLLAMA_BASE}/api/chat",
                json={
                    "model": _OLLAMA_MODEL,
                    "stream": False,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
                timeout=5,
            )
            if r.status_code == 200:
                text = r.json().get("message", {}).get("content", "")
                if text:
                    _active_provider = f"ollama/{_OLLAMA_MODEL}"
                    return text
        except Exception:
            _ollama_blocked_until = now + 300

    _active_provider = None
    return ""


def get_active_provider() -> str:
    return _active_provider or "none"


def is_llm_available() -> bool:
    """Quick check if any LLM provider might be available (no actual call)."""
    now = time.time()
    if os.environ.get("GROQ_API_KEY") and now > _groq_blocked_until:
        return True
    if os.environ.get("GEMINI_API_KEY") and now > _gemini_blocked_until:
        return True
    if now > _ollama_blocked_until:
        return True
    return False
