# TripSecure+ Flight Delay — Live Pipeline Demo UI (Design Spec)

**Date:** 2026-06-04
**Status:** Approved (requirements authored by product owner)
**Scope:** A single-page, self-contained demo UI that issues a monitoring policy and lets the user drive synthetic flight events to watch the real brain/state-machine/notifier react live. Monitoring-only — moves no money (consistent with Product 4233 brief).

---

## 1. Goal & shape

One polished page served by the existing FastAPI app. It runs in **demo mode** (`DEMO_MODE=True`) and **mock-provider mode** (`MOCK_PROVIDERS=True`) so it is fully self-contained: no external API keys, no real WhatsApp/SMS sent, yet it exercises the **real** issuance → brain `decide()` → persistence → notifier pipeline. Notifications are written to the DB by the mock sender and surfaced in the UI.

```
[Left: mobile customer app]  --POST /policies-->  policy_id (baseline ON_TIME)
        │                                              │
        │  data-flow animation across the screen       ▼
[Right: telemetry dashboard reveals] --POST /demo/simulate--> real brain pipeline
        └─ polls GET /policies/{id} every ~2s --> stepper + timeline + notifications update
```

## 2. Visual brand (hard requirement)

- **Theme:** premium dark dashboard. Background `#0B0F19` (slate-navy), cards `#111625`.
- **Colors:** `primary` `#B02A30` (Autumn Crimson, corporate), `secondary` `#F99D27` (Sea Buckthorn Orange, warnings/active), `tertiary` `#005B75` (Ocean Blue, structure/progress). Emerald (`#10B981`-ish) for the healthy/on-time state.
- **Fonts (Google):** headings `Space Grotesk`; body `Inter`.
- Implemented with Tailwind (Play CDN + inline `tailwind.config` for the custom palette/fonts) so there is **no build step**. Vanilla JS only.

## 3. Layout — split screen

**Left panel — Customer app (mobile frame):** styled as a premium phone frame showing the ICICI Lombard portal. Contains the issuance form: PNR, Flight Number, Date, Passenger Name, Phone, and a DPDP **consent checkbox** (affirmative, not pre-ticked). A large Autumn-Crimson CTA: **"Purchase TripSecure+"**.

**Right panel — Backend telemetry & operations:** starts **dimmed + blurred** under an overlay "*Awaiting Active Policy…*". Activates after issuance.

## 4. Transition / data-flow animation

On "Purchase TripSecure+":
1. **Minting:** button shows a spinner, then a checkmark "*Policy Minted!*".
2. **Visual bridge:** a glowing CSS particle/line animation shoots from the phone frame across the screen into the dashboard.
3. **Reveal:** the right panel un-blurs and fades in with a pulsing activation animation.

## 5. Right-panel telemetry (active state)

- **State-machine stepper:** horizontal progress line `Registered → On-Time → Delayed → Departed → Landed`, current node glows/pulses. `Cancelled`/`Diverted` render as terminal branch chips. Color by state:
  - `ON_TIME` → emerald · `DELAYED_T1/T2` → Sea Buckthorn Orange · `DELAYED_T3`/`CANCELLED` → Autumn Crimson · `DEPARTED`/`LANDED` → Ocean Blue.
  - The single "Delayed" node reflects tier intensity (T1/T2 orange, T3 crimson).
- **Simulation control center:** `[Delay +90m]` `[Depart]` `[Land]` `[Recover]` `[Cancel]` `[Divert]` → each calls `POST /demo/simulate`.
- **Raw webhook payload console:** collapsible panel showing the raw synthetic provider JSON payload that the last simulate action sent through the pipeline.
- **Audit & notification cards:** event timeline (newest first, notified events flagged) + notification cards (WhatsApp/SMS) with **PII masked** (e.g. `+91 98••••99`).

## 6. Backend additions

### 6.1 `GET /policies/{policy_id}` — status read model
Returns JSON for the dashboard. Reads `flight_state` (current state, last update), `policies` (flight no./date, scheduled arrival), `event_log` (timeline), `notifications`, and `policy_pii` for a **masked** name/phone (display only — raw PII never leaves the masking layer).
```jsonc
{
  "policy_id": "…", "flight_number": "6E1341", "flight_date": "2026-06-12",
  "current_state": "DELAYED_T2", "scheduled_in_utc": "…", "last_update_ts": "…",
  "contact": { "name": "Rajat S.", "phone_masked": "+91 98••••99" },
  "timeline": [ { "ts": "…", "prev_state": "ON_TIME", "new_state": "DELAYED_T2",
                  "event_type": "DELAY_TIER_2", "notified": true } ],
  "notifications": [ { "channel": "whatsapp", "body": "…", "status": "sent", "sent_ts": "…" } ]
}
```
404 if the policy doesn't exist.

### 6.2 `POST /demo/simulate` — drive a synthetic event (DEMO_MODE only)
Body `{ "policy_id": "…", "event": "delay_t1|delay_t2|delay_t3|recover|depart|land|cancel|divert" }`.
Builds a **synthetic provider payload** (AeroDataBox-style flight object, from the captured fixture shape), runs it through `provider.normalise()` then the **same** `_process_for_policy()` the webhooks use — so the demo exercises real logic, not a parallel path. Returns the resulting state plus the raw payload (for the console).

Event → arrival delta (derived from configured thresholds — **no magic numbers**, read T1/T2/T3 + recovery buffer from settings):
| event | effect |
|---|---|
| `delay_t1` | est = scheduled + (T1 + a bit), below T2 |
| `delay_t2` | est = scheduled + (T2 + a bit), below T3 |
| `delay_t3` | est = scheduled + (T3 + a bit) |
| `recover` | est = scheduled + (below T1 − recovery buffer) → back ON_TIME |
| `depart` | departed flag set, est carried from last delay |
| `land` | actual_in set (status Arrived) |
| `cancel` | cancelled flag |
| `divert` | diverted flag |
Each synthetic event uses a fresh `event_ts` (now) and a distinct content hash so the brain's dedup guard doesn't drop it.

### 6.3 The page route
`GET /` (or `/demo`) returns the HTML page. Both the page and `/demo/simulate` are **guarded by `DEMO_MODE`** (default `False`) — production deploys expose neither. Returns 404 when `DEMO_MODE` is off.

## 7. Guardrails
- No payout/claim/compensation language anywhere (monitoring-only).
- `DEMO_MODE` default off; `MOCK_PROVIDERS` makes the demo self-contained.
- PII masking happens server-side in the read model; the brain and tracking tables remain PII-free.
- Brain (`app/domain/`) is **not** modified.

## 8. File structure
| File | Responsibility |
|---|---|
| `app/config.py` | add `demo_mode: bool = False` |
| `app/api/policies.py` | add `GET /policies/{policy_id}` read model + masking helper |
| `app/api/demo.py` (new) | `GET /` page + `POST /demo/simulate`, both `DEMO_MODE`-gated |
| `app/services/simulator.py` (new) | build synthetic payloads per event; reuse `_process_for_policy` |
| `app/static/demo.html` (new) | the single polished page (Tailwind CDN + vanilla JS) |
| `app/main.py` | mount static + include demo router |
| `tests/test_policy_read.py` (new) | read-model shape, masking, 404 |
| `tests/test_demo_simulate.py` (new) | each event → correct transition + audit row; `DEMO_MODE=False` → 404 |

## 9. Testing
- pytest for both endpoints (read shape + masking + 404; each simulate event's transition + audit; demo-mode gating).
- Manual E2E: `DEMO_MODE=True MOCK_PROVIDERS=True .venv/bin/python -m uvicorn app.main:app --port 8000`, open the page, issue → simulate → watch the stepper/timeline/notifications update live.

## 10. Out of scope (YAGNI)
Real provider issuance in the demo, multi-policy fan-out view, cost dashboard, auth/login, persistence cleanup UI. The simulator is the demo engine; real-flight mode is already covered by the existing scripts.
