# Pranav Yadav DTU Lakshay India Chatbot

A WhatsApp-style career counselling assistant for **Lakshay India**. It answers visitors' questions from Lakshay India's official FAQ (20 questions across 5 sections), in English or Hinglish, and turns a "yes" into a counselling request for the team — asking only for details it doesn't already have. It also handles "not now", off-topic asks, auto-replies, frustration and "stop", and closes every chat politely.

**Built by Pranav Yadav** — B.Tech, Mathematics & Computing, Delhi Technological University (DTU).

## Try it
Open the base URL in a browser for the chat page: pick a visitor and a starting point (welcome, enquiry follow-up, parent enquiry, session reminder…), see the opening message, then reply as the visitor. "All 20 questions" lets you ask any FAQ in one tap; "Requests sent to team" shows what the bot handed over.

## API
| Endpoint | What it does |
|---|---|
| `POST /v1/start` | Open a chat (optionally for a stored visitor / starting point) and get the first message |
| `POST /v1/reply` | Handle the visitor's message → `send` / `wait` / `end` |
| `POST /v1/context` | Store/update a visitor or starting point (versioned, idempotent) |
| `POST /v1/tick` | Decide which proactive follow-ups to send right now |
| `GET /v1/faq` | The knowledge base |
| `GET /v1/leads` | Counselling / callback / reschedule requests collected |
| `GET /v1/healthz`, `GET /v1/metadata` | Liveness and bot info |

## How it works
- **Knowledge base** (`data/faq.json`) — the 20 Lakshay India FAQs, each with an English answer (word-for-word), a Hinglish version and matching keywords. Edit this file to change what the bot says.
- **Matcher** (`lakshay/kb.py`) — keyword phrases plus word overlap; answers two FAQs at once when a question asks two things ("how much does it cost and how long is it?"). If nothing matches, it says it won't guess and offers a representative callback.
- **Conversation engine** (`lakshay/replies.py`) — "yes" → straight into a short request flow (name, current stage, interests, online/in-person, contact) that skips anything already known, then one CONFIRM; numbered menu; reminders with 1 = attend / 2 = reschedule; auto-reply → one nudge, then wait, then goodbye; "stop" / rude → apologise and stop; off-topic (GST, loans…) → polite redirect; Hinglish detected per message; every chat ends with "Thanks, and have a nice day! 🙏".
- **Openers** (`lakshay/composer.py`) — one per starting point; every fact comes from the visitor's data or the FAQ, never invented fees, durations, addresses or promises.
- **Optional LLM** (`lakshay/llm.py`) — set `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` to answer questions the matcher can't place, strictly from the FAQ; output with links, promise words or numbers not in the FAQ is rejected.

## Run locally
```bash
pip install -r requirements.txt
uvicorn lakshay.app:app --port 8080 --workers 1
python tests/test_faq_matching.py   # 49 paraphrased questions → right FAQ
python tests/harness.py             # API end-to-end
python tests/replay.py              # multi-turn conversations
```

## Deploy
- **GitHub Codespaces**: Code → Codespaces → Create codespace. The bot starts automatically on port 8080 (set the port to Public); the link is written to `PUBLIC_URL.txt`.
- **Render / Railway / Fly / Docker**: `render.yaml`, `Procfile` and `Dockerfile` are included. Keep it to one worker — state is held in memory.

## What I'd add next
Real appointment slots from Lakshay India's calendar, a small database so requests survive restarts, and a WhatsApp Business API connection.
