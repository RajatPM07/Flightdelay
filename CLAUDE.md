# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

**TripSecure+ Flight Delay** — the monitoring + notification MVP for ICICI Lombard's flight-delay
product (Product Code **4233**). It watches each insured flight and notifies the customer when
something **material** changes (delay crossing a threshold, cancellation, diversion, departure with
a new ETA, landing).

**It moves NO money.** Explicit non-goals — do NOT build: automated payouts, bank/IMPS/NEFT, the
"is the delay long enough to pay" decision, fraud/anti-selection, travel verification. These are
Phase 2; leave clean seams, build none of them. `Decision.payout_recommendation` is a seam that is
always `None` — never read or build behind it. No payout/claim/compensation language anywhere in
customer-facing text.

## Commands

Always use the venv interpreter; **never** bare `python` (the brain is fine on 3.11+, the venv is 3.14).

```bash
# Run the full suite (was 112 passing at last handoff)
.venv/bin/python -m pytest -q

# Run the canonical brain spec (the product contract — keep it green)
.venv/bin/python -m pytest tests/test_brain.py

# Run a single test
.venv/bin/python -m pytest tests/test_webhook_fanout.py::test_name -q

# Demo server (self-contained, no keys, NO real messages sent)
DEMO_MODE=True MOCK_PROVIDERS=True .venv/bin/python -m uvicorn app.main:app --port 8000
# open http://localhost:8000/  → issue a policy, click simulate buttons

# Demo with REAL messaging but mocked flight data
DEMO_MODE=True MOCK_PROVIDERS=True MOCK_MESSAGING=False .venv/bin/python -m uvicorn app.main:app --port 8000

# Synthetic E2E (demo server must be up)
.venv/bin/python scripts/e2e_smoke.py          # FlightAware synthetic E2E
.venv/bin/python scripts/e2e_aerodatabox.py    # AeroDataBox fan-out synthetic E2E
.venv/bin/python scripts/check_env.py          # validate .env

# Lint (ruff, line-length 100)
.venv/bin/ruff check .
```

For live AeroAPI alerts you need a public URL: `ngrok http 8000` and set `PUBLIC_WEBHOOK_BASE_URL`.

## Architecture

The data feed is a commodity; the asset is **the brain** — a pure, I/O-free decision engine. Swap
the vendor and the brain behaves identically. Data flows: **issuance** registers a flight and
captures a baseline → vendor pushes an alert to a **webhook receiver** → the receiver normalizes and
hands a `FlightStatus` to the **brain** → the brain returns a `Decision` → on `should_notify` the
**notifier** templates a message → everything is persisted and audited.

### The brain — `app/domain/` (do not modify lightly; it IS the product)
- `states.py` — state machine, `BrainConfig`, `TERMINAL_STATES`
- `materiality.py` — `tier_for_delay`
- `brain.py` — `FlightStatus`, `decide(current_state, last_known_status, incoming_status, config) -> Decision`

States: `REGISTERED, ON_TIME, DELAYED_T1, DELAYED_T2, DELAYED_T3, DEPARTED, LANDED, CANCELLED,
DIVERTED, CLOSED`. `decide()` is pure and fully unit-tested. **Contract:** `tests/test_brain.py` — a
7-event sequence → **exactly 3 notifications**, final state `LANDED`. If you change the brain, that
test is the spec.

Rules baked in: dedup/idempotency (drop events with `event_ts <= last stored` or duplicate content
hash); delay = best arrival estimate (gate arrival / `actual_in`, NOT runway touchdown) − scheduled;
tier hysteresis (escalate on crossing up, "back on schedule" only when delay drops below tier minus
recovery buffer; never re-notify same-tier wobble); branch/terminal events for cancel/divert/depart/
land. Notifiable set: tier escalations, recovery, cancelled, diverted, departed, landed — everything
else logs silently. **DIVERTED is intentionally NOT terminal** (a diverted flight may still land;
the STA+24h backstop force-closes it) — don't "fix" it without checking the domain owner.

### Services — `app/services/`
`issuance.py`, `webhook.py` (`_process_for_policy` + fan-out), `notifier.py`, `scheduler.py`
(STA+24h backstop), `subscriptions.py` (reference counting), `simulator.py` (demo events),
`audit.py`.

### Providers — two vendors, one interface
`FlightDataProvider` (ABC in `app/providers/flightdata/base.py`) is the seam. Selection in
`app/deps.get_flight_provider()`: `MOCK_PROVIDERS=True` → mock; else `FLIGHT_PROVIDER=aerodatabox`
→ AeroDataBox; else FlightAware (default).

- **FlightAware AeroAPI** — per-policy push Alerts, routed by `alert_id`. `subscription_scope = "per_policy"`.
- **AeroDataBox** — subscribes per **flight number** (no date filter), so one subscription is shared
  across many policies. `subscription_scope = "per_flight_number"`. This drove three mechanisms:
  1. `flight_subscriptions` table + `subscriptions.py` reference counting — subscribe once per number,
     deregister only when the LAST active policy on that number closes (the scheduler backstop respects this too).
  2. A fan-out route `POST /webhooks/aerodatabox/{secret}` resolves the payload to ALL active policies
     for that number+date and runs `_process_for_policy` per policy.
  3. Secret-token-in-URL auth (AeroDataBox does not sign webhooks).

Messaging providers (`app/providers/messaging/`): `twilio.py`, `email.py`, selected in
`app/deps.get_msg_provider()`. `mock.py` is the offline flight+messaging provider.

### API / models / config
- `app/api/` — `policies.py` (`POST /policies`, `GET /policies/{id}`), `webhooks.py`
  (`/webhooks/flightaware`, `/webhooks/aerodatabox/{secret}`), `demo.py` (`GET /`, `POST /demo/simulate`).
- `app/models.py` — `policies`, `policy_pii`, `flight_state`, `event_log`, `notifications`, `flight_subscriptions`.
- `app/config.py` — `Settings` (pydantic-settings); `app/deps.py` — DI + provider selection.
- Frontend: one self-contained `app/static/demo.html` (Tailwind Play CDN + vanilla JS + GSAP), served
  by FastAPI. No build step.

### Stack
Python 3.11+, FastAPI, Uvicorn, httpx, pydantic-settings. SQLModel over Supabase Postgres
(`create_all` for MVP; Alembic is a later step). APScheduler (in-process) for the backstop and
deregistration.

## Hard rules (reviews enforce these)

- **PII isolation (DPDP):** ONLY `policy_pii` holds name/phone/email. Every other table and log
  references the anonymous `policy_id` only. `GET /policies/{id}` masks contact server-side
  (`_mask_phone`/`_mask_name`, India +91 oriented) and never returns raw phone/email or `raw_payload`.
- **Brain purity:** `app/domain/` imports no vendor SDK, no DB, no httpx. Keep it that way.
- **No magic numbers:** thresholds/buffers/backstop come from config (`DELAY_T1/T2/T3_MIN`,
  `RECOVERY_BUFFER_MIN`, `BACKSTOP_HOURS`; defaults 30/60/120/15/24). Even the demo simulator derives
  its delay deltas from config (tier midpoints) so they never land in the wrong tier.
- **Idempotency:** on webhook processing (brain dedup) and notification send (notifier checks
  `EventLog.notification_id`).
- **Timezone discipline:** store STA with local offset, convert to UTC on ingest, do ALL
  scheduling/comparison in UTC. SQLite round-trips can drop tz — coerce naive→aware before comparing.
- **Secrets:** real values live ONLY in gitignored `.env`. `.env.example` is committed and stays placeholders.
- **Any new teardown path must branch on `subscription_scope`** (per_policy → `deregister_alert`;
  per_flight_number → `release_subscription`) or it will kill monitoring for other policies on the flight.

## Repo conventions

- **Working branch IS the trunk:** `claude/flightdelay-mvp-scaffold-EvfpU` (no `main`/`master`;
  `origin/HEAD` points here).
- Plan-first for non-trivial work; TDD (write the failing test first); commit per logical unit;
  never claim done without running the test and pasting output. See `tasks/todo.md` (plan) and
  `tasks/lessons.md` (corrections log). The bar: would a staff engineer approve this?
- `docs/CODEX_HANDOFF.md` is the canonical deep-dive — read it for the full "war stories" list of
  gotchas this codebase already paid for (FlightAware Alerts need a paid tier; IATA→ICAO ident
  mapping via `carriers.py`; AeroDataBox 1 req/s rate limit and `"6E 1341"` spacing; Twilio trial
  WhatsApp only via sandbox sender; `StaticPool` for in-memory SQLite tests; etc.).

## Gotchas worth surfacing here

- **Demo `MOCK_PROVIDERS=True` mocks messaging too** — the demo writes `notifications` rows with
  `status="sent"` but no real WhatsApp/SMS goes out unless you also set `MOCK_MESSAGING=False`.
- **IATA vs ICAO idents:** customers type IATA (`6E1341`); FlightAware `/flights` resolves only ICAO
  (`IGO1341`). `carriers.py` maps the carrier code; always normalize with `normalize_flight_number`
  (AeroDataBox returns numbers spaced, e.g. `"6E 1341"`).
- **Future-date dedup trap:** the demo seeds a future flight date, so an event stamped with wall-clock
  `now` is *older* than the baseline and the brain silently drops it. `simulator.py` keeps `event_ts`
  strictly monotonic (`now = max(wall_now, last_known_event_ts + 1s)`).
- **`DATABASE_URL`** must be the direct Postgres DSN
  (`postgresql+psycopg://postgres:<pwd>@db.<ref>.supabase.co:5432/postgres`), not the Supabase REST
  URL; URL-encode `@` in the password as `%40`.
