# Spec — Live multi-vendor flight-status lookup (page 2)

**Date:** 2026-06-05
**Status:** Approved (design), implementing
**Scope:** A second demo page that, given a flight number, returns the *real* current
status from a chosen vendor — proving the multi-vendor abstraction and surfacing the
edge cases the code already handles. **Read-only: no policy, monitoring, subscription,
audit, or messaging side-effects.**

> Frontend is intentionally **minimal/functional** for now — both pages will be visually
> redesigned afterward. Invest effort in the backend + edge-case correctness, not polish.

## Why this is cheap
`FlightDataProvider.get_baseline()` is already a one-shot real lookup
(`GET /flights/...`) that works on the **free** vendor tiers and does **not** touch the
paid Alerts/subscription path. `carriers.py` already does IATA→ICAO + flight-number
normalization. So page 2 is a thin read-only layer over existing code.

## Architecture

### Backend
1. **`get_lookup_provider(vendor: str) -> FlightDataProvider`** (in `app/deps.py`)
   - Builds a **real** `FlightAwareProvider` or `AeroDataBoxProvider` from `settings`,
     **ignoring `MOCK_PROVIDERS`** (page 1 stays mock-driven; page 2 hits real feeds).
   - `vendor` ∈ {`flightaware`, `aerodatabox`}; unknown → `ValueError`.
   - Not a cached singleton (distinct from `get_flight_provider`).

2. **`GET /live/lookup`** and **`GET /live`** (new `app/api/live.py`; gated by `DEMO_MODE`)
   - Query params: `flight` (required), `date` (required, `YYYY-MM-DD`),
     `vendor` (default `flightaware`).
   - Calls `provider.get_baseline(flight, date)` → `FlightStatus`.
   - Derives delay minutes + brain state by reusing `materiality.tier_for_delay` and
     `issuance._state_from_baseline` (or an equivalent small pure helper — do not duplicate
     tier logic).
   - **Response (200):**
     ```json
     {
       "vendor": "flightaware",
       "requested_ident": "6E1341",
       "resolved_ident": "IGO1341",
       "flight_date": "2026-06-10",
       "status": {
         "scheduled_in_utc": "...", "estimated_in_utc": "...", "actual_in_utc": null,
         "departed": false, "cancelled": false, "diverted": false
       },
       "delay_minutes": 90,
       "state": "DELAYED_T2",
       "notes": "Delay computed from best gate-arrival estimate (not runway touchdown)."
     }
     ```
   - `resolved_ident` shows the transformation: FlightAware → ICAO via `carriers.py`;
     AeroDataBox → `normalize_flight_number` form. Computed in the endpoint for display
     (provider internals unchanged).

### Edge cases → clean responses (all four surfaced)
| Edge case | Behavior |
|---|---|
| IATA→ICAO / spacing | `requested_ident` vs `resolved_ident` in the response + shown on the page |
| Not found / bad input | `get_baseline` raises `ValueError` → **404** `{ "error": "No flight found for <ident> on <date>" }` |
| Future-date limit (FA HTTP 400) | caught → **400** `{ "error": "FlightAware only resolves flights ~2 days out. Try a nearer date." }` |
| Rate limit (ADB HTTP 429) | caught → **429** `{ "error": "AeroDataBox is rate-limited (free tier ~1 req/s). Wait a moment and retry." }` |
| Gate vs runway / reconciliation | `notes` field + an explainer card on the page |

Other upstream errors → **502** `{ "error": "Upstream flight provider error." }`.

### Frontend — `app/static/live.html`, served at `GET /live` (gated by `DEMO_MODE`)
Minimal, functional, same Tailwind CDN; **no GSAP / no animation / no heavy styling.**
- **Lookup card:** `#live-form` with `#live-flight`, `#live-date` (default today),
  `#live-vendor` toggle (FlightAware/AeroDataBox), submit button.
- **Result card:** `#live-result` — status badge, `requested → resolved` ident line,
  scheduled→estimated + delay, `Source: <vendor>`, `notes`, and a trust line:
  *"Read-only status lookup — no monitoring or messages are triggered here."*
- **Edge-cases showcase:** `#live-edgecases` — four cards, each with a one-line
  explanation and a **"try it"** button that triggers the real behavior
  (far-future date → 400; bogus flight → not-found; IATA ident → ICAO mapping;
  gate-vs-runway note).
- Link back to page 1; add a small link on `demo.html` → `/live`.

## Testing
- **Backend** (`tests/test_live_lookup.py`): TestClient with a **fake provider injected**
  via dependency override / monkeypatch (no network). Assert: happy path mapping +
  delay/state derivation; not-found→404; 400 and 429 mapped to friendly errors;
  `/live/lookup` 404s when `DEMO_MODE=False`.
- **Frontend** (`tests/test_live_page.py`): `/live` returns HTML with key element IDs
  (`live-form`, `live-result`, `live-edgecases`, vendor toggle) and 404s when
  `DEMO_MODE=False`. Mirrors `test_demo_page.py`.

## Deploy note (not code)
Set `FLIGHTAWARE_API_KEY` and `AERODATABOX_API_KEY` in the Render env for `/live` to
return real data. Page 1 remains fully mock and needs no keys.

## Out of scope
Monitoring, webhooks, subscriptions, persistence, messaging, "both vendors at once"
side-by-side view (toggle only), and any visual polish (deferred to the redesign).
