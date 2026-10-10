---
title: Futures Paper
emoji: 📈
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
---

# Futures paper

One Nifty spot strike, UT Bot and B-Xtrender side by side, paper fills only. The page and the API run in this Space.

## Each morning

1. Open the Space.
2. Click **Log in with Zerodha** and finish their login.
3. Check the indicators and press Start.
4. Leave the Space awake until you press Stop.

The Zerodha token lasts one trading day. The paper books live in memory. If the Space sleeps or restarts, that day's trades are gone.

## Before the first deploy

Set these as Space secrets, not in git:

- `ZERODHA_API_KEY`
- `ZERODHA_API_SECRET`
- `ZERODHA_REDIRECT_URL` = `https://<your-space>.hf.space/api/v1/auth/callback`
- `GROQAPIKEY` so a pasted Pine script can be converted. The Space name box allows only letters and numbers, so this name has no underscores. Restart the Space after adding it.

On [developers.kite.trade](https://developers.kite.trade), set that same callback URL as the app redirect.

In the Space settings, pick hardware that can stay awake and set sleep to never. A sleeping Space drops the Kite tick stream.

Quantity defaults to 1 lot of 65. Override with `STRATEGY_LOTS` or `NIFTY_OPTION_LOT_SIZE` if that changes.

## Local run

```bash
pip install -r requirements.txt
```

Put the three Zerodha variables in a `.env` file in this folder, then:

```bash
uvicorn app:app --host 127.0.0.1 --port 7860
```

Open http://127.0.0.1:7860. For a local login, the Kite redirect URL has to be `http://127.0.0.1:7860/api/v1/auth/callback`.
