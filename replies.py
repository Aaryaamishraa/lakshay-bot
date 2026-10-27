"""Reply engine: classify the inbound turn, then route to send / wait / end.

Same shape as Vera's engine: opt-out and hostility are respected immediately, auto-replies get one nudge then
a back-off, "yes" switches straight to action (a short counselling-request flow that only asks for what we
don't already know), questions are answered word-for-word from faq.json, off-topic asks are politely
redirected, and every conversation closes with one polite goodbye.
"""
from __future__ import annotations

import re
from datetime import timedelta

from . import llm
from .composer import compose, menu_text, offer_for, section_text
from .kb import KB
from .store import S, Conversation
from .util import first_name, is_english_text, is_hinglish_text, nice_date, nice_time, norm, parse_dt, prefers_hinglish

# ------------------------------------------------------------------ patterns
OPTOUT = re.compile(
    r"\b(stop|unsubscribe|not interested|no interest|don'?t (?:message|msg|text|contact|call)|do not (?:message|contact|text|call)|"
    r"leave me alone|remove me|remove my number|band karo|mat bhejo|message mat|msg mat|nahi chahiye|nai chahiye|"
    r"no more messages|stop sending|stop messaging)\b", re.I)
HOSTILE = re.compile(
    r"\b(useless|spam|spammer|bothering|irritat\w*|nonsense|bakwas|bakwaas|pagal|idiot|stupid|shut up|fraud|scam|"
    r"waste of (?:my )?time|bekaar|bekar|annoying|harass\w*|bloody|damn|f+u+c+k\w*|shit|bullshit|get lost|"
    r"why are you (?:bothering|messaging|disturbing))\b", re.I)
AUTO = re.compile(
    r"(thank(?:s| you) for (?:contacting|reaching|your message|messaging|writing)|we will (?:get back|respond|revert|contact)|"
    r"will (?:get back|respond|revert) (?:to you )?(?:shortly|soon|asap)|our team will (?:respond|reply|get back|contact)|"
    r"automated (?:message|response|reply|assistant)|auto[- ]?reply|out of (?:the )?office|currently (?:unavailable|closed|away)|"
    r"business hours|working hours are|i am an automated|this is an automated|we are closed|will be in touch)", re.I)
STRONG_COMMIT = re.compile(
    r"\b(yes|yeah|yep|yup|haan|haa|haanji|hanji|ji haan|go ahead|let'?s do it|lets do it|do it|let'?s go|proceed|"
    r"please do|pls do|kar do|kardo|kar dijiye|karwa do|chalo|go for it|sign me up|i'?m in|book (?:it|me|a session|one|my session)|"
    r"i want (?:a|to book|to join|counselling|a session)|i would like (?:a|to book)|request (?:a|one|it)|"
    r"session chahiye|book kar do|book karna hai|interested)\b", re.I)
COMMIT = re.compile(r"\b(ok(?:ay)?|okk+|sure|theek hai|thik hai|sounds good|perfect|fine|chalega|done)\b", re.I)
CONFIRM = re.compile(r"\b(confirm(?:ed)?|yes|yeah|yep|haan|haanji|send it|submit|go ahead|ok(?:ay)?|correct|sahi hai|theek hai|bhej do)\b", re.I)
DEFER = re.compile(
    r"\b(later|busy|baad mein|baad me|kal baat|tomorrow|in a meeting|not now|abhi nahi|next week|thodi der|"
    r"after some time|remind me|give me (?:some )?time|in class|exam chal)\b", re.I)
OFFTOPIC = re.compile(
    r"\b(gst|income tax|itr|tax filing|tax return|loan|insurance|electricity bill|passport|aadhaar|aadhar|pan card|"
    r"legal notice|cricket score|stock tips?|share market|mutual fund|bank account|credit card|crypto|bitcoin|"
    r"instagram followers|recipe|movie|weather|girlfriend|boyfriend|dating|solve (?:this|my) (?:question|homework|sum)|"
    r"homework|assignment answers?|exam paper leak|paper leak)\b", re.I)
NEGATIVE = re.compile(r"^\s*(no|nope|nah|nahi|nahin|na|not really|no thanks|no thank you|not required|pass|nothing|nothing else|"
                      r"that'?s all|thats all|bas|bas itna|kuch nahi)\b", re.I)
QUESTION = re.compile(
    r"(\?|^\s*(what|how|why|when|where|which|who|can|could|does|do|is|are|will|should|kitna|kitne|kya|kaise|kab|kaun|kahan|konsa|kaunsa)\b|"
    r"\b(details|explain|tell me more|batao|bataiye|batayein)\b)", re.I)
GREETING = re.compile(r"^\s*(hi+|hello+|hey+|hii+|namaste|namaskar|good (?:morning|afternoon|evening)|hola|yo|start)\s*[!.👋🙏]*\s*$", re.I)
THANKS = re.compile(r"\b(thanks?|thank you|thx|ty|shukriya|dhanyavaad|dhanyawad|great|nice|cool|awesome|👍|🙏)\b", re.I)
MENU = re.compile(r"^\s*(menu|options|topics|help|faq|faqs|main menu|start over|restart|questions)\s*[?!.]*\s*$", re.I)
HUMAN = re.compile(
    r"\b(agent|human|real person|representative|talk to (?:a |someone|somebody|a person|counsel)\w*|speak (?:to|with) (?:a |someone|somebody)\w*|"
    r"call me|call back|callback|kisi se baat|insaan se baat|counsellor se baat)\b", re.I)
BOT_Q = re.compile(r"\b(are you (?:a )?(?:bot|robot|human|real|ai)|is this (?:a )?(?:bot|human|real person|automated)|bot ho|insaan ho)\b", re.I)
RESCHEDULE = re.compile(r"(^\s*2\s*$|\b(reschedule|postpone|another (?:day|time)|change (?:the )?(?:date|time)|can'?t make it|cant make it|"
                        r"cancel|nahi aa (?:paunga|paungi|payenge)|date badal|time badal)\b)", re.I)
ATTEND = re.compile(r"(^\s*1\s*$|\b(i'?ll be there|will attend|attending|coming|aaunga|aaungi|aa raha|aa rahi|confirm(?:ed)?|yes|haan)\b)", re.I)
NUMBER_PICK = re.compile(r"^\s*(\d{1,2})\s*[.)]?\s*$")

CLOSING_EN = "Thanks, and have a nice day! 🙏"
CLOSING_HI = "Dhanyavaad, aapka din accha rahe! 🙏"


def _closing(hi: bool) -> str:
    return CLOSING_HI if hi else CLOSING_EN


# ------------------------------------------------------------------ classification
def classify(msg: str, conv: Conversation, flags) -> str:
    m = (msg or "").strip()
    if not m:
        return "empty"
    n = norm(m)
    if OPTOUT.search(m) and not re.search(r"\bdon'?t stop\b", m, re.I):
        return "optout"
    prev = [norm(x) for x in conv.inbound[:-1]]
    if AUTO.search(m) or (len(n) > 25 and (n in prev or n in flags.auto_texts)):
        return "auto"
    if HOSTILE.search(m):
        return "hostile"
    if NUMBER_PICK.match(m) and conv.menu:
        return "pick"
    if MENU.match(m):
        return "menu"
    if BOT_Q.search(m):
        return "bot_q"
    if OFFTOPIC.search(m):
        return "offtopic"
    if DEFER.search(m):
        return "defer"
    if HUMAN.search(m):
        return "human"
    if STRONG_COMMIT.search(m) and not NEGATIVE.match(m):
        return "commit"
    if COMMIT.search(m) and not NEGATIVE.match(m) and not QUESTION.search(m) and len(n.split()) <= 4:
        return "commit"
    if NEGATIVE.match(m):
        return "negative"
    if GREETING.match(m):
        return "greeting"
    if THANKS.search(m) and not QUESTION.search(m) and len(n.split()) <= 6:
        return "thanks"
    if QUESTION.search(m) or KB.match(m):
        return "question"
    return "other"


# ------------------------------------------------------------------ helpers
def _visitor(conv: Conversation) -> dict:
    return S().get("visitor", conv.visitor_id) or {}


def _hi(conv: Conversation, msg: str, visitor: dict) -> bool:
    if is_hinglish_text(msg):
        conv.lang_hinglish = True
        return True
    if is_english_text(msg):
        conv.lang_hinglish = False
        return False
    if conv.lang_hinglish is not None:
        return conv.lang_hinglish
    return prefers_hinglish(visitor)


def _send(conv: Conversation, body: str, cta: str, rationale: str, extra: dict | None = None) -> dict:
    body = re.sub(r"https?://\S+|www\.\S+", "", body).strip()
    if body in conv.bot_bodies:  # anti-repetition
        body = body + (" (Reply STOP anytime to pause these.)" if "STOP" not in body else " 🙏")
        if body in conv.bot_bodies:
            conv.ended = True
            return {"action": "end", "rationale": "Would otherwise repeat an earlier message verbatim; closing instead."}
    conv.bot_bodies.append(body)
    out = {"action": "send", "body": body, "cta": cta, "rationale": rationale}
    if extra:
        out.update(extra)
    return out


def _backoff(conv: Conversation, received_at: str | None, seconds: int):
    base = parse_dt(received_at)
    if base and conv.visitor_id:
        S().flag(conv.visitor_id).backoff_until = (base + timedelta(seconds=seconds)).isoformat()


def _cta_line(conv: Conversation, hi: bool) -> str:
    if conv.stage == "done":
        return "Kuch aur poochna hai?" if hi else "Anything else you'd like to know?"
    if conv.kind == "session_reminder":
        return "Reply 1 agar aap aa rahe hain, ya 2 reschedule ke liye." if hi else "Reply 1 to confirm you'll attend, or 2 to reschedule."
    if conv.question_count >= 3:
        return "Jab ready hon, bas YES bhej dijiye." if hi else "Whenever you're ready, just send YES."
    offer = offer_for(_visitor(conv), hi, conv.offer or "session")
    return (f"Kya main {offer}? Reply YES." if hi else f"Shall I {offer}? Reply YES.")


def ensure_conv(body: dict) -> Conversation:
    cid = body.get("conversation_id") or "conv_unknown"
    with S().lock:
        conv = S().convs.get(cid)
        if conv:
            return conv
        vid = body.get("visitor_id")
        visitor = S().get("visitor", vid)
        conv = Conversation(conversation_id=cid, visitor_id=vid, kind="free_chat", offer="session",
                            lang_hinglish=True if prefers_hinglish(visitor) else None)
        S().convs[cid] = conv
        return conv


def start_conversation(conv_id: str, visitor_id: str | None, trigger: dict | None, now=None) -> tuple[Conversation, dict]:
    """Create a conversation and its opening message (used by /v1/start, /v1/tick and the chat page)."""
    visitor = S().get("visitor", visitor_id)
    c = compose(visitor, trigger, now)
    with S().lock:
        conv = Conversation(conversation_id=conv_id, visitor_id=visitor_id, trigger_id=(trigger or {}).get("id"),
                            kind=(trigger or {}).get("kind") or "new_visitor_welcome", topic=c["_topic"],
                            offer=c["_offer"], menu=list(c["_menu"]), bot_bodies=[c["body"]], lang_hinglish=c["_hi"])
        S().convs[conv_id] = conv
    return conv, c


# ------------------------------------------------------------------ FAQ answers
def _faq_reply(conv, msg, hi, faqs, lead_in="") -> dict:
    conv.question_count += 1
    parts = [lead_in] if lead_in else []
    for f in faqs:
        txt = KB.answer_text(f, hi)
        if len(faqs) > 1 or not QUESTION.search(msg):
            # "Absolutely!" / "Not at all!" answer the FAQ's own wording; drop them when the visitor didn't ask it that way
            txt = re.sub(r"^(?:Absolutely!|Not at all!|Bilkul nahi!|Bilkul!)\s*", "", txt)
            txt = txt[:1].upper() + txt[1:]
        if f["id"] in conv.answered and len(faqs) == 1:
            txt = ("Dobara bata doon: " if hi else "To recap: ") + txt
        parts.append(txt)
        if f["id"] not in conv.answered:
            conv.answered.append(f["id"])
    ids = {f["id"] for f in faqs}
    if conv.stage in ("booking", "confirm"):
        parts.append(_ask(conv, hi, again=True))
        cta = "open_ended"
    elif ids & {"q11", "q18"} and conv.stage == "open":
        parts.append("Hum yahin aapke liye request kar sakte hain — YES bhejiye, bas 2-3 details chahiye." if hi
                     else "I can do that for you right here — reply YES and I'll take a few details.")
        cta = "binary_yes_no"
    elif ids & {"q19", "q20"} and conv.stage == "open":
        parts.append("Representative se call chahiye? Reply YES." if hi else "Want a call from a representative? Reply YES.")
        conv.offer = "callback"
        conv.booking.setdefault("request_type", "callback")
        cta = "binary_yes_no"
    else:
        parts.append(_cta_line(conv, hi))
        cta = "binary_yes_no" if conv.stage == "open" else "open_ended"
    nums = ", ".join(f"Q{KB.number(f['id'])}" for f in faqs)
    return _send(conv, "\n\n".join(p for p in parts if p), cta,
                 f"Matched FAQ {nums}; answered word-for-word from the knowledge base, then one next step.",
                 {"faq_ids": [f["id"] for f in faqs]})


def _unknown(conv, msg, hi, visitor) -> dict:
    out = llm.answer(msg, conv, visitor, hi)
    if out:
        conv.question_count += 1
        return _send(conv, out, "open_ended", "No direct FAQ hit; LLM answered strictly from faq.json (validated for grounding).")
    conv.unknown_count += 1
    if conv.unknown_count == 1:
        text = ("Iska confirmed jawab mere paas nahi hai, aur hum guess nahi karna chahte. Lakshay India ka representative "
                "isme madad kar sakta hai — YES bhejiye aur hum callback request kar denge, ya MENU likhiye topics dekhne ke liye."
                if hi else "I don't have a confirmed answer to that, and I'd rather not guess. A Lakshay India representative can "
                "help — reply YES and I'll request a callback, or type MENU to see the topics I can answer.")
    else:
        text = ("Yeh bhi mere notes mein nahi hai. Representative isme sahi madad karega — YES bhejiye callback ke liye." if hi
                else "That's not in my notes either. A representative is the right person for it — reply YES for a callback.")
    conv.offer = "callback"
    conv.booking.setdefault("request_type", "callback")
    return _send(conv, text, "binary_yes_no", "Question not covered by the FAQ: no guessing; offered a representative callback (FAQ Q20).")


# ------------------------------------------------------------------ booking flow
FIELDS_SESSION = ["name", "stage", "interest", "mode", "contact"]
FIELDS_CALLBACK = ["name", "contact"]

ASK = {
    "name": ("What's your name?", "Aapka naam kya hai?"),
    "stage": ("What are you doing currently? (e.g. Class 10, Class 12, college student, graduate, working professional, or parent)",
              "Aap abhi kya kar rahe hain? (jaise Class 10, Class 12, college student, graduate, working professional, ya parent)"),
    "interest": ("Which careers or fields are you curious about? It's fine to say \"not sure\".",
                 "Kaunse careers ya fields mein interest hai? \"pata nahi\" bhi chalega."),
    "mode": ("Would you prefer an online or in-person session? (or \"any\")",
             "Online session chahiye ya in-person? (ya \"koi bhi\")"),
    "contact": ("What's the best phone number or email for our representative to reach you? Reply SAME to use this WhatsApp number.",
                "Representative aapse kis number ya email par contact kare? Isi WhatsApp number ke liye SAME likhiye."),
}
ASK_CHILD = {
    "name": ("What's your child's name?", "Aapke bachche ka naam kya hai?"),
    "stage": ("Which class or stage is your child in right now?", "Aapka bachcha abhi kis class / stage mein hai?"),
    "interest": ("Which careers or fields is your child curious about? \"Not sure\" is fine.",
                 "Bachche ka kin careers ya fields mein interest hai? \"pata nahi\" bhi chalega."),
}

STAGE_RULES = [
    (r"\b(class|std|standard)\s*(?:[5-9]|10)\b|\b(?:[5-9]|10)(?:th)?\s*(?:class|std|standard)\b|\b10th\b|\bmatric\b", "School student"),
    (r"\b(class|std|standard)\s*1[12]\b|\b1[12](?:th)?\s*(?:class|std|standard)\b|\b1[12]th\b|\bjunior college\b|\bschool\b", "School student"),
    (r"\b(parent|mother|father|mom|dad|my son|my daughter|my child|my kid|beta|beti)\b", "Parent"),
    (r"\b(working|job|employed|professional|work (?:as|in|at)|experience|yrs? exp|years? (?:of )?exp|naukri|company mein)\b", "Working professional"),
    (r"\b(graduate[ds]?|graduation (?:done|complete)|passed out|pass ?out|fresher|completed my|post ?grad|masters|m\.?tech|mba)\b", "Graduate"),
    (r"\b(college|university|b\.?\s?tech|b\.?\s?com|b\.?\s?sc|b\.?\s?a\b|bba|bca|mbbs|b\.?\s?e\b|diploma|undergrad|ug|[1-4](?:st|nd|rd|th) year|first year|second year|third year|final year|pursuing)\b", "College student"),
]
NOT_SURE = re.compile(r"\b(not sure|no idea|don'?t know|dont know|pata nahi|nahi pata|confused|unsure|kuch bhi|koi bhi|anything|any|skip|explore)\b", re.I)
PHONE = re.compile(r"(?:\+?91[\s-]?)?([6-9]\d{4})[\s-]?(\d{5})\b")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
SAME = re.compile(r"\b(same|this number|this one|isi number|yahi number|yahi|whatsapp|this whatsapp|use this|current number|yes|haan|ok)\b", re.I)


def _fields(conv) -> list[str]:
    return FIELDS_CALLBACK if conv.booking.get("request_type") == "callback" else FIELDS_SESSION


def _prefill(conv, visitor):
    b = conv.booking
    child = visitor.get("child") or {}
    if child:
        b.setdefault("for_child", True)
        b.setdefault("parent_name", (visitor.get("identity") or {}).get("name"))
        if child.get("name"):
            b.setdefault("name", child["name"])
        if child.get("stage_label"):
            b.setdefault("stage", child["stage_label"])
    else:
        nm = (visitor.get("identity") or {}).get("name")
        if nm:
            b.setdefault("name", nm)
        if visitor.get("stage_label"):
            b.setdefault("stage", visitor["stage_label"])
    if visitor.get("interests"):
        b.setdefault("interest", ", ".join(visitor["interests"]))
    if visitor.get("visitor_id") and not b.get("contact") and visitor.get("phone_on_file"):
        b["contact"] = "Number on file"


def _missing(conv) -> str | None:
    return next((f for f in _fields(conv) if not conv.booking.get(f)), None)


def _ask(conv, hi, again=False) -> str:
    f = conv.awaiting or _missing(conv)
    if not f:
        return ""
    table = ASK_CHILD if conv.booking.get("for_child") and f in ASK_CHILD else ASK
    q = table[f][1 if hi else 0]
    if again:
        q = ("Wapas request par: " if hi else "Back to your request: ") + q
    return q


def _parse(field: str, msg: str) -> str | None:
    m = msg.strip()
    if field == "name":
        mm = re.search(r"(?:my name is|my name's|i am|i'm|im|this is|it'?s|mera naam|naam)\s+([a-z][a-z .'-]{0,40})", m, re.I)
        cand = (mm.group(1) if mm else m)
        cand = re.sub(r"\b(hai|hoon|hu|here|ji)\b.*$", "", cand, flags=re.I).strip(" .!,")
        words = cand.split()
        if not words or len(words) > 4 or re.search(r"\d", cand) or norm(cand) in {"yes", "no", "ok", "okay", "haan", "hi", "hello"}:
            return None
        return " ".join(w[:1].upper() + w[1:] for w in words[:3])
    if field == "stage":
        if re.fullmatch(r"\s*(?:[5-9]|1[0-2])(?:th)?\s*", m):
            return f"Class {re.sub(r'\D', '', m)}"
        for rx, _grp in STAGE_RULES:
            if re.search(rx, m, re.I):
                return _cap(re.sub(r"^(?:i'?m|i am|im|main)\s+(?:in|a|an)?\s*", "", m[:80], flags=re.I).strip(" .!"))
        return _cap(m[:80].strip(" .!")) if len(norm(m)) >= 3 and norm(m) not in JUNK else None
    if field == "interest":
        if NOT_SURE.search(m) or NEGATIVE.match(m):
            return "Not sure yet — wants to explore options"
        if norm(m) in JUNK:
            return None
        return clean_interest(m) or None
    if field == "mode":
        if re.search(r"\b(online|video|zoom|google meet|virtual|ghar se)\b", m, re.I):
            return "Online"
        if re.search(r"\b(offline|in[- ]?person|physical|visit|cent(?:re|er)|office|face to face)\b", m, re.I):
            return "In-person"
        if re.search(r"\b(any|either|both|koi bhi|dono|no preference|doesn'?t matter|skip|anything)\b", m, re.I):
            return "No preference"
        return None
    if field == "contact":
        e = EMAIL.search(m)
        if e:
            return e.group(0)
        p = PHONE.search(re.sub(r"[()]", "", m))
        if p:
            return f"+91 {p.group(1)} {p.group(2)}"
        if SAME.search(m) or NEGATIVE.match(m):
            return "This WhatsApp number"
        return None
    return m


JUNK = {"same", "confirm", "yes", "no", "ok", "okay", "haan", "hi", "hello", "yes please", "sure", "done"}


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s


def clean_interest(m: str) -> str:
    t = re.sub(r"^(?:i\s+(?:really\s+)?(?:like|love|enjoy|am interested in|'m interested in|want to (?:do|become|be|go into))|"
               r"i'?m into|interested in|mujhe)\s+", "", m.strip(), flags=re.I)
    t = re.sub(r"\s+(?:pasand hai|mein interest hai|achha lagta hai)\s*$", "", t, flags=re.I)
    return t[:120].strip(" .!")


def stage_group(text: str) -> str:
    for rx, grp in STAGE_RULES:
        if re.search(rx, text or "", re.I):
            return grp
    return "Other"


def _summary(conv, hi) -> str:
    b = conv.booking
    rows = []
    if b.get("for_child"):
        rows.append(("Parent", b.get("parent_name") or "—"))
        rows.append(("Student", b.get("name")))
    else:
        rows.append(("Name", b.get("name")))
    if b.get("request_type") != "callback":
        rows += [("Currently", b.get("stage")), ("Interested in", b.get("interest")), ("Preferred mode", b.get("mode"))]
    rows.append(("Contact", b.get("contact")))
    head = ("Yeh details hamari team ko jayengi:" if hi else "Here's what I'll send to our team:")
    what = ("Request: representative callback" if b.get("request_type") == "callback" else "Request: one-on-one career counselling session")
    lines = "\n".join(f"• {k}: {v}" for k, v in rows if v)
    tail = ("CONFIRM likhiye bhejne ke liye, ya bataiye kya badalna hai." if hi
            else "Reply CONFIRM to send it, or tell me what to change.")
    return f"{head}\n• {what}\n{lines}\n\n{tail}"


def _advance(conv, hi, lead_in="") -> dict:
    nxt = _missing(conv)
    if nxt:
        conv.stage, conv.awaiting = "booking", nxt
        conv.menu = []
        text = (lead_in + "\n\n" if lead_in else "") + _ask(conv, hi)
        return _send(conv, text, "open_ended", f"Collecting only the details we don't already have (next: {nxt}).")
    conv.stage, conv.awaiting = "confirm", None
    text = (lead_in + "\n\n" if lead_in else "") + _summary(conv, hi)
    return _send(conv, text, "binary_confirm_cancel", "All details in hand; one summary and a single CONFIRM step before anything is sent.")


def _start_booking(conv, hi, visitor, request_type="session", lead_in="") -> dict:
    if conv.stage == "done" and conv.lead_id:
        text = (f"Aapki request ({conv.lead_id}) pehle se hamari team ke paas hai — woh jaldi contact karenge. Kuch aur poochna hai?"
                if hi else f"Your request ({conv.lead_id}) is already with our team — they'll be in touch soon. Anything else?")
        return _send(conv, text, "open_ended", "Request already submitted; not creating a duplicate.")
    if request_type == "callback":
        conv.booking["request_type"] = "callback"
    else:
        conv.booking.setdefault("request_type", "session")
    _prefill(conv, visitor)
    intro = lead_in or ("Badhiya — shuru karte hain." if hi else "Great — let's do it.")
    return _advance(conv, hi, intro)


def _submit(conv, hi, visitor) -> dict:
    b = dict(conv.booking)
    lead = {"conversation_id": conv.conversation_id, "visitor_id": conv.visitor_id, "trigger_id": conv.trigger_id,
            "request_type": b.pop("request_type", "session"), "stage_group": stage_group(b.get("stage", "")), **b}
    conv.lead_id = S().add_lead(lead)
    conv.stage, conv.awaiting = "done", None
    if lead["request_type"] == "callback":
        text = (f"Done ✅ Callback request hamari team ko bhej di (ref {conv.lead_id}). Lakshay India ka representative aapse contact "
                "karke aapke sawaalon ka jawab dega aur next steps batayega. Kuch aur poochna hai?" if hi else
                f"Done ✅ Your callback request is with our team (ref {conv.lead_id}). A Lakshay India representative will contact "
                "you to answer your questions and guide you through the next steps. Anything else?")
    else:
        text = (f"Done ✅ Aapki request hamari team ko bhej di (ref {conv.lead_id}). Lakshay India ka representative aapse available "
                "appointment options ke saath contact karega. Session duration aur applicable fees booking se pehle bata di jayengi. "
                "Kuch aur poochna hai?" if hi else
                f"Done ✅ Your request is with our team (ref {conv.lead_id}). A Lakshay India representative will contact you with the "
                "available appointment options, and they'll share the session duration and any applicable fees before you book "
                "anything. Anything else you'd like to know?")
    why = ("FAQ Q20" if lead["request_type"] == "callback" else "FAQ Q11/Q13/Q16")
    return _send(conv, text, "open_ended", f"Confirmed: lead saved and handed to the team; set expectations exactly as {why} state.",
                 {"lead_id": conv.lead_id})


def _apply_change(conv, msg, hi) -> dict:
    fields = {"name": r"\b(name|naam)\b", "contact": r"\b(number|phone|mobile|email|contact)\b",
              "mode": r"\b(online|offline|in[- ]?person|mode)\b", "interest": r"\b(interest|career|field)\b",
              "stage": r"\b(class|stage|college|working|graduate|school)\b"}
    for f, rx in fields.items():
        if re.search(rx, msg, re.I) and f in _fields(conv):
            val = _parse(f, re.sub(r"^.*?\b(?:to|is|=|:|se|ko)\b\s*", "", msg, count=1, flags=re.I)) if f != "mode" else _parse(f, msg)
            if val and re.search(r"\b(wrong|galat|incorrect|change)\b", val, re.I):
                val = None
            if val and norm(val) != norm(msg):
                conv.booking[f] = val
                return _advance(conv, hi, "Updated ✅" if not hi else "Update kar diya ✅")
            conv.booking.pop(f, None)
            conv.awaiting = f
            return _advance(conv, hi)
    text = ("Kya badalna hai — naam, number, mode ya interest?" if hi else "What should I change — name, contact, mode or interest?")
    return _send(conv, text, "open_ended", "Change requested but field unclear; asked which one.")


# ------------------------------------------------------------------ main entry
def _handle_reply_inner(body: dict) -> dict:
    conv = ensure_conv(body)
    msg = (body.get("message") or "").strip()
    with S().lock:
        conv.inbound.append(msg)
    flags = S().flag(conv.visitor_id)
    if conv.ended:
        return {"action": "end", "rationale": "Conversation already closed (goodbye already sent); not re-engaging."}
    visitor = _visitor(conv)
    hi = _hi(conv, msg, visitor)
    if not conv.offer:
        conv.offer = "session"
    kind = classify(msg, conv, flags)

    # ---------------- opt-out
    if kind == "optout":
        conv.ended = True
        flags.opted_out = True
        return {"action": "end", "rationale": "Explicit opt-out/not-interested. Closing and suppressing further proactive messages."}

    # ---------------- auto-reply: one nudge, then 24h back-off, then exit
    if kind == "auto":
        n = norm(msg)
        if n not in flags.auto_texts:
            flags.auto_texts.append(n)
        flags.auto_count += 1
        conv.auto_count += 1
        count = max(conv.auto_count, flags.auto_count)
        if count == 1:
            text = ("Lagta hai yeh auto-reply hai 🙂 Jab aap dekhein, bas YES bhej dijiye — phir hum aage badhenge."
                    if hi else f"Looks like an auto-reply 🙂 Whenever you see this, just reply YES and I'll {offer_for(visitor, False, conv.offer)}.")
            return _send(conv, text, "binary_yes_no", "Detected an automated reply (canned phrasing). One short nudge, no new pitch.")
        if count == 2:
            _backoff(conv, body.get("received_at"), 86400)
            return {"action": "wait", "wait_seconds": 86400,
                    "rationale": "Same auto-reply again — nobody at the phone. Backing off 24h instead of burning turns."}
        conv.ended = True
        return {"action": "end", "rationale": f"Auto-reply received {count}x with no human response; closing to avoid spamming."}
    flags.auto_count = 0

    # ---------------- hostile: one apology + opt-out path, then exit
    if kind == "hostile":
        conv.hostile_count += 1
        if conv.hostile_count >= 2:
            conv.ended = True
            flags.opted_out = True
            return {"action": "end", "rationale": "Repeated frustration; exiting gracefully and suppressing further messages."}
        text = ("Maaf kijiye, pareshan karne ka irada nahi tha. Agar career ke baare mein koi sawaal ho toh main yahin hoon. "
                "Message nahi chahiye toh STOP likh dijiye, phir message nahi aayega." if hi else
                "Sorry for the bother — I'll keep it short. If you have any career question, I'm right here. "
                "If you'd rather not hear from us, reply STOP and we won't message again.")
        return _send(conv, text, "binary_yes_no", "Visitor frustrated but did not opt out: brief apology, explicit opt-out path, no pitch.")

    # ---------------- session reminder: attend / reschedule
    if conv.kind == "session_reminder" and conv.stage != "done" and kind not in ("question", "offtopic", "defer", "bot_q", "menu"):
        trg = S().get("trigger", conv.trigger_id) or {}
        when = (trg.get("payload") or {}).get("session_iso")
        if RESCHEDULE.search(msg):
            conv.stage = "done"
            conv.lead_id = S().add_lead({"conversation_id": conv.conversation_id, "visitor_id": conv.visitor_id,
                                         "trigger_id": conv.trigger_id, "request_type": "reschedule",
                                         "name": (visitor.get("identity") or {}).get("name"), "original_session": when})
            text = (f"Koi baat nahi — reschedule request support team ko bhej di (ref {conv.lead_id}). Changes appointment availability "
                    "aur scheduling policy par depend karte hain; team aapse naye options ke saath contact karegi." if hi else
                    f"No problem — I've sent your reschedule request to our support team (ref {conv.lead_id}). Changes depend on "
                    "appointment availability and the scheduling policy; they'll contact you with new options.")
            return _send(conv, text, "none", "Reschedule asked: logged it and set expectations exactly as FAQ Q15 states.",
                         {"lead_id": conv.lead_id})
        if ATTEND.search(msg) or kind == "commit":
            conv.stage = "done"
            text = (f"Badhiya, confirm ho gaya ✅ {nice_date(when, True)}, {nice_time(when)} par milte hain. Kuch badle toh yahin bata dijiye."
                    if hi else f"Great, you're confirmed ✅ See you on {nice_date(when, True)} at {nice_time(when)}. "
                    "If anything changes, just reply here.")
            return _send(conv, text, "none", "Visitor confirmed attendance for the booked slot.")

    # ---------------- booking in progress: treat the message as the answer to our last question
    if conv.stage == "booking" and conv.awaiting and kind in ("other", "commit", "negative", "greeting", "thanks", "question", "pick", "human"):
        faqs = KB.match(msg, min_score=4.0) if QUESTION.search(msg) else []
        if faqs:
            return _faq_reply(conv, msg, hi, faqs)
        val = _parse(conv.awaiting, msg)
        if val:
            got = conv.awaiting
            conv.booking[got] = val
            conv.awaiting = None
            lead = ""
            if got == "name" and not conv.booking.get("for_child"):
                lead = f"Shukriya, {val}!" if hi else f"Thanks, {val}!"
            return _advance(conv, hi, lead)
        if conv.awaiting != "contact" and not conv.booking.get("contact") and (EMAIL.search(msg) or PHONE.search(msg)):
            conv.booking["contact"] = _parse("contact", msg)
            got = ("Number/email note kar liya ✅" if hi else "Got your contact ✅")
            return _send(conv, got + " " + _ask(conv, hi), "open_ended", "Visitor shared contact early; saved it and re-asked the pending detail.")
        conv.nudges += 1
        if conv.nudges >= 3:
            conv.stage, conv.awaiting = "open", None
            text = ("Koi baat nahi — jab ready hon, YES bhejiye aur hum wahin se shuru karenge." if hi
                    else "No worries — whenever you're ready, send YES and we'll pick up from there.")
            return _send(conv, text, "binary_yes_no", "Couldn't read the detail after a few tries; paused the request instead of looping.")
        hint = {"contact": ("10-digit mobile number ya email bhejiye, ya SAME." if hi else "Please send a 10-digit mobile number or an email, or reply SAME."),
                "mode": ("Bas \"online\", \"in-person\" ya \"koi bhi\" likhiye." if hi else "Just reply \"online\", \"in-person\" or \"any\"."),
                "name": ("Bas apna naam likhiye." if hi else "Just type your name.")}.get(conv.awaiting, "")
        sorry = ("Maaf kijiye, samajh nahi aaya. " if hi else "Sorry, I didn't catch that. ") if conv.nudges >= 2 else ""
        return _send(conv, sorry + (hint + " " if hint else "") + _ask(conv, hi), "open_ended", "Reply didn't fit the requested detail; asked again with a hint.")

    # ---------------- confirm step
    if conv.stage == "confirm":
        if kind in ("question",) and KB.match(msg, min_score=4.0) and not re.search(r"\b(change|update|edit|galat|wrong)\b", msg, re.I):
            return _faq_reply(conv, msg, hi, KB.match(msg, min_score=4.0))
        if re.search(r"\b(change|update|edit|galat|wrong|instead|badal|badlo)\b", msg, re.I):
            return _apply_change(conv, msg, hi)
        if CONFIRM.search(msg) and not NEGATIVE.match(msg):
            return _submit(conv, hi, visitor)
        if NEGATIVE.match(msg) or re.search(r"\bcancel\b", msg, re.I):
            conv.ended = True
            return {"action": "end", "rationale": "Visitor cancelled at the confirm step; nothing was sent to the team."}
        if kind in ("other", "greeting", "thanks", "pick", "commit", "question"):
            tail = ("CONFIRM likhiye bhejne ke liye, ya bataiye kya badalna hai." if hi
                    else "Reply CONFIRM to send it, or tell me what to change.")
            return _send(conv, tail, "binary_confirm_cancel", "Still at the confirm step; restated the single next action.")

    # ---------------- numbered menu pick
    if kind == "pick":
        idx = int(NUMBER_PICK.match(msg).group(1)) - 1
        if 0 <= idx < len(conv.menu):
            item = conv.menu[idx]
            if item.startswith("sec:"):
                text, ids = section_text(item[4:], hi)
                conv.menu = ids
                return _send(conv, text, "open_ended", "Menu: opened the chosen section's questions.")
            if item.startswith("faq:"):
                f = KB.get(item[4:])
                conv.menu = []
                out = _faq_reply(conv, msg, hi, [f])
                out["body"] += ("\n\n(MENU likhiye aur topics ke liye)" if hi else "\n\n(Type MENU for more topics)")
                conv.bot_bodies[-1] = out["body"]
                return out
        text = (f"1 se {len(conv.menu)} ke beech number bhejiye, ya apna sawaal likhiye." if hi
                else f"Please reply with a number from 1 to {len(conv.menu)}, or type your question.")
        return _send(conv, text, "open_ended", "Number out of range for the current menu.")

    if kind == "menu":
        menu, ids = menu_text(hi)
        conv.menu = ids
        text = ("Topic chuniye (number bhejiye):\n" if hi else "Pick a topic (reply with a number):\n") + menu
        return _send(conv, text, "open_ended", "Visitor asked for the menu.")

    if kind == "bot_q":
        text = ("Main Lakshay India ka virtual assistant hoon. Kisi insaan se baat karni ho toh AGENT likhiye — hum representative se "
                "callback request kar denge." if hi else "I'm Lakshay India's virtual assistant. If you'd like a real person, reply "
                "AGENT and I'll request a callback from a representative.")
        return _send(conv, text, "open_ended", "Honest about being a bot; offered a human path (FAQ Q20).")

    # ---------------- off-topic ask: decline politely, redirect
    if kind == "offtopic":
        conv.offtopic_count += 1
        mm = OFFTOPIC.search(msg)
        what = mm.group(0) if mm else "that"
        what = what.upper() if what.lower() in ("gst", "itr") else what[0].upper() + what[1:]
        if conv.offtopic_count == 1:
            text = (f"{what} mere scope se bahar hai — main Lakshay India ke career counselling sawaalon ke liye hoon. "
                    "Topics dekhne ke liye MENU likhiye, ya session ke liye YES." if hi else
                    f"{what} is outside what I can help with — I'm here for career counselling questions about Lakshay India. "
                    "Type MENU to see topics, or reply YES to request a session.")
        else:
            text = ("Us mein hum madad nahi kar payenge, sorry. Career se juda kuch bhi poochiye." if hi
                    else "I can't help with that one, sorry. Ask me anything career-related.")
        return _send(conv, text, "open_ended", "Out-of-scope request declined politely without lecturing; redirected to career topics.")

    # ---------------- deferral
    if kind == "defer":
        secs = 86400 if re.search(r"\b(kal|tomorrow|next week)\b", msg, re.I) else 7200
        _backoff(conv, body.get("received_at"), secs)
        return {"action": "wait", "wait_seconds": secs,
                "rationale": f"Visitor asked for time; backing off {secs // 3600}h before following up."}

    # ---------------- wants a human
    if kind == "human":
        lead = ("Zaroor — hum Lakshay India ke representative se call request kar dete hain." if hi
                else "Sure — I'll request a call from a Lakshay India representative.")
        return _start_booking(conv, hi, visitor, "callback", lead)

    # ---------------- yes: switch straight to action
    if kind == "commit":
        faqs = KB.match(msg, min_score=4.0)
        lead_in = "\n\n".join(KB.answer_text(f, hi) for f in faqs) if faqs and QUESTION.search(msg) else ""
        rtype = "callback" if conv.booking.get("request_type") == "callback" else "session"
        if re.search(r"\b(session|book|counsel\w*|appointment)\b", msg, re.I):
            rtype = "session"
            conv.booking["request_type"] = "session"
        return _start_booking(conv, hi, visitor, rtype, lead_in)

    if kind == "negative":
        conv.ended = True
        return {"action": "end", "rationale": "Visitor declined / nothing else needed; closing gracefully without a further pitch."}

    if kind == "greeting":
        if len(conv.bot_bodies) == 0:
            c = compose(visitor, {"kind": "new_visitor_welcome", "payload": {}}, hi=hi)
            conv.menu = c["_menu"]
            return _send(conv, c["body"], "open_ended", "First message was a greeting: welcome + topic menu.")
        text = ("Namaste! Bataiye, kaise madad karun? Topics ke liye MENU likhiye." if hi
                else "Hi again! How can I help? Type MENU to see topics.")
        return _send(conv, text, "open_ended", "Greeting mid-conversation; short and helpful.")

    if kind == "thanks":
        conv.nudges += 1
        if conv.stage == "done" or conv.nudges >= 2:
            conv.ended = True
            return {"action": "end", "rationale": "Visitor said thanks with nothing pending; closing politely."}
        text = ("Aapka swagat hai! Kuch aur poochna ho toh likhiye, ya session ke liye YES." if hi
                else "You're welcome! Ask me anything else, or reply YES to request a session.")
        return _send(conv, text, "open_ended", "Acknowledgement; one light nudge.")

    if kind == "empty":
        return {"action": "wait", "wait_seconds": 3600, "rationale": "Empty inbound; waiting for a real reply."}

    # ---------------- question / other
    faqs = KB.match(msg)
    if faqs:
        return _faq_reply(conv, msg, hi, faqs)
    if kind == "other" and conv.kind in ("career_explorer",) and len(conv.inbound) == 1 and len(norm(msg).split()) <= 12:
        # visitor answered "what do you enjoy?" — note it as their interest and move to the request
        conv.booking["interest"] = clean_interest(msg)
        lead = (f"Achha — \"{conv.booking['interest']}\" note kar liya. Counsellor isi ke hisaab se options explore karwayega."
                if hi else f"Nice — noted \"{conv.booking['interest']}\". A counsellor can explore options around that with you.")
        conv.question_count += 1
        return _send(conv, lead + " " + _cta_line(conv, hi), "binary_yes_no", "Captured the visitor's interest without re-asking; one YES CTA.")
    return _unknown(conv, msg, hi, visitor)


def handle_reply(body: dict) -> dict:
    """Every conversation closes politely: instead of a silent `end`, send one closing line (cta none),
    mark the conversation closed, and answer any later turn with `end`."""
    cid = body.get("conversation_id")
    with S().lock:
        c0 = S().convs.get(cid)
        already_closed = bool(c0 and c0.ended)
    out = _handle_reply_inner(body)
    if out.get("action") != "end" or already_closed:
        return out
    conv = S().convs.get(cid)
    msg = body.get("message") or ""
    hi = bool(conv and conv.lang_hinglish)
    if OPTOUT.search(msg) or HOSTILE.search(msg):
        text = ("Samajh gaye — ab aapko message nahi karenge. " if hi else "Understood — we won't message you again. ") + _closing(hi)
    elif conv and conv.auto_count:
        text = ("Koi baat nahi — jab free hon, bas 'Hi' bhej dijiye. " if hi else "No worries — whenever you're free, just send 'Hi'. ") + _closing(hi)
    elif conv and conv.stage == "confirm":
        text = ("Theek hai, kuch nahi bheja gaya. " if hi else "Okay — nothing has been sent. ") + _closing(hi)
    else:
        text = _closing(hi)
    if conv:
        conv.ended = True
        conv.bot_bodies.append(text)
    return {"action": "send", "body": text, "cta": "none",
            "rationale": out.get("rationale", "") + " Closing with a single polite goodbye (no CTA); no further messages on this conversation."}
