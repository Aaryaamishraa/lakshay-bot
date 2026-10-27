"""Small helpers: dates, language detection, text normalisation."""
from __future__ import annotations

import re
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))


# ---------------------------------------------------------------- dates
def parse_dt(s) -> datetime | None:
    if not s or not isinstance(s, str):
        return None
    try:
        s2 = s.strip().replace("Z", "+00:00")
        if len(s2) == 10:
            return datetime.fromisoformat(s2).replace(tzinfo=IST)
        dt = datetime.fromisoformat(s2)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def nice_date(s, with_dow=False) -> str:
    dt = parse_dt(s)
    if not dt:
        return str(s)
    dt = dt.astimezone(IST)
    out = f"{dt.day} {dt.strftime('%b')}"
    if with_dow:
        out = f"{dt.strftime('%a')}, {out}"
    return out


def nice_time(s) -> str:
    dt = parse_dt(s)
    if not dt:
        return ""
    dt = dt.astimezone(IST)
    h = dt.hour % 12 or 12
    m = f":{dt.minute:02d}" if dt.minute else ""
    return f"{h}{m} {'AM' if dt.hour < 12 else 'PM'}"


# ---------------------------------------------------------------- language
HINDI_TOKENS = {
    "hai", "hain", "kya", "nahi", "nahin", "haan", "han", "karo", "kar", "karna", "mujhe", "aap",
    "aapka", "aapki", "chahiye", "theek", "thik", "acha", "accha", "achha", "bhai", "ji", "bhejo",
    "bhej", "kaise", "kab", "kitna", "kitne", "abhi", "baad", "mein", "hum", "humara", "hamara",
    "mera", "meri", "sab", "jaldi", "chalo", "chalega", "dekho", "batao", "bataiye", "samjha",
    "shukriya", "dhanyavaad", "haanji", "ho", "gaya", "raha", "rahi", "wala", "wali", "koi",
    "karun", "karu", "banu", "beta", "beti", "kaun", "konsa", "kaunsa", "lagega", "milegi", "naam",
    "hoon", "hu", "hun", "kuch", "bhi", "namaste", "namaskar", "pranam", "liye", "ke", "ki", "ka", "se", "par", "ya", "aur", "toh", "to",
}
EN_WORDS = {"the", "and", "please", "what", "how", "is", "are", "you", "your", "can", "will", "this", "that",
            "me", "my", "we", "our", "it", "for", "with", "do", "does", "not", "want", "need", "about",
            "i", "a", "hi", "hello", "have", "would", "like", "much", "should", "which", "there"}


def is_hinglish_text(text: str) -> bool:
    if not text:
        return False
    if re.search(r"[ऀ-ॿ]", text):
        return True
    words = re.findall(r"[a-zA-Z]+", text.lower())
    if not words:
        return False
    hits = sum(1 for w in words if w in HINDI_TOKENS and w not in ("to", "ho", "ka", "se", "par"))
    en = sum(1 for w in words if w in EN_WORDS)
    return (hits >= 2 and hits >= en) or (hits >= 1 and len(words) <= 3 and en == 0)


def is_english_text(text: str) -> bool:
    words = re.findall(r"[a-z]+", (text or "").lower())
    return sum(1 for w in words if w in EN_WORDS) >= 2


def prefers_hinglish(visitor: dict | None) -> bool:
    pref = str(((visitor or {}).get("identity") or {}).get("language_pref", "")).lower()
    return pref.startswith("hi") or "hindi" in pref


# ---------------------------------------------------------------- text
def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9₹+]+", " ", (s or "").lower()).strip()


def first_name(visitor: dict | None) -> str:
    n = str(((visitor or {}).get("identity") or {}).get("name") or "").strip()
    return n.split()[0] if n else ""
