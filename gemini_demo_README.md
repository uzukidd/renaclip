# Gemini-API Demo

Demo for [HanaokaYuzu/Gemini-API](https://github.com/HanaokaYuzu/Gemini-API) (reverse-engineered Python API for Google Gemini web app).

## Setup

- Python 3.10+
- Install deps:

```bash
pip install -r requirements-gemini-demo.txt
```

## Authentication

**Option A – Cookies (env)**

1. Open https://gemini.google.com and log in.
2. F12 → Network → refresh → copy cookie values for `__Secure-1PSID` and `__Secure-1PSIDTS`.
3. Set env before running:

```bash
set GEMINI_1PSID=your_1PSID_value
set GEMINI_1PSIDTS=your_1PSIDTS_value
```

**Option B – Browser cookies**

- Install `browser-cookie3` (included in `requirements-gemini-demo.txt`).
- Log in to https://gemini.google.com in your browser. The demo will use those cookies; no env needed.

## Run

```bash
# Single-turn (default prompt)
python gemini_demo.py

# Single-turn with custom prompt
python gemini_demo.py Tell me a short joke

# Streaming
python gemini_demo.py --stream
python gemini_demo.py --stream Explain async in one paragraph

# Multi-turn chat (default: remember name + ask name)
python gemini_demo.py --chat

# Multi-turn with your messages
python gemini_demo.py --chat "What is 2+2?" "And multiply that by 3"
```

## Reference

- [Gemini-API GitHub](https://github.com/HanaokaYuzu/Gemini-API)
- [PyPI: gemini-webapi](https://pypi.org/project/gemini-webapi/)
