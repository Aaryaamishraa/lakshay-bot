"""Lakshay India chatbot — HTTP API.

Author: Pranav Yadav (B.Tech Mathematics & Computing, DTU)

GET  /v1/healthz   GET /v1/metadata   GET /v1/faq   GET /v1/leads
POST /v1/context   POST /v1/start     POST /v1/tick   POST /v1/reply   POST /v1/teardown
"""
from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import llm
from .kb import KB
from .replies import handle_reply, start_conversation
from .store import SCOPES, STORE
from .util import parse_dt

app = FastAPI(title="Lakshay India chatbot", version="1.0.0")

from .playground import router as _playground_router  # noqa: E402
app.include_router(_playground_router)

MAX_ACTIONS = 20


def _now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


async def _json(req: Request):
    try:
        data = await req.json()
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _action(conv, c) -> dict:
    return {"conversation_id": conv.conversation_id, "visitor_id": conv.visitor_id, "trigger_id": conv.trigger_id,
            "send_as": c["send_as"], "template_name": c["template_name"], "template_params": c["template_params"],
            "body": c["body"], "cta": c["cta"], "suppression_key": c["suppression_key"], "rationale": c["rationale"]}


# ------------------------------------------------------------------ health / metadata / faq
@app.get("/v1/healthz")
async def healthz():
    return {"status": "ok", "uptime_seconds": int(time.time() - STORE.started), "faqs_loaded": len(KB.faqs),
            "contexts_loaded": STORE.counts()}


@app.get("/v1/metadata")
async def metadata():
    members = [m.strip() for m in os.getenv("TEAM_MEMBERS", "Pranav Yadav").split(",") if m.strip()]
    return {
        "bot_name": "Pranav Yadav DTU Lakshay India Chatbot",
        "team_name": os.getenv("TEAM_NAME", "Pranav Yadav (DTU)"),
        "team_members": members,
        "model": llm.model_label(),
        "approach": ("FAQ knowledge base (20 Lakshay India answers, English + Hinglish) with keyword/overlap matching so every "
                     "answer is word-for-word from the source; conversation engine with auto-reply detection, yes->action switch "
                     "into a short counselling-request flow that only asks for missing details, hostile/off-topic handling, "
                     "per-turn Hinglish detection and a polite goodbye; proactive openers for enquiries, reminders and "
                     "follow-ups; optional LLM fallback validated against the FAQ."),
        "contact_email": os.getenv("CONTACT_EMAIL", "pranavyadav9004@gmail.com"),
        "version": "1.0.0",
    }


@app.get("/v1/faq")
async def faq():
    return {"company": KB.company, "sections": KB.sections,
            "faqs": [{k: f[k] for k in ("id", "section", "question", "answer")} for f in KB.faqs]}


@app.get("/v1/leads")
async def leads():
    return {"leads": STORE.leads}


# ------------------------------------------------------------------ context
@app.post("/v1/context")
async def push_context(req: Request):
    b = await _json(req)
    if b is None:
        return JSONResponse(status_code=400, content={"accepted": False, "reason": "malformed_json", "details": "body must be a JSON object"})
    scope, cid, ver, payload = b.get("scope"), b.get("context_id"), b.get("version"), b.get("payload")
    if scope not in SCOPES:
        return JSONResponse(status_code=400, content={"accepted": False, "reason": "invalid_scope", "details": f"scope must be one of {list(SCOPES)}"})
    if not isinstance(cid, str) or not cid:
        return JSONResponse(status_code=400, content={"accepted": False, "reason": "invalid_context_id", "details": "context_id required"})
    try:
        ver = int(ver)
    except (TypeError, ValueError):
        return JSONResponse(status_code=400, content={"accepted": False, "reason": "invalid_version", "details": "version must be an integer"})
    if not isinstance(payload, dict):
        return JSONResponse(status_code=400, content={"accepted": False, "reason": "invalid_payload", "details": "payload must be an object"})
    ok, cur = STORE.put(scope, cid, ver, payload)
    if not ok:
        return JSONResponse(status_code=409, content={"accepted": False, "reason": "stale_version", "current_version": cur})
    return {"accepted": True, "ack_id": f"ack_{cid}_v{ver}", "stored_at": _now_iso()}


# ------------------------------------------------------------------ start (visitor opens the chat)
@app.post("/v1/start")
async def start(req: Request):
    b = await _json(req) or {}
    vid, tid = b.get("visitor_id"), b.get("trigger_id")
    trg = STORE.get("trigger", tid) if tid else None
    if tid and not trg:
        return JSONResponse(status_code=400, content={"error": "unknown trigger_id"})
    conv_id = b.get("conversation_id") or f"conv_{uuid.uuid4().hex[:10]}"
    if conv_id in STORE.convs:
        return JSONResponse(status_code=409, content={"error": "conversation_id already exists"})
    conv, c = start_conversation(conv_id, vid or (trg or {}).get("visitor_id"), trg)
    return _action(conv, c)


# ------------------------------------------------------------------ tick (proactive follow-ups)
@app.post("/v1/tick")
async def tick(req: Request):
    b = await _json(req) or {}
    now = parse_dt(b.get("now")) or datetime.now(timezone.utc)
    ids = b.get("available_triggers") or []
    if not isinstance(ids, list):
        ids = []
    cands, seen = [], set()
    for tid in ids:
        trg = STORE.get("trigger", tid)
        if not trg:
            continue
        real_id = trg.get("id") or tid
        if real_id in seen or real_id in STORE.sent_triggers:
            continue
        seen.add(real_id)
        sk = trg.get("suppression_key")
        if sk and sk in STORE.used_suppression:
            continue
        fl = STORE.flag(trg.get("visitor_id"))
        if fl.opted_out:
            continue
        bu = parse_dt(fl.backoff_until)
        if bu and bu > now and (trg.get("urgency") or 0) < 5:
            continue
        cands.append(trg)
    cands.sort(key=lambda t: (-(t.get("urgency") or 0), t.get("expires_at") or ""))
    actions, used = [], set()
    for trg in cands:
        vid = trg.get("visitor_id")
        if vid in used:
            continue  # one message per visitor per round
        conv_id = f"conv_{vid}_{trg.get('id')}"
        if conv_id in STORE.convs:
            conv_id += "_" + uuid.uuid4().hex[:6]
        conv, c = start_conversation(conv_id, vid, trg, now)
        if c.get("_skip"):
            with STORE.lock:
                STORE.convs.pop(conv_id, None)
            continue
        used.add(vid)
        with STORE.lock:
            STORE.sent_triggers.add(trg.get("id"))
            if trg.get("suppression_key"):
                STORE.used_suppression.add(trg["suppression_key"])
            STORE.flag(vid).last_sent_at = now.isoformat()
        actions.append(_action(conv, c))
        if len(actions) >= MAX_ACTIONS:
            break
    return {"actions": actions}


# ------------------------------------------------------------------ reply
@app.post("/v1/reply")
async def reply(req: Request):
    b = await _json(req)
    if b is None or not b.get("conversation_id"):
        return JSONResponse(status_code=400, content={"action": "end", "rationale": "malformed reply payload"})
    try:
        return handle_reply(b)
    except Exception as e:  # never return a 500 to the caller
        return {"action": "wait", "wait_seconds": 1800, "rationale": f"Internal issue handling this turn ({type(e).__name__}); backing off briefly."}


@app.post("/v1/teardown")
async def teardown():
    STORE.reset()
    return {"ok": True, "wiped_at": _now_iso()}
