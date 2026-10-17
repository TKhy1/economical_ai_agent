"""Runtime configuration for the optional LLM-powered chat assistant.

Reads from real environment variables first, then falls back to a local `.env` file
(simple manual parser so the project doesn't need the `python-dotenv` dependency).
Without an API key configured, `POST /api/chat` automatically falls back to the
rule-based parser in agent.py — the app is fully functional either way.
"""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_dotenv(path):
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


_load_dotenv(os.path.join(BASE_DIR, ".env"))

AI_API_KEY = os.environ.get("AI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY") or ""
AI_MODEL = os.environ.get("AI_MODEL", "claude-sonnet-5")
