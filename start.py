"""
Unified launcher for the Evaluator platform.

Starts:
  1. Flask API        on port 5001
  2. Streamlit        on port 8502  (baseUrlPath = /insights)
  3. Vite dev server  on port 8080  (optional, pass --dev)

Usage:
    python start.py          # production — Flask serves React build
    python start.py --dev    # development — Vite dev server + HMR
"""

import os
import sys
import signal
import subprocess
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
INSIGHTS_DIR = os.path.join(ROOT, "eval_insights")
procs = []


def _spawn(label, cmd, **kwargs):
    print(f"[start] {label}: {' '.join(cmd)}")
    if sys.platform == "win32":
        kwargs.setdefault("shell", True)
    p = subprocess.Popen(cmd, **kwargs)
    procs.append((label, p))
    return p


def cleanup(*_):
    for label, p in procs:
        if p.poll() is None:
            print(f"[start] stopping {label} (pid {p.pid})")
            p.terminate()
    for _, p in procs:
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()
    sys.exit(0)


def main():
    dev_mode = "--dev" in sys.argv

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)
    if sys.platform == "win32":
        signal.signal(signal.SIGBREAK, cleanup)

    env_flask = os.environ.copy()
    if not dev_mode:
        env_flask["FLASK_SERVE_STATIC"] = "1"
    _spawn("flask", [sys.executable, "-m", "api.app"], cwd=ROOT, env=env_flask)

    _spawn("streamlit", [
        sys.executable, "-m", "streamlit", "run", "app.py",
        "--server.port=8502",
        "--server.baseUrlPath=insights",
        "--server.headless=true",
        "--server.enableCORS=false",
        "--server.enableXsrfProtection=false",
    ], cwd=INSIGHTS_DIR)

    if dev_mode:
        _spawn("vite", ["npx", "vite"], cwd=os.path.join(ROOT, "ui"))

    time.sleep(2)
    port = 8080 if dev_mode else 5001
    print(f"\n[start] Ready — open http://localhost:{port}")
    print("[start] Press Ctrl+C to stop all services\n")

    while True:
        for label, p in procs:
            ret = p.poll()
            if ret is not None:
                print(f"[start] {label} exited with code {ret}")
                if label == "flask":
                    cleanup()
        time.sleep(1)


if __name__ == "__main__":
    main()
