"""Optional LLM layer (Anthropic or OpenAI-compatible) with strict guards.

The bot is fully functional without an LLM. When a key is configured, the LLM is only used to answer
questions the FAQ matcher couldn't place — and only from the FAQ text. Every output is validated
(no links, no numbers that aren't in the FAQ, no promise words); on any failure the bot falls back to
"I don't want to guess — a representative can help".
"""
from __future__ import annotations

import json
import os
import re
import urllib.request

from .kb import KB

PROVIDER = os.getenv("LLM_PROVIDER", "").lower()
API_KEY = os.getenv("LLM_API_KEY") or os.getenv("ANTHROPIC_API_KEY") or os.getenv("OPENAI_API_KEY") or ""
if not PROVIDER and API_KEY:
    PROVIDER = "anthropic" if (os.getenv("ANTHROPIC_API_KEY") or API_KEY.startswith("sk-ant")) else "openai"
MODEL = os.getenv("LLM_MODEL") or ("claude-sonnet-5" if PROVIDER == "anthropic" else "gpt-4o-mini")
BASE_URL = os.getenv("LLM_BASE_URL", "")  # for OpenAI-compatible hosts (Groq, OpenRouter, DeepSeek…)
TIMEOUT = float(os.getenv("LLM_TIMEOUT", "9"))

TABOO = ["guaranteed", "guarantee you", "100%", "assured placement", "sure-shot", "sure shot", "definitely get"]
NO_ANSWER = "NO_ANSWER"


def enabled() -> bool:
    return bool(API_KEY) and PROVIDER in ("anthropic", "openai") and os.getenv("LLM_DISABLED") != "1"


def model_label() -> str:
    return MODEL if enabled() else "deterministic FAQ matcher (no LLM)"


def _call(system: str, user: str, max_tokens=400) -> str:
    if PROVIDER == "anthropic":
        req = urllib.request.Request(
            (BASE_URL or "https://api.anthropic.com") + "/v1/messages",
            data=json.dumps({"model": MODEL, "max_tokens": max_tokens, "temperature": 0,
                             "system": system, "messages": [{"role": "user", "content": user}]}).encode(),
            headers={"x-api-key": API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            data = json.loads(r.read())
        return "".join(b.get("text", "") for b in data.get("content", []))
    req = urllib.request.Request(
        (BASE_URL or "https://api.openai.com") + "/v1/chat/completions",
        data=json.dumps({"model": MODEL, "max_tokens": max_tokens, "temperature": 0,
                         "messages": [{"role": "system", "content": system},
                                      {"role": "user", "content": user}]}).encode(),
        headers={"Authorization": f"Bearer {API_KEY}", "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        data = json.loads(r.read())
    return data["choices"][0]["message"]["content"]


NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _numbers(s: str) -> set[str]:
    return {n.replace(",", "").rstrip(".") for n in NUM_RE.findall(s or "")}


def validate(text: str, grounding: str) -> str | None:
    if not text:
        return None
    text = text.strip().strip('"').strip()
    if not text or NO_ANSWER in text or len(text) > 900:
        return None
    if re.search(r"https?://|www\.|\.com\b|\.in\b", text, re.I):
        return None
    low = text.lower()
    if any(t in low for t in TABOO):
        return None
    allowed = _numbers(grounding)
    if any(n not in allowed and len(n) > 1 for n in _numbers(text)):
        return None
    return text


SYSTEM = """You are the WhatsApp career-counselling assistant for Lakshay India.
Rules:
- Answer ONLY from the FAQ provided. Never invent fees, durations, addresses, phone numbers, websites, names, dates or results.
- Never promise jobs, salaries or success.
- If the FAQ does not answer the question, reply with exactly NO_ANSWER.
- Warm, simple, WhatsApp-short (under ~80 words). No links. No preamble.
- If asked to write in Hindi-English, write natural Hinglish (Roman script); otherwise English.
- End with one short next step (e.g. reply YES to request a counselling session).
Output ONLY the message text."""


def answer(question: str, conv, visitor: dict | None, hinglish: bool) -> str | None:
    if not enabled():
        return None
    faq = [{"q": f["question"], "a": f["answer"]} for f in KB.faqs]
    history = [{"from": "assistant", "body": b} for b in conv.bot_bodies[-4:]]
    user = (f"Language: {'Hinglish' if hinglish else 'English'}\n"
            f"FAQ (the only facts you may use): {json.dumps(faq, ensure_ascii=False)}\n"
            f"Visitor profile: {json.dumps({k: (visitor or {}).get(k) for k in ('identity', 'stage_label', 'interests')}, ensure_ascii=False)}\n"
            f"Recent messages: {json.dumps(history, ensure_ascii=False)}\n\n"
            f"Visitor's latest message: {question}")
    try:
        out = _call(SYSTEM, user)
    except Exception:
        return None
    return validate(out, KB.all_text() + question)
