"""Opening messages: one handler per starting point (trigger kind).

Every fact in an opener comes from the visitor/trigger data or from faq.json — no invented fees,
durations, addresses or promises.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .kb import KB
from .util import first_name, nice_date, nice_time, parse_dt, prefers_hinglish


def menu_text(hi: bool) -> tuple[str, list[str]]:
    lines = [f"{i}. {s['title_hi'] if hi else s['title']}" for i, s in enumerate(KB.sections, 1)]
    return "\n".join(lines), [f"sec:{s['id']}" for s in KB.sections]


def section_text(sid: str, hi: bool) -> tuple[str, list[str]]:
    sec = KB.section(sid)
    faqs = KB.in_section(sid)
    head = (f"*{sec['title_hi']}* — number bhejiye:" if hi else f"*{sec['title']}* — reply with a number:")
    lines = [f"{i}. {f['question']}" for i, f in enumerate(faqs, 1)]
    return head + "\n" + "\n".join(lines), [f"faq:{f['id']}" for f in faqs]


def offer_for(visitor: dict | None, hi: bool, key: str = "session") -> str:
    """Text of what the bot is offering to do next, in the visitor's current language."""
    if key == "callback":
        return "representative se call request kar doon" if hi else "request a call from a representative"
    if key == "attend":
        return "aapka session confirm kar doon" if hi else "confirm your session"
    child = ((visitor or {}).get("child") or {}).get("name")
    if child:
        return f"{child} ke liye counselling session request kar doon" if hi else f"request a counselling session for {child}"
    return "aapke liye one-on-one counselling session request kar doon" if hi else "request a one-on-one counselling session for you"


def _base(kind, visitor, trigger, body, cta, rationale, topic, hi, menu=None, offer=None):
    name = first_name(visitor)
    return {
        "body": body.strip(),
        "cta": cta,
        "send_as": "lakshay_assistant",
        "template_name": f"lakshay_{kind}_v1",
        "template_params": [name or "there", topic, "hi" if hi else "en"],
        "suppression_key": (trigger or {}).get("suppression_key") or f"{kind}:{(visitor or {}).get('visitor_id', 'anon')}",
        "rationale": rationale,
        "_offer": offer or "session",
        "_topic": topic,
        "_menu": menu or [],
        "_hi": hi,
    }


def compose(visitor: dict | None, trigger: dict | None, now: datetime | None = None, hi: bool | None = None) -> dict:
    trigger = trigger or {"kind": "new_visitor_welcome", "payload": {}}
    now = now or datetime.now(timezone.utc)
    kind = trigger.get("kind") or "new_visitor_welcome"
    p = trigger.get("payload") or {}
    hi = prefers_hinglish(visitor) if hi is None else hi
    name = first_name(visitor)
    hello = (f"Namaste {name} ji" if hi and (visitor or {}).get("stage") == "parent"
             else (f"Hi {name}" if name else ("Namaste" if hi else "Hi")))

    exp = parse_dt(trigger.get("expires_at"))
    skip = "this starting point has expired" if exp and exp < now else None

    if kind == "enquiry_followup":
        q = p.get("enquiry_text")
        d = nice_date(p.get("enquiry_date")) if p.get("enquiry_date") else ""
        asked = f" ({d})" if d else ""
        if hi:
            body = (f"{hello}, aapki enquiry{asked} mili — \"{q}\". Yahi cheez hamari one-on-one counselling mein hoti hai: "
                    "counsellor aapke interests, abilities, educational background aur long-term goals ke hisaab se options "
                    "explore karne mein madad karta hai. Kya main aapke liye session request kar doon? Reply YES.")
        else:
            body = (f"{hello}, thanks for your enquiry{asked} — \"{q}\". That's exactly what our one-on-one counselling is for: "
                    "a counsellor helps you explore options based on your interests, abilities, educational background and "
                    "long-term goals. Shall I request a session for you? Reply YES.")
        c = _base(kind, visitor, trigger, body, "binary_yes_no",
                  "Fresh enquiry: quote the visitor's own question back, tie it to FAQ Q6/Q8, single YES CTA.", "enquiry", hi)

    elif kind == "dormant_lead":
        d = nice_date(p.get("enquiry_date")) if p.get("enquiry_date") else "a while back"
        if hi:
            body = (f"{hello}, aapne {d} ko humse enquiry ki thi. Agar abhi bhi next step clear nahi hai, toh koi baat nahi — "
                    "bahut se students confused hokar hi hamare paas aate hain, aur hamari team options samajhne mein madad karti hai. "
                    "Session request kar doon? Reply YES, ya apna sawaal poochiye.")
        else:
            body = (f"{hello}, you'd enquired with us on {d}. If your next step still isn't clear, that's completely fine — many "
                    "students come to us confused, and our team helps them understand their options. Shall I request a "
                    "counselling session for you? Reply YES, or ask me anything.")
        c = _base(kind, visitor, trigger, body, "binary_yes_no",
                  "Lead went quiet after enquiring: low-pressure re-open grounded in FAQ Q7, one YES CTA.", "follow-up", hi)

    elif kind == "session_reminder":
        when = p.get("session_iso")
        dt = parse_dt(when)
        if dt and dt < now:
            skip = skip or "the session time has already passed"
        mode = p.get("mode")
        mode_txt = f"{mode} " if mode else ""
        if hi:
            body = (f"{hello}, reminder: aapka {mode_txt}career counselling session {nice_date(when, True)} ko {nice_time(when)} par hai. "
                    "Reply 1 agar aap aa rahe hain, ya 2 agar reschedule karna hai.")
        else:
            body = (f"{hello}, a quick reminder: your {mode_txt}career counselling session is on {nice_date(when, True)} at "
                    f"{nice_time(when)}. Reply 1 to confirm you'll attend, or 2 if you need to reschedule.")
        c = _base(kind, visitor, trigger, body, "multi_choice",
                  "Upcoming booked session: exact date/time from the booking, two-option reply (attend / reschedule).",
                  "session reminder", hi, menu=["attend", "reschedule"],
                  offer="attend")

    elif kind == "parent_enquiry":
        child = (visitor or {}).get("child") or {}
        cname = child.get("name") or ("aapke bachche" if hi else "your child")
        q = p.get("enquiry_text")
        qtxt = f" — \"{q}\"" if q else ""
        if hi:
            body = (f"{hello}, {cname} ke liye aapka sawaal mila{qtxt}. Jahan zaroori ho, parents counselling process mein shamil ho "
                    f"sakte hain; counsellor {cname} ke interests, abilities aur goals ke hisaab se options explore karwata hai. "
                    f"Kya main {cname} ke liye session request kar doon? Reply YES.")
        else:
            body = (f"{hello}, we got your question about {cname}{qtxt}. Parents can be involved in the counselling process where "
                    f"appropriate, and the counsellor explores options based on {cname}'s interests, abilities and goals. "
                    f"Shall I request a session for {cname}? Reply YES.")
        c = _base(kind, visitor, trigger, body, "binary_yes_no",
                  "Parent enquiry: answer the implied 'can I be involved' (FAQ Q9) before the ask; session for the child.",
                  "parent enquiry", hi)

    elif kind == "career_explorer":
        if hi:
            body = (f"{hello}, abhi decide nahi hua ki kaunsa career? Lakshay India alag-alag industries ki 50+ high-profile career "
                    "opportunities explore karne mein madad karta hai, aur counsellor aapke interests aur strengths ke hisaab se "
                    "options match karta hai. Session request kar doon? Reply YES, ya bataiye aapko kya pasand hai.")
        else:
            body = (f"{hello}, still deciding which career to aim for? Lakshay India helps you explore 50+ high-profile career "
                    "opportunities across industries, and a counsellor matches options to your interests and strengths. "
                    "Shall I request a session for you? Reply YES, or tell me what you enjoy.")
        c = _base(kind, visitor, trigger, body, "binary_yes_no",
                  "Undecided visitor: curiosity hook from FAQ Q5/Q6 (no income promises), one YES CTA.", "career exploration", hi)

    else:  # new_visitor_welcome / anything unknown
        menu, ids = menu_text(hi)
        if hi:
            body = (f"{hello} 👋 Lakshay India mein aapka swagat hai — students aur young professionals ke liye career counselling "
                    f"aur mentorship. Hum aapke sawaalon ka jawab de sakte hain ya counselling session request kar sakte hain. "
                    f"Topic chuniye (number bhejiye):\n{menu}\nYa seedha apna sawaal likhiye.")
        else:
            body = (f"{hello} 👋 Welcome to Lakshay India — career counselling and mentorship for students and young professionals. "
                    f"I can answer your questions or request a counselling session for you. Pick a topic (reply with a number):\n"
                    f"{menu}\nOr just type your question.")
        c = _base("new_visitor_welcome", visitor, trigger, body, "open_ended",
                  "Welcome: one-line intro from FAQ Q1/Q3, numbered topic menu, free-text questions welcome.", "welcome", hi,
                  menu=ids)

    if skip:
        c["_skip"] = skip
    return c
