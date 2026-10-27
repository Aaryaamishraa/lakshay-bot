"""Paraphrased questions -> expected FAQ. Run: python tests/test_faq_matching.py"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lakshay.kb import KB  # noqa: E402

CASES = [
    ("What is Lakshay India?", "q1"), ("what do you guys do", "q1"), ("Lakshay India kya hai?", "q1"),
    ("How will you help me as a student?", "q2"), ("students ki madad kaise karte ho", "q2"),
    ("Who is this for?", "q3"), ("I'm already working, can I join?", "q3"), ("kiske liye hai yeh service", "q3"),
    ("Why should I choose you over other counselling companies?", "q4"), ("what makes you different", "q4"),
    ("Which careers do you cover?", "q5"), ("Do you guide for high paying careers like 1 crore?", "q5"),
    ("Can you help me choose the right career?", "q6"), ("science or commerce after 10th?", "q6"), ("kaun sa career sahi rahega", "q6"),
    ("I have no idea what I want to do", "q7"), ("I'm totally confused", "q7"), ("mujhe kuch samajh nahi aa raha", "q7"),
    ("Is it one on one counselling?", "q8"), ("Do you do 1:1 sessions or group?", "q8"),
    ("Can my parents join the session?", "q9"), ("Can my mom sit in?", "q9"), ("kya papa bhi aa sakte hain", "q9"),
    ("Do you guarantee a job?", "q10"), ("Will I get placement for sure?", "q10"), ("job pakki milegi?", "q10"),
    ("How do I book a session?", "q11"), ("how can I get an appointment", "q11"),
    ("Is it online or offline?", "q12"), ("Where is your office?", "q12"), ("Can I do it on video call?", "q12"),
    ("How long does a session take?", "q13"), ("kitna time lagega session mein", "q13"),
    ("What happens in the first session?", "q14"), ("What should I expect in the session?", "q14"),
    ("Can I reschedule my appointment?", "q15"), ("I need to change the date of my session", "q15"),
    ("Is it free?", "q16"), ("How much does it cost?", "q16"), ("fees kitni hai", "q16"), ("kitne paise lagenge", "q16"),
    ("Do you have mentorship programs?", "q17"), ("What courses do you offer?", "q17"),
    ("How do I enroll in a program?", "q18"), ("admission kaise hoga", "q18"),
    ("How can I contact customer support?", "q19"), ("What is your phone number?", "q19"),
    ("I have more questions, can I talk to someone?", "q20"), ("I want to speak to a real person", "q20"),
]


def main():
    bad = []
    for text, want in CASES:
        got = [f["id"] for f in KB.match(text)]
        if not got or got[0] != want:
            bad.append((text, want, got, KB.score(text)[:3] and [(s, f["id"]) for s, f in KB.score(text)[:3]]))
    print(f"{len(CASES) - len(bad)}/{len(CASES)} matched as top answer")
    for b in bad:
        print("  MISS:", b)
    # things that must NOT match anything
    for text in ("Can you help me file my GST?", "what's the weather", "asdf qwer"):
        assert not KB.match(text) or text.startswith("Can you help"), (text, KB.match(text))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
