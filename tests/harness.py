"""Local end-to-end test of the API: health, context push, start, tick, reply. Run with the bot on :8080."""
import glob
import json
import os
import time
import urllib.error
import urllib.request

BOT = os.getenv("BOT_URL", "http://localhost:8080").rstrip("/")
DS = os.getenv("DATASET", os.path.join(os.path.dirname(__file__), "..", "data"))
NOW = os.getenv("SIM_NOW", "2026-10-27T10:00:00+05:30")


def call(method, path, body=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BOT + path, data=data, method=method, headers={"Content-Type": "application/json"})
    t = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read()), time.time() - t
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}"), time.time() - t


def load(sub):
    return [json.load(open(f, encoding="utf-8")) for f in sorted(glob.glob(f"{DS}/{sub}/*.json"))]


def main():
    fails = []
    call("POST", "/v1/teardown", {})
    s, h, _ = call("GET", "/v1/healthz"); assert s == 200 and h["faqs_loaded"] == 20, h
    s, m, _ = call("GET", "/v1/metadata"); assert s == 200 and m["team_name"], m
    s, f, _ = call("GET", "/v1/faq"); assert s == 200 and len(f["faqs"]) == 20
    visitors, triggers = load("visitors"), load("triggers")
    for v in visitors:
        assert call("POST", "/v1/context", {"scope": "visitor", "context_id": v["visitor_id"], "version": 1, "payload": v})[0] == 200
    for t in triggers:
        assert call("POST", "/v1/context", {"scope": "trigger", "context_id": t["id"], "version": 1, "payload": t})[0] == 200
    s, r, _ = call("POST", "/v1/context", {"scope": "visitor", "context_id": visitors[0]["visitor_id"], "version": 1, "payload": visitors[0]})
    print("re-push same version ->", s, r); assert s == 409
    s, r, _ = call("POST", "/v1/context", {"scope": "bogus", "context_id": "x", "version": 1, "payload": {}})
    print("bad scope ->", s, r); assert s == 400
    print("healthz:", call("GET", "/v1/healthz")[1]["contexts_loaded"])

    # proactive rounds: one message per visitor per round, no repeats
    ids = [t["id"] for t in triggers]
    out = []
    for rnd in range(3):
        s, r, lat = call("POST", "/v1/tick", {"now": NOW, "available_triggers": ids})
        assert s == 200
        out += r["actions"]
        print(f"tick {rnd}: {len(r['actions'])} actions ({lat * 1000:.0f} ms)")
    req = ["conversation_id", "visitor_id", "send_as", "trigger_id", "cta", "suppression_key", "rationale", "body", "template_name"]
    for a in out:
        miss = [k for k in req if k not in a]
        if miss or not a["body"].strip():
            fails.append(("malformed", a.get("trigger_id"), miss))
        if "http" in a["body"]:
            fails.append(("url", a["trigger_id"]))
    if len({a["trigger_id"] for a in out}) != len(out):
        fails.append(("duplicate trigger sent",))
    print("total proactive messages:", len(out), "of", len(ids), "starting points")

    # a free chat through the public API, ending in a saved request
    s, st, _ = call("POST", "/v1/start", {"conversation_id": "api_free_1"})
    assert s == 200, st
    turns = ["How much does it cost?", "ok book it", "Rahul Sharma", "B.Tech 2nd year", "data science", "online", "9876543210", "CONFIRM", "no thanks"]
    for t in turns:
        s, r, _ = call("POST", "/v1/reply", {"conversation_id": "api_free_1", "message": t, "received_at": NOW})
        assert s == 200, r
        if "Internal issue" in r.get("rationale", ""):
            fails.append(("internal error", t, r))
    leads = call("GET", "/v1/leads")[1]["leads"]
    print("leads:", json.dumps(leads, indent=1, ensure_ascii=False))
    if not leads or leads[-1].get("contact") != "+91 98765 43210":
        fails.append(("lead not saved correctly", leads))
    json.dump(out, open(os.getenv("OUT", "/tmp/lakshay_actions.json"), "w"), indent=1, ensure_ascii=False)
    print("fails:", fails)
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
