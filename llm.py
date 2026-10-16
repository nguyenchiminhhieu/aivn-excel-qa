"""LLM access: Qwen3.7 Flash through OpenRouter (OpenAI-compatible API).

Configuration comes only from environment variables or a `.env` file (never from source code):
    OPENROUTER_API_KEY   required
    QA_MODEL             default qwen/qwen3.7-flash
    OPENROUTER_BASE_URL  default https://openrouter.ai/api/v1
    QA_CALL_TIMEOUT      seconds per request, default 90
"""
from __future__ import annotations

import os
import re
import time
from collections import Counter
from pathlib import Path

from openai import OpenAI


def _load_dotenv():
    """Read KEY=VALUE lines from .env (current directory, then the project folder); real env vars win."""
    for p in (Path.cwd() / ".env", Path(__file__).resolve().parent / ".env"):
        if p.is_file():
            for k, v in re.findall(r'^\s*([A-Za-z_]\w*)\s*=\s*"?([^"\n]*)"?', p.read_text(encoding="utf-8"), re.M):
                os.environ.setdefault(k, v.strip())


_load_dotenv()
MODEL = os.environ.get("QA_MODEL", "qwen/qwen3.7-flash")
_NO_TEMPERATURE: set = set()          # (model, effort) pairs that reject a custom temperature


def make_client() -> OpenAI:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise SystemExit("OPENROUTER_API_KEY is not set. Put it in the environment or in a .env file "
                         "(see .env.example).")
    return OpenAI(base_url=os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"), api_key=key,
                  timeout=float(os.environ.get("QA_CALL_TIMEOUT", "90")), max_retries=2)


def llm_call(client: OpenAI, messages, temperature: float, effort: str, usage: Counter) -> str:
    """One chat completion. `effort` is OpenRouter's reasoning effort: none | low | medium | high."""
    kw = {"model": MODEL, "messages": messages, "max_tokens": 6000,
          "extra_body": {"reasoning": {"effort": effort}}}
    if (MODEL, effort) not in _NO_TEMPERATURE:
        kw["temperature"] = temperature
    for attempt in range(4):
        try:
            r = client.chat.completions.create(**kw)
            break
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "temperature" in msg and "temperature" in kw:
                _NO_TEMPERATURE.add((MODEL, effort))
                kw.pop("temperature")
                continue
            if attempt == 3 or not re.search(r"rate|timeout|overload|5\d\d|connection", msg, re.I):
                raise
            time.sleep(2 * (attempt + 1))
    if r.usage:
        usage["prompt"] += r.usage.prompt_tokens
        usage["completion"] += r.usage.completion_tokens
        usage["calls"] += 1
    return r.choices[0].message.content or ""


def extract_code(text: str) -> str | None:
    """Last fenced code block of a reply."""
    m = re.findall(r"```(?:python)?\s*\n(.*?)```", text or "", re.S)
    return m[-1] if m else None
