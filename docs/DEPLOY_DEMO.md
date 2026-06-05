# Deploying the shareable demo

This deploys the demo with **no paid plans and no vendor keys**: flight data and
messaging are mocked, and storage falls back to on-container SQLite. Anyone with the
URL can issue a policy and drive the simulate buttons.

> Demo mode serves the UI at `/` and the `/demo/simulate` route. With `DEMO_MODE=False`
> both 404, so this image is demo-only by design.

---

## Option A — Render (persistent public URL, free) — recommended

1. Push this branch to GitHub (already the trunk: `claude/flightdelay-mvp-scaffold-EvfpU`).
2. Go to <https://dashboard.render.com> → **New → Blueprint**.
3. Connect the `RajatPM07/Flightdelay` repo. Render reads `render.yaml` and creates the
   web service with the demo env vars already set.
4. Click **Apply**. First build takes a few minutes; you then get
   `https://tripsecure-flightdelay-demo.onrender.com`.

Notes:
- Free plan **spins down after ~15 min idle**; the first request after that cold-starts
  (~50s). Fine for a demo. The in-process backstop scheduler pauses while spun down —
  irrelevant for the demo (it uses the simulate buttons, not real feeds).
- Each redeploy/restart resets the SQLite DB → a clean demo every time.

## Option B — instant share from your machine (no deploy)

If you just need a link for the next 30 minutes, tunnel the local server:

```bash
DEMO_MODE=True MOCK_PROVIDERS=True MOCK_MESSAGING=True \
  .venv/bin/python -m uvicorn app.main:app --port 8000 &
ngrok http 8000   # share the https URL it prints
```

Stays up only while your machine and the tunnel run.

## Option C — any container host (Railway / Fly.io / Cloud Run)

The `Dockerfile` is portable and binds to `$PORT`. Point the host at it and set the same
four env vars (`DEMO_MODE=True`, `MOCK_PROVIDERS=True`, `MOCK_MESSAGING=True`, leave
`DATABASE_URL` unset).

---

## Optional: real WhatsApp in the deployed demo

The demo records notifications without delivering them. To send via the Twilio **WhatsApp
sandbox** instead, set on the service:

- `MOCK_MESSAGING=False`
- `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`
- `TWILIO_WHATSAPP_FROM=whatsapp:+14155238886`
- `TWILIO_SMS_FROM=<your trial number>`

Delivery only works to numbers that have joined the sandbox (`join <code>` to
+1 415 523 8886) within the last 24h. Keep these secrets in the host's env store — never
commit them.
