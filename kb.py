"""FAQ knowledge base: loads data/faq.json and matches a visitor's question to the right answer(s).

Matching is deliberately simple and explainable:
  * hand-written keyword phrases per FAQ (English + Hinglish) — longer phrases score higher;
  * plus word overlap with the FAQ's own question, with very common words down-weighted.
Every answer the bot gives comes word-for-word from faq.json, so nothing is made up.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .util import norm

DATA = Path(__file__).resolve().parent.parent / "data"

STOP = set("a an the is are was were be to of in on for with and or but this that these those it its my your our we you "
           "i me can could would should will do does did what how why when which who too also just only about any some more "
           "please pls tell kya hai hain ho ka ki ke se ko mein main hum aap get have has there their them if so".split())
COMMON = {"lakshay", "india", "career", "careers", "counselling", "session", "student", "team", "help", "program"}

ALIASES = [(r"\bcounsel+ing\b", "counselling"), (r"\bcouns[ae]l+or\b", "counsellor"), (r"\bprogramme\b", "program"),
           (r"\bpls\b", "please"), (r"\bu\b", "you"), (r"\bur\b", "your"), (r"\bcareer counsel\w*", "career counselling")]


def _prep(text: str) -> str:
    t = (text or "").lower()
    for a, b in ALIASES:
        t = re.sub(a, b, t)
    return " " + norm(t) + " "


def _stem(w: str) -> str:
    for suf in ("ing", "ed", "es", "s"):
        if len(w) > 4 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def _tokens(text: str) -> set[str]:
    return {_stem(w) for w in _prep(text).split() if w not in STOP and len(w) > 2}


class KnowledgeBase:
    def __init__(self, path: Path = DATA / "faq.json"):
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        self.company = raw.get("company", "Lakshay India")
        self.sections = raw["sections"]
        self.faqs = raw["faqs"]
        self.by_id = {f["id"]: f for f in self.faqs}
        self._kw = {f["id"]: [(" " + norm(_prep(k)) + " ", len(k.split())) for k in f.get("keywords", [])] for f in self.faqs}
        self._qt = {f["id"]: _tokens(f["question"]) for f in self.faqs}

    # ------------------------------------------------------------ lookups
    def get(self, fid: str) -> dict | None:
        return self.by_id.get(fid)

    def section(self, sid: str) -> dict | None:
        return next((s for s in self.sections if s["id"] == sid), None)

    def in_section(self, sid: str) -> list[dict]:
        return [f for f in self.faqs if f["section"] == sid]

    def number(self, fid: str) -> int:
        return int(re.sub(r"\D", "", fid) or 0)

    # ------------------------------------------------------------ matching
    def score(self, text: str) -> list[tuple[float, dict]]:
        q = _prep(text)
        qt = _tokens(text)
        out = []
        for f in self.faqs:
            s = 0.0
            for phrase, nwords in self._kw[f["id"]]:
                if phrase in q:
                    s += 2 + nwords
            for t in qt & self._qt[f["id"]]:
                s += 0.3 if t in COMMON else 1.0
            if s > 0:
                out.append((round(s, 2), f))
        out.sort(key=lambda x: -x[0])
        return out

    def match(self, text: str, min_score: float = 3.0) -> list[dict]:
        """Best FAQ, plus a second one when the question clearly asks about two things."""
        ranked = self.score(text)
        if not ranked or ranked[0][0] < min_score:
            return []
        best = [ranked[0][1]]
        if len(ranked) > 1 and ranked[1][0] >= 3.0 and ranked[1][0] >= 0.5 * ranked[0][0]:
            best.append(ranked[1][1])
        return best

    def answer_text(self, f: dict, hinglish: bool) -> str:
        return (f.get("answer_hi") if hinglish and f.get("answer_hi") else f["answer"]).strip()

    def all_text(self) -> str:
        return " ".join(f["question"] + " " + f["answer"] + " " + (f.get("answer_hi") or "") for f in self.faqs)


KB = KnowledgeBase()
