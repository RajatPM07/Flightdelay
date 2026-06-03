# Claude Code — Opening Prompt: TripSecure+ Flight Delay MVP

You are building the **MVP for ICICI Lombard's TripSecure+ Flight Delay notification service**
(Product Code 4233). Read this entire brief before writing code. Build in the milestone order
given. Ask me before adding heavy dependencies or changing the schema/spec.

## What we're building
A standalone service that watches each insured flight and sends the customer a notification
whenever something **material** changes (delay crossing a threshold, cancellation, diversion,
departure with new ETA, landed). It moves NO money. Monitoring + notification only.

**Explicit non-goals (do NOT build):** automated payouts, bank/IMPS/NEFT integration, the
"is the delay long enough to pay" decision, fraud/anti-selection enforcement, travel verification.
Leave clean extension points for these (Phase 2) but implement none of them.

## Stack (decided — don't second-guess unless you hit a blocker)
- Python 3.11+, FastAPI, Uvicorn, httpx, pydantic-settings
- Supabase Postgres via SQLModel (use create_all for MVP; note Alembic as a later step)
- APScheduler (in-process) for the STA+24h backstop and deregistration
- Live feed: FlightAware AeroAPI — Alerts (push webhooks) for change events + GET /flights/{ident}
  for the baseline snapshot — behind a `FlightDataProvider` interface
- Messaging: `MessageProvider` interface; Twilio for WhatsApp + SMS, SendGrid/SMTP for email

## Core architecture (keep separate)
1. **Issuance API** — `POST /policies`: PNR, flight no., journey date, contact + consent ->
   create policy, baseline snapshot, register flight for alerts.
2. **Webhook receiver** — `POST /webhooks/flightaware`: receive push alerts, normalise, hand to brain.
3. **The Brain** (`app/domain/`) — PURE, I/O-free decision engine: state machine + materiality
   rules + dedup + out-of-order handling + two-feed reconciliation. Fully unit-testable.
4. **Notifier** — turns a brain `Decision` into a templated message via `MessageProvider`.
5. **Scheduler** — APScheduler: STA+24h backstop force-close; deregister on terminal states.
6. **Persistence + Audit** — Postgres tables; an immutable decision log.

## The state machine (implement exactly)
States: `REGISTERED, ON_TIME, DELAYED_T1, DELAYED_T2, DELAYED_T3, DEPARTED, LANDED, CANCELLED,
DIVERTED, CLOSED`. Thresholds are CONFIGURABLE (env/DB, never hardcoded): T1=30, T2=60, T3=120
min; recovery buffer=15 min; backstop=24h. The brain notifies ONLY on specific transitions.

Pure function:
`decide(current_state, last_known_status, incoming_status, config) -> Decision(new_state,
should_notify, event_type, message_context, payout_recommendation=None)`

Rules:
- **Dedup/idempotency:** ignore an incoming event whose `event_ts` <= last stored update_ts
  (out-of-order), or whose content hash equals the last processed hash (duplicate retry).
- **Delay calc:** delay_minutes = (best available arrival estimate, else actual_in) - scheduled_in.
  Use gate arrival (chocks-on / `actual_in`), NOT runway touchdown (`actual_on`).
- **Tiers with hysteresis:** escalate when delay crosses the next threshold up. Emit "back on
  schedule" only when delay drops below a tier minus the recovery buffer. Never re-notify on
  same-tier wobble.
- **Branch/terminal events:** cancellation, diversion, departed-with-new-ETA, landed each map to
  a notifiable event type.
- **Notifiable set:** tier escalations, recovery-to-on-time, cancelled, diverted, departed,
  landed. Everything else -> log silently, no notification.

## Canonical test (write/finish FIRST — must pass before any I/O)
`tests/test_brain.py` feeds this 7-event sequence for one flight (STA 14:30) and asserts EXACTLY
3 notifications and a final state of LANDED:
1. baseline on-time -> no notify
2. est +25 min -> no notify (under T1)
3. est +70 min -> NOTIFY (crosses T2), state DELAYED_T2
4. est +65 min -> no notify (same-tier wobble)
5. duplicate of #4 -> no notify (dedup)
6. departed, est +140 min -> NOTIFY (crosses T3), state DEPARTED
7. landed +138 min -> NOTIFY landed, state LANDED, then deregister

## Data model (PII isolation is mandatory — DPDP)
- `policy_pii` (encrypted): policy_id -> name, phone, email. ONLY this table holds PII.
- `policies`: policy_id (anon token, PK), pnr, flight_number, flight_date, scheduled_in_utc,
  scheduled_in_tz_offset, consent_ts, status, created_at.
- `flight_state`: policy_id, current_state, last_known_status (JSON), last_update_ts,
  last_event_hash, provider.
- `event_log` (immutable, append-only): id, policy_id, received_ts, source, raw_payload,
  prev_state, new_state, decided_event_type, notified, notification_id.
- `notifications`: id, policy_id, channel, template, body, provider_msg_id, status, sent_ts.
All tracking tables and logs reference ONLY policy_id — never PII.

## Hard guardrails
- **Timezone:** store STA with local offset, convert to UTC on ingest, do ALL scheduling and
  comparisons in UTC. Known failure point — get it right.
- **Idempotency** on webhook processing and notification send.
- **No magic numbers** — config only.
- **Brain purity** — `app/domain/` imports no vendor SDK, no DB, no httpx.
- **Phase-2 seam** — `Decision.payout_recommendation` exists but is always `None`; build nothing
  behind it.
- **Secrets** in `.env` (see `.env.example`); never commit real keys.

## Build order (verify each before moving on; commit per milestone)
1. Scaffold, config, DB models, `/health`
2. **Domain core** — make the 7-event test green, NO I/O. Priority.
3. Provider interfaces + FlightAware adapter (baseline GET + alert registration)
4. Issuance API: create policy -> baseline snapshot -> register alert
5. Webhook receiver -> normalise -> brain -> persist state -> audit log
6. Notifier: Twilio WhatsApp/SMS + email; map event types to templates
7. Scheduler: STA+24h backstop + deregister on terminal states
8. End-to-end run against the FlightAware sandbox

## Real-world frictions (flag to me, don't silently work around)
- AeroAPI Alerts need a publicly reachable webhook URL — use ngrok for local dev.
- WhatsApp via Twilio needs sandbox join (dev) and an approved template/sender (prod). If approval
  blocks you, fall back to SMS/email and tell me.
- AeroAPI is priced per result set; log every external call so we can measure real per-policy cost.

Start with milestone 1, then build and prove milestone 2 (the brain + the 7-event test) before
touching any external API.
