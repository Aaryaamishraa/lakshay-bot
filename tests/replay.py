"""Multi-turn conversation scenarios against a running bot (run harness.py first so visitors are loaded)."""
import json
import os
import urllib.request

BOT = os.getenv("BOT_URL", "http://localhost:8080").rstrip("/")
NOW = "2026-10-27T10:00:00+05:30"


def post(path, body):
    req = urllib.request.Request(BOT + path, data=json.dumps(body).encode(), method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def convo(title, msgs, visitor=None, trigger=None):
    cid = "replay_" + title.replace(" ", "_")
    print(f"\n===== {title}")
    st = post("/v1/start", {"conversation_id": cid, "visitor_id": visitor, "trigger_id": trigger}) if trigger else None
    if st:
        print(f"  BOT: {st['body']}")
    for m in msgs:
        r = post("/v1/reply", {"conversation_id": cid, "visitor_id": visitor, "message": m, "received_at": NOW})
        print(f"  VISITOR: {m}\n  BOT[{r['action']}]: {r.get('body') or r.get('wait_seconds') or ''}\n     why: {r['rationale']}")
        if r["action"] == "end" or "Closing with a single polite goodbye" in r.get("rationale", ""):
            break


convo("free chat greeting and menu", ["hi", "4", "1", "MENU", "3", "3"])
convo("free chat questions then book", ["Is it online or offline?", "how much does it cost and how long is a session?",
                                        "do you guarantee a job?", "ok book a session", "I'm Priya", "class 12", "not sure",
                                        "any", "SAME", "change mode to online", "confirm", "thanks"])
convo("enquiry follow-up hinglish", ["haan kar do", "online", "same", "confirm", "shukriya"], "v_002_simran_bcom_chandigarh", "trg_002_enquiry_followup_simran")
convo("parent enquiry", ["kya main bhi session mein baith sakti hoon?", "haan", "koi bhi", "online", "9988776655", "CONFIRM"], "v_005_sunita_parent_jaipur", "trg_005_parent_enquiry_sunita")
convo("session reminder reschedule", ["2", "thanks"], "v_004_neha_professional_bangalore", "trg_004_session_reminder_neha")
convo("session reminder attend", ["yes I'll be there"], "v_004_neha_professional_bangalore", "trg_004_session_reminder_neha")
convo("career explorer interest", ["I like drawing and design", "yes", "my number is 9123456789", "in-person", "yes"], "v_006_kabir_class10_mumbai", "trg_006_career_explorer_kabir")
convo("auto-reply loop", ["Thank you for contacting us! Our team will respond shortly."] * 4, "v_003_rohit_graduate_lucknow", "trg_003_dormant_lead_rohit")
convo("hostile then stop", ["This is useless, stop bothering me"], "v_001_aarav_class12_delhi", "trg_001_welcome_aarav")
convo("hostile once then question", ["you people are so annoying", "ok fine, can my parents join?", "no"], None, None)
convo("off-topic and unknown", ["Can you help me file my GST?", "Do you have a branch in Pune?", "What is the success rate of IIT JEE toppers?", "yes", "Ankit", "graduated in 2025, B.A.", "law or civil services", "offline", "same", "confirm"])
convo("defer", ["abhi busy hoon, baad mein baat karte hain"], "v_003_rohit_graduate_lucknow", "trg_008_career_explorer_rohit")
convo("are you a bot", ["are you a bot?", "agent please", "Meera", "meera@mail.com", "confirm"])
convo("hard no", ["no thanks"], "v_001_aarav_class12_delhi", "trg_001_welcome_aarav")
