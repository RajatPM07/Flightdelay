# TripSecure+ Flight Delay — Monitoring & Notification MVP

In-house service that watches each insured flight (Product Code **4233**) and notifies the
customer whenever something **material** changes — a delay crossing a threshold, cancellation,
diversion, departure with a new ETA, or landing.

> **Scope: monitoring + notification only. This service moves NO money.**
> Automated payouts, bank/IMPS-NEFT, the pay/no-pay decision, fraud / anti-selection
> enforcement, and travel verification are **Phase 2** and are deliberately *not* built here.
> Clean extension points are left for them.

## Why this shape
The data feed is a commodity (anyone can rent radar). The asset is **the brain** — the in-house
decision layer that remembers each flight's last-known status and applies our rules to decide
what (if anything) to tell the customer. Swap the vendor and the brain behaves identically.

## Architecture
| Component | Responsibility |
|---|---|
| **Issuance API** | `POST /policies` — capture PNR, flight no., date, contact + consent; baseline snapshot; register flight for alerts |
| **Webhook receiver** | `POST /webhooks/flightaware` — receive push alerts, normalise, hand to the brain |
| **The Brain** (`app/domain/`) | PURE, I/O-free decision engine: state machine + materiality rules + dedup + out-of-order handling + two-feed reconciliation |
| **Notifier** | Turn a brain `Decision` into a templated WhatsApp / SMS / email message |
| **Scheduler** | STA+24h backstop force-close; deregister on terminal states |
| **Persistence + Audit** | Postgres tables; immutable decision log |

## State machine
`REGISTERED -> ON_TIME -> DELAYED_T1/T2/T3 -> DEPARTED -> LANDED`, with side-branches to
`CANCELLED` / `DIVERTED`, a recovery path back to `ON_TIME`, and `CLOSED` on deregistration.
Thresholds (T1=30, T2=60, T3=120 min; backstop=24h) are **configurable**, never hardcoded.
The brain notifies **only on specific transitions**, never on every ping.

## Stack
Python 3.11+ · FastAPI · SQLModel + Supabase Postgres · APScheduler · httpx ·
FlightAware AeroAPI (live feed, behind an interface) · Twilio (WhatsApp/SMS) + SendGrid/SMTP (email).

## Quickstart
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # fill in keys
pytest tests/test_brain.py    # the canonical 7-event spec (build the brain until this is green)
uvicorn app.main:app --reload
```
For local AeroAPI alerts you need a public URL — run `ngrok http 8000` and set
`PUBLIC_WEBHOOK_BASE_URL`.

## Build order (see `claude_code_prompt.md` for the full brief)
1. Scaffold, config, DB models, `/health`
2. **Domain core** — make `tests/test_brain.py` (7-event sequence -> exactly 3 notifications, final `LANDED`) pass, with NO I/O
3. Provider interfaces + FlightAware adapter
4. Issuance API
5. Webhook receiver -> brain -> persist -> audit log
6. Notifier (Twilio + email)
7. Scheduler (backstop + deregister)
8. End-to-end against the FlightAware sandbox

## Guardrails (non-negotiable)
- **PII isolation:** only `policy_pii` holds name/phone/email. All tracking tables and logs reference the anonymised `policy_id` only. (DPDP.)
- **Timezone discipline:** store STA with local offset, convert to UTC on ingest, do ALL scheduling/comparisons in UTC.
- **Idempotency** on webhook processing and notification send.
- **No magic numbers** — thresholds/buffers/backstop from config.
- **Brain purity** — no vendor SDK, DB, or httpx inside `app/domain/`.
