"""Chat page: pick a visitor and a starting point, then chat as the visitor.

Runs on its own Store (preloaded with the bundled sample data) so it never touches
contexts or conversations pushed through the /v1 API.
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from .kb import KB
from .replies import ensure_conv, handle_reply, start_conversation
from .store import Store, reset_store, use_store

DATA = Path(__file__).resolve().parent.parent / "data"
router = APIRouter()
_lock = threading.Lock()
_play: Store | None = None
DEMO_NOW = "2026-10-27T10:00:00+05:30"  # sample data is dated around this week


def _load() -> Store:
    st = Store()
    for scope, sub, key in (("visitor", "visitors", "visitor_id"), ("trigger", "triggers", "id")):
        for f in sorted((DATA / sub).glob("*.json")):
            p = json.loads(f.read_text(encoding="utf-8"))
            st.put(scope, p.get(key) or f.stem, 1, p)
    return st


def play() -> Store:
    global _play
    with _lock:
        if _play is None:
            _play = _load()
        return _play


@router.get("/playground/api/catalog")
async def catalog():
    st = play()
    visitors, triggers = [], {}
    for (scope, cid), e in st.contexts.items():
        p = e["payload"]
        if scope == "visitor":
            idn = p.get("identity", {})
            visitors.append({"id": cid, "name": idn.get("name"), "stage": p.get("stage_label"), "city": idn.get("city"),
                             "lang": "Hinglish" if str(idn.get("language_pref", "")).startswith("hi") else "English"})
        elif scope == "trigger":
            triggers.setdefault(p.get("visitor_id"), []).append({"id": p.get("id") or cid, "kind": p.get("kind"), "urgency": p.get("urgency")})
    visitors.sort(key=lambda v: v["id"])
    for v in triggers.values():
        v.sort(key=lambda t: -(t["urgency"] or 0))
    return {"visitors": visitors, "triggers": triggers,
            "sections": [{"id": s["id"], "title": s["title"],
                          "questions": [{"id": f["id"], "q": f["question"]} for f in KB.in_section(s["id"])]} for s in KB.sections]}


@router.post("/playground/api/start")
async def start(req: Request):
    b = await req.json()
    st = play()
    vid, tid = b.get("visitor_id") or None, b.get("trigger_id") or None
    conv_id = f"play_{uuid.uuid4().hex[:8]}"
    tok = use_store(st)
    try:
        if not tid:  # free chat: visitor speaks first
            conv = ensure_conv({"conversation_id": conv_id, "visitor_id": vid})
            return {"conversation_id": conv.conversation_id, "opening": None}
        trg = st.get("trigger", tid)
        if not trg:
            return JSONResponse(status_code=400, content={"error": "unknown starting point"})
        from .util import parse_dt
        conv, c = start_conversation(conv_id, vid or trg.get("visitor_id"), trg, parse_dt(DEMO_NOW))
        return {"conversation_id": conv_id,
                "opening": {k: c[k] for k in ("body", "cta", "send_as", "rationale", "template_name", "suppression_key")},
                "skipped_reason": c.get("_skip")}
    finally:
        reset_store(tok)


@router.post("/playground/api/reply")
async def reply(req: Request):
    b = await req.json()
    st = play()
    conv = st.convs.get(b.get("conversation_id"))
    if not conv:
        return JSONResponse(status_code=400, content={"error": "start a conversation first"})
    tok = use_store(st)
    try:
        out = handle_reply({"conversation_id": conv.conversation_id, "visitor_id": conv.visitor_id,
                            "message": b.get("message", ""), "received_at": datetime.now(timezone.utc).isoformat(),
                            "turn_number": len(conv.inbound) + 2})
        out["conversation_closed"] = conv.ended
        return out
    finally:
        reset_store(tok)


@router.get("/playground/api/leads")
async def leads():
    return {"leads": play().leads}


@router.post("/playground/api/reset")
async def reset():
    global _play
    with _lock:
        _play = None
    play()
    return {"ok": True}


@router.get("/", response_class=HTMLResponse)
@router.get("/playground", response_class=HTMLResponse)
async def page():
    return HTMLResponse((Path(__file__).parent / "playground.html").read_text(encoding="utf-8"))
