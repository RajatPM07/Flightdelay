# Codex Handoff — TripSecure+ Flight Delay MVP

> Paste everything below into Codex as the opening prompt. It contains the mission, the
> architecture, the hard rules, the immediate next task, and — most valuably — the
> hard-won gotchas this codebase already paid for. Read it fully before writing code.

---

You are taking over an in-progress, **working** backend + demo UI. Do not rewrite it. Extend it, following the existing patterns and the rules below.

## 1. What this is
**TripSecure+ Flight Delay** — the monitoring + notification MVP for ICICI Lombard's flight-delay product (Product Code **4233**). A standalone service that watches each insured flight and notifies the customer when something **material** changes (delay crossing a threshold, cancellation, diversion, departure with new ETA, landing).

**It moves NO money.** Explicit non-goals (do NOT build): automated payouts, bank/IMPS/NEFT, the "is the delay long enough to pay" decision, fraud/anti-selection, travel verification. These are Phase 2; leave clean seams, build none of them. There is a `Decision.payout_recommendation` seam that is always `None` — never read or build behind it. No payout/claim/compensation language anywhere in customer-facing text.

The asset is **the brain**: an in-house, pure, I/O-free decision engine. The data vendor is a commodity — swap it and the brain behaves identically.

## 2. Stack & repo
- Python 3.11+ (the venv is 3.14), FastAPI, Uvicorn, httpx, pydantic-settings.
- SQLModel over **Supabase Postgres** (`create_all` for MVP; Alembic is a later step).
- APScheduler (in-process) for the STA+24h backstop and deregistration.
- Frontend demo: one self-contained `app/static/demo.html` (Tailwind Play CDN + vanilla JS + GSAP), served by FastAPI. No build step.
- Repo: `github.com/RajatPM07/Flightdelay`. **Working branch IS the trunk:** `claude/flightdelay-mvp-scaffold-EvfpU` (there is no `main`/`master`; `origin/HEAD` points here).
- Run tests with the venv: `.venv/bin/python -m pytest -q` (currently **112 passing**). Do NOT use bare `python`.

### Layout
```
app/domain/            PURE brain — states.py (state machine, BrainConfig, TERMINAL_STATES),
                       materiality.py (tier_for_delay), brain.py (FlightStatus, decide()).
                       NO vendor SDK, NO DB, NO httpx in here. Ever.
app/providers/flightdata/  base.py (FlightDataProvider ABC + subscription_scope + extract_subject),
                       flightaware.py, aerodatabox.py, carriers.py (IATA→ICAO + normalize_flight_number),
                       mock.py (offline provider).
app/providers/messaging/   base.py (MessageProvider, Channel), twilio.py, email.py.
app/services/          issuance.py, webhook.py (_process_for_policy + fan-out), notifier.py,
                       scheduler.py (backstop), subscriptions.py (reference counting),
                       simulator.py (demo events), audit.py.
app/api/               policies.py (POST /policies, GET /policies/{id}), webhooks.py
                       (/webhooks/flightaware, /webhooks/aerodatabox/{secret}), demo.py (GET /, POST /demo/simulate).
app/models.py          policies, policy_pii, flight_state, event_log, notifications, flight_subscriptions.
app/config.py          Settings (pydantic-settings); app/deps.py (DI + provider selection).
scripts/               check_env.py, e2e_smoke.py (FlightAware synthetic E2E),
                       e2e_aerodatabox.py (fan-out synthetic E2E).
docs/superpowers/      specs/ and plans/ — design + implementation plans.
```

## 3. The brain & state machine (do not modify lightly; it is the product)
States: `REGISTERED, ON_TIME, DELAYED_T1, DELAYED_T2, DELAYED_T3, DEPARTED, LANDED, CANCELLED, DIVERTED, CLOSED`. Thresholds are **config** (env/DB), never hardcoded: T1=30, T2=60, T3=120 min; recovery buffer=15; backstop=24h.

`decide(current_state, last_known_status, incoming_status, config) -> Decision(new_state, should_notify, event_type, message_context, payout_recommendation=None)` is pure and fully unit-tested. The canonical spec lives in `tests/test_brain.py`: a 7-event sequence → **exactly 3 notifications**, final state `LANDED`. If you ever change the brain, that test is the contract.

Rules baked in: dedup/idempotency (ignore events with `event_ts <= last stored` or duplicate content hash); delay = best arrival estimate (gate arrival / `actual_in`, NOT runway touchdown) − scheduled; tier hysteresis (escalate on crossing up, "back on schedule" only when delay drops below tier minus recovery buffer; never re-notify same-tier wobble); branch/terminal events for cancel/divert/depart/land. Notifiable set: tier escalations, recovery, cancelled, diverted, departed, landed. Everything else logs silently.

## 4. HARD RULES (these are non-negotiable; reviews enforce them)
- **PII isolation (DPDP):** ONLY `policy_pii` holds name/phone/email. Every other table and log references the anonymous `policy_id` only. The `GET /policies/{id}` read model masks contact server-side (`_mask_phone`/`_mask_name`, India +91 oriented) and never returns raw phone/email or `raw_payload`.
- **Brain purity:** `app/domain/` imports no vendor SDK, no DB, no httpx. Keep it that way.
- **No magic numbers:** thresholds/buffers/backstop come from config. Even the demo simulator derives its delay deltas from config (midpoints between tiers) so they never land in the wrong tier under non-default thresholds.
- **Idempotency:** on webhook processing (brain dedup) and notification send (notifier checks `EventLog.notification_id`).
- **Timezone discipline:** store STA with local offset, convert to UTC on ingest, do ALL scheduling/comparison in UTC. SQLite round-trips can drop tz — coerce naive→aware before comparing.
- **Secrets:** real values live ONLY in gitignored `.env`. `.env.example` is committed and must stay placeholders.
- **Workflow:** plan-first for non-trivial work; TDD (write the failing test first); commit per logical unit; never claim done without running the test and pasting output. The team runs spec + quality reviews on each task — they have caught a real bug essentially every round (see §6).

## 5. Two providers, one interface
`FlightDataProvider` is the seam. Selection in `app/deps.get_flight_provider()`: `MOCK_PROVIDERS=True` → mock; else `FLIGHT_PROVIDER=aerodatabox` → AeroDataBox; else FlightAware (default).

- **FlightAware AeroAPI** (`flightaware.py`): per-policy push Alerts, routed by `alert_id`. `subscription_scope = "per_policy"`.
- **AeroDataBox** (`aerodatabox.py`): subscribes per **flight number** (no date filter), so one subscription is shared across many policies. `subscription_scope = "per_flight_number"`. This forced three things, all built and tested:
  1. `flight_subscriptions` table + `app/services/subscriptions.py` reference counting — subscribe once per number, deregister only when the LAST active policy on that number closes (and the **scheduler backstop** respects this too — see §6).
  2. A **fan-out** route `POST /webhooks/aerodatabox/{secret}` → `process_aerodatabox_webhook` resolves the payload to ALL active policies for that number+date and runs `_process_for_policy` per policy.
  3. Secret-token-in-URL auth (AeroDataBox does not sign webhooks).

## 6. War stories — the gotchas this codebase already paid for (read these!)
1. **FlightAware Alerts need a paid tier.** The Personal/free key gets `401` on `POST /alerts` ("Alerts and Historical data are only available on Standard and Premium tiers"). `GET /flights` works on Personal. Issuance currently treats alert registration as mandatory — a "graceful degradation" path (create policy even if alert registration fails) was discussed but NOT built; decide before relying on it.
2. **FlightAware `/flights` only allows ~2 days into the future.** Querying a flight 8 days out returns HTTP 400. AeroDataBox is more forgiving for near-term lookups.
3. **IATA vs ICAO idents.** Customers type the IATA ident on their ticket (`6E1341`); FlightAware's `/flights` only resolves the **ICAO** ident (`IGO1341`) — IATA returns zero flights and a silently empty baseline. `carriers.py` maps the 2-char IATA carrier code → 3-char ICAO (a bounded ~1,400-carrier table; we ship a curated subset; Phase 2 = full dataset + cached AeroDataBox `/operators` fallback). AeroDataBox accepts IATA but returns the number **with a space** (`"6E 1341"`) — always normalize with `normalize_flight_number`.
4. **AeroDataBox free BASIC tier ≈ 1 request/second** → HTTP 429 on back-to-back calls. Space calls / add throttling for real use.
5. **AeroDataBox subscription id** is returned under the `id` key; no inbound signature mechanism exists. Times are nested (`arrival.scheduledTime.utc`, `arrival.revisedTime.utc` = estimated/actual, gate-based — good, that's what the brain wants). Datetime format is `"2026-06-05 20:10Z"` (space, optional seconds).
6. **Scheduler backstop must respect `subscription_scope`.** The STA+24h force-close originally called `deregister_alert` unconditionally — for AeroDataBox's shared subscription that would kill monitoring for every other policy on the flight. Fixed to branch (per_policy → deregister; per_flight_number → `release_subscription`). Any new teardown path must do the same.
7. **Demo runs `MOCK_PROVIDERS=True`, which mocks messaging too.** So the demo writes `notifications` rows with `status="sent"` but `MockMessageProvider` never calls Twilio — **no real WhatsApp/SMS is delivered in the demo.** (This is the OPEN TASK — see §8.)
8. **Future-date dedup trap (demo simulator).** The demo seeds a future flight date, so a synthetic event stamped with wall-clock `now` is *older* than the baseline and the brain's dedup guard silently drops it. `simulator.py` derives `now = max(wall_now, last_known_event_ts + 1s)` to stay strictly monotonic.
9. **Twilio account is a TRIAL.** Its one number (`+19132780553`) is SMS/voice-capable but **NOT WhatsApp-enabled** (`POST` with it as WhatsApp From → error `63007`). Real WhatsApp works only via the **sandbox** sender `whatsapp:+14155238886`, and only to numbers that have sent `join <code>` to it (`+917358467199` is joined — a real message reached it and showed status `read`). Trial SMS only reaches **verified** numbers. There was a double-`whatsapp:` prefix bug (env already had the scheme, code prepended again → `whatsapp:whatsapp:+…`) — fixed to be idempotent.
10. **Secrets hygiene.** Real credentials were once pasted into the tracked `.env.example`; they must live only in gitignored `.env`. The Supabase password contains `@` → URL-encode as `%40` in `DATABASE_URL`. `DATABASE_URL` must be the **direct Postgres DSN** (`postgresql+psycopg://postgres:<pwd>@db.<ref>.supabase.co:5432/postgres`), NOT the Supabase REST URL.
11. **In-memory SQLite tests** need `poolclass=StaticPool` so the seed and the request handler share one connection under FastAPI's threaded TestClient.
12. **DIVERTED is intentionally NOT terminal** (a diverted flight may still land elsewhere; the STA+24h backstop force-closes to `CLOSED`). Don't "fix" it without checking the domain owner.

## 7. How to run
```bash
# tests
.venv/bin/python -m pytest -q                       # 112 passing

# the demo (self-contained, no keys, NO real messages)
DEMO_MODE=True MOCK_PROVIDERS=True .venv/bin/python -m uvicorn app.main:app --port 8000
# open http://localhost:8000/  → issue a policy, click the simulate buttons.

# synthetic fan-out E2E (AeroDataBox), with the demo server up:
.venv/bin/python scripts/e2e_aerodatabox.py
```
Config flags (env/.env): `DEMO_MODE`, `MOCK_PROVIDERS`, `FLIGHT_PROVIDER` (flightaware|aerodatabox), `FLIGHTAWARE_*`, `AERODATABOX_API_KEY` (+ optional base/host/webhook secret), `TWILIO_*`, `SENDGRID_*`/`EMAIL_FROM`, `DELAY_T1/T2/T3_MIN`, `RECOVERY_BUFFER_MIN`, `BACKSTOP_HOURS`, `DATABASE_URL`.

## 8. Immediate next task (start here)
**Make the demo able to send REAL WhatsApp on each simulate event.** Today `MOCK_PROVIDERS` mocks both flight and messaging together (§6.7), so the demo never calls Twilio.

Decouple them: add a `mock_messaging: bool` setting (default mirrors `mock_providers` so existing behaviour is unchanged) and branch on it in `app/deps.get_msg_provider()` instead of `mock_providers`. Then the demo can run flight-mocked but messaging-real:
```bash
DEMO_MODE=True MOCK_PROVIDERS=True MOCK_MESSAGING=False .venv/bin/python -m uvicorn app.main:app --port 8000
```
Real delivery still requires (§6.9): the form's phone must be a **sandbox-joined** number (`+917358467199`), `TWILIO_WHATSAPP_FROM=whatsapp:+14155238886`, within the 24h sandbox window. Add a test that `get_msg_provider()` returns the real composite when `mock_messaging=False` and the mock when `True`. TDD it.

**After that:** frontend visual polish (the GSAP page is functional and tested but its look/animation needs a human eyeball — the MotionPath particle arc on narrow/stacked viewports is the known rough edge), and optionally a code review of `app/static/demo.html` JS (polling-interval cleanup, fetch error handling, consent enforcement).

## 9. How to work here
Follow the existing patterns. Plan non-trivial work before coding, write the failing test first, keep the brain pure, keep PII isolated, derive constants from config, and verify by running — paste the pytest output. When you finish a unit, commit with a clear message. The bar is: would a staff engineer approve this?
