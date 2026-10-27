"""In-memory state: versioned contexts (visitors, triggers), conversations, per-visitor flags, leads."""
from __future__ import annotations

import threading
import time
from contextvars import ContextVar
from dataclasses import dataclass, field

SCOPES = ("visitor", "trigger")


@dataclass
class Conversation:
    conversation_id: str
    visitor_id: str | None
    trigger_id: str | None = None
    kind: str = "free_chat"
    topic: str = ""
    offer: str = ""
    stage: str = "open"            # open -> booking -> confirm -> done ; ended flag closes it
    booking: dict = field(default_factory=dict)   # collected lead fields
    awaiting: str | None = None    # which booking field we asked for last
    menu: list = field(default_factory=list)      # what a numbered reply ("2") refers to
    bot_bodies: list = field(default_factory=list)
    inbound: list = field(default_factory=list)
    answered: list = field(default_factory=list)  # FAQ ids already answered
    auto_count: int = 0
    hostile_count: int = 0
    offtopic_count: int = 0
    question_count: int = 0
    unknown_count: int = 0
    nudges: int = 0
    lang_hinglish: bool | None = None
    lead_id: str | None = None
    ended: bool = False
    created: float = field(default_factory=time.time)


@dataclass
class VisitorFlags:
    opted_out: bool = False
    backoff_until: str | None = None
    auto_texts: list = field(default_factory=list)
    auto_count: int = 0
    last_sent_at: str | None = None


class Store:
    def __init__(self):
        self.lock = threading.RLock()
        self.started = time.time()
        self.reset()

    def reset(self):
        with getattr(self, "lock", threading.RLock()):
            self.contexts: dict[tuple[str, str], dict] = {}
            self.alias: dict[tuple[str, str], str] = {}
            self.convs: dict[str, Conversation] = {}
            self.flags: dict[str, VisitorFlags] = {}
            self.used_suppression: set[str] = set()
            self.sent_triggers: set[str] = set()
            self.leads: list[dict] = []

    # ------------------------------------------------------------ contexts
    def put(self, scope: str, cid: str, version: int, payload: dict):
        with self.lock:
            cur = self.contexts.get((scope, cid))
            if cur and cur["version"] >= version:
                return False, cur["version"]
            self.contexts[(scope, cid)] = {"version": version, "payload": payload, "ts": time.time()}
            pid = payload.get("visitor_id") if scope == "visitor" else payload.get("id")
            if pid and pid != cid:
                self.alias[(scope, pid)] = cid
            return True, version

    def get(self, scope: str, cid: str | None) -> dict | None:
        if not cid:
            return None
        with self.lock:
            e = self.contexts.get((scope, cid))
            if not e:
                real = self.alias.get((scope, cid))
                if real:
                    e = self.contexts.get((scope, real))
            return e["payload"] if e else None

    def counts(self) -> dict:
        with self.lock:
            c = {s: 0 for s in SCOPES}
            for (scope, _cid) in self.contexts:
                c[scope] = c.get(scope, 0) + 1
            c["conversations"] = len(self.convs)
            c["leads"] = len(self.leads)
            return c

    def triggers_for_visitor(self, visitor_id: str) -> list[dict]:
        with self.lock:
            return [e["payload"] for (scope, _), e in self.contexts.items()
                    if scope == "trigger" and e["payload"].get("visitor_id") == visitor_id]

    def flag(self, visitor_id: str | None) -> VisitorFlags:
        key = visitor_id or "_anonymous"
        with self.lock:
            if key not in self.flags:
                self.flags[key] = VisitorFlags()
            return self.flags[key]

    def add_lead(self, lead: dict) -> str:
        with self.lock:
            lead_id = f"LKY-{1001 + len(self.leads)}"
            self.leads.append({"lead_id": lead_id, **lead, "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
            return lead_id


STORE = Store()

# The API uses STORE. The chat page uses its own Store (preloaded with sample data) so demo chats
# never touch data pushed through the API; handlers read whichever store is active via S().
_active: ContextVar = ContextVar("lakshay_store", default=None)


def S() -> Store:
    return _active.get() or STORE


def use_store(store: Store):
    return _active.set(store)


def reset_store(token):
    _active.reset(token)
