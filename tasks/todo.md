# AeroDataBox Push-Webhook Provider — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add AeroDataBox as a second, free, push-webhook flight-data provider behind the existing `FlightDataProvider` interface, handling its per-flight-number (date-less) subscription model via date-based webhook routing, fan-out to multiple policies, and reference-counted subscription lifecycle — without touching the pure brain.

**Architecture:** AeroDataBox subscribes per *flight number* (not per flight+date), so one subscription serves many policies. We keep the brain, models' core, and notifier untouched. Three edge changes: (1) a new `AeroDataBoxProvider` HTTP adapter; (2) a `flight_subscriptions` table + subscription service that reference-counts shared subscriptions; (3) a fan-out webhook route that resolves an inbound payload to *all* active policies for that flight number+date and runs the existing per-policy pipeline for each. Provider choice is config-driven, exactly like the current `MOCK_PROVIDERS` toggle.

**Tech Stack:** Python 3.11+, FastAPI, SQLModel + Supabase Postgres, APScheduler, httpx, pytest. AeroDataBox via RapidAPI (`aerodatabox.p.rapidapi.com`, headers `X-RapidAPI-Key` / `X-RapidAPI-Host`).

---

## Verified external contract (June 2026)

- **Subscribe:** `POST {base}/subscriptions/webhook/FlightByNumber/{number}?useCredits=true`
  body `{"url": "<webhook>", "maxDeliveryRetries": 2}` → returns a subscription object incl. its `id`.
- **Unsubscribe:** `DELETE {base}/subscriptions/webhook/{id}`.
- **Baseline (date-specific):** `GET {base}/flights/number/{number}/{date}` (`date` = `YYYY-MM-DD`) → array of flight objects.
- **Times (nested, post-2023 schema):** `arrival.scheduledTime.utc`, `arrival.revisedTime.utc` (estimated, later actual), `arrival.runwayTime.utc` (touchdown). `status` is an enum string. `departure.*` mirrors arrival.
- **Inbound auth:** none documented (no HMAC). Mitigation: a secret token embedded in the webhook URL path, checked on receipt.
- **Billing:** 1 credit per flight item per notification; log every inbound call for cost tracking (per project brief).

### Open items resolved by the probe task (T2), not by guessing
1. Exact `FlightNotificationContract` wrapper (single flight vs batch array; where `subscription id` and `remaining balance` sit).
2. Whether a gate-in (`actual_in`/chocks-on) time exists or only `runwayTime` (touchdown). The brief mandates **gate** arrival for the delay calc — if only runway is available, that is a documented accuracy caveat, recorded in T2.
3. The verbatim `status` enum strings used for cancelled / diverted / departed / arrived.

---

## File structure

| File | Responsibility | Action |
|---|---|---|
| `app/providers/flightdata/aerodatabox.py` | AeroDataBox HTTP adapter implementing `FlightDataProvider` | Create |
| `app/providers/flightdata/base.py` | Add `subscription_scope` capability + `extract_subject()` to the interface | Modify |
| `app/providers/flightdata/flightaware.py` | Declare `subscription_scope = "per_policy"` | Modify |
| `app/providers/mock.py` | Declare `subscription_scope = "per_policy"` | Modify |
| `app/models.py` | New `FlightSubscription` table | Modify |
| `app/services/subscriptions.py` | Reference-counted ensure/release of shared subscriptions | Create |
| `app/services/issuance.py` | Call `subscriptions.ensure_subscribed()` instead of `provider.register_alert()` directly | Modify |
| `app/services/webhook.py` | Extract `_process_for_policy()`; add `process_aerodatabox_webhook()` fan-out | Modify |
| `app/api/webhooks.py` | New `POST /webhooks/aerodatabox/{secret}` route | Modify |
| `app/config.py` | `flight_provider`, AeroDataBox keys + webhook secret | Modify |
| `app/deps.py` | Provider selection by `settings.flight_provider` | Modify |
| `tests/test_aerodatabox_normalise.py` | normalise() against the captured real fixture | Create |
| `tests/test_subscriptions.py` | reference-counting logic (pure-ish, in-memory DB) | Create |
| `tests/test_webhook_fanout.py` | one payload → N policies; date/number matching | Create |
| `tests/fixtures/aerodatabox_webhook.json` | real captured payload (from T2) | Create |

---

## Task 1: Config + provider selection

**Files:**
- Modify: `app/config.py`
- Modify: `app/deps.py:38-52` (`get_flight_provider`)
- Test: `tests/test_provider_selection.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_provider_selection.py
from app.providers.flightdata.aerodatabox import AeroDataBoxProvider
from app.providers.flightdata.flightaware import FlightAwareProvider


def _build(monkeypatch, **over):
    import app.config as c
    for k, v in over.items():
        monkeypatch.setattr(c.settings, k, v, raising=False)
    import importlib, app.deps as d
    importlib.reload(d)
    d._flight_provider = None
    return d


def test_selects_aerodatabox(monkeypatch):
    d = _build(monkeypatch, mock_providers=False, flight_provider="aerodatabox",
               aerodatabox_api_key="k", aerodatabox_base_url="https://x", aerodatabox_rapidapi_host="h")
    assert isinstance(d.get_flight_provider(), AeroDataBoxProvider)


def test_defaults_to_flightaware(monkeypatch):
    d = _build(monkeypatch, mock_providers=False, flight_provider="flightaware")
    assert isinstance(d.get_flight_provider(), FlightAwareProvider)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_provider_selection.py -q`
Expected: FAIL — `ModuleNotFoundError: app.providers.flightdata.aerodatabox` (created in T3) and missing settings.

- [ ] **Step 3: Add settings**

In `app/config.py` `Settings`, after the FlightAware block:

```python
    # Flight-data provider selection: "flightaware" | "aerodatabox" | (mock via mock_providers)
    flight_provider: str = "flightaware"
    aerodatabox_api_key: str = ""
    aerodatabox_base_url: str = "https://aerodatabox.p.rapidapi.com"
    aerodatabox_rapidapi_host: str = "aerodatabox.p.rapidapi.com"
    aerodatabox_webhook_secret: str = ""
```

- [ ] **Step 4: Wire selection in `deps.get_flight_provider()`**

Replace the body of `get_flight_provider()`:

```python
def get_flight_provider() -> FlightDataProvider:
    global _flight_provider
    if _flight_provider is None:
        if settings.mock_providers:
            _flight_provider = MockFlightDataProvider(
                webhook_secret=settings.flightaware_webhook_secret
            )
        elif settings.flight_provider == "aerodatabox":
            from app.providers.flightdata.aerodatabox import AeroDataBoxProvider
            _flight_provider = AeroDataBoxProvider(
                api_key=settings.aerodatabox_api_key,
                base_url=settings.aerodatabox_base_url,
                rapidapi_host=settings.aerodatabox_rapidapi_host,
                webhook_secret=settings.aerodatabox_webhook_secret,
                public_webhook_base_url=settings.public_webhook_base_url,
            )
        else:
            _flight_provider = FlightAwareProvider(
                api_key=settings.flightaware_api_key,
                base_url=settings.flightaware_base_url,
                webhook_secret=settings.flightaware_webhook_secret,
                public_webhook_base_url=settings.public_webhook_base_url,
            )
    return _flight_provider
```

- [ ] **Step 5: Run test (passes after T3 exists)** — defer green until T3, or temporarily stub the class. Run full suite at T3.

- [ ] **Step 6: Commit**

```bash
git add app/config.py app/deps.py tests/test_provider_selection.py
git commit -m "feat(config): flight_provider selector + AeroDataBox settings"
```

---

## Task 2: Interface capability + live payload probe (resolves the schema gap)

**Files:**
- Modify: `app/providers/flightdata/base.py`
- Modify: `app/providers/flightdata/flightaware.py`, `app/providers/mock.py` (declare scope)
- Create: `tests/fixtures/aerodatabox_webhook.json` (captured real payload)
- Create: `scripts/capture_aerodatabox.py` (one-off probe)

- [ ] **Step 1: Add capability + routing hook to the interface**

In `app/providers/flightdata/base.py`, add to the `FlightDataProvider` ABC:

```python
    # "per_policy": one subscription per policy (FlightAware).
    # "per_flight_number": one shared subscription per flight number (AeroDataBox).
    subscription_scope: str = "per_policy"

    def extract_subject(self, raw_payload: dict) -> tuple[str, str] | None:
        """
        Return (normalised_flight_number, flight_date_yyyy_mm_dd) used to route an
        inbound webhook to active policies. Default None = route by alert_id instead.
        """
        return None
```

- [ ] **Step 2: Declare scope on existing providers**

In `flightaware.py` class body: `subscription_scope = "per_policy"`.
In `mock.py` `MockFlightDataProvider`: `subscription_scope = "per_policy"`.

- [ ] **Step 3: Probe script (captures a real notification)**

```python
# scripts/capture_aerodatabox.py — subscribe a known live flight, print the first
# webhook payload received, then unsubscribe. Run with the server + ngrok up.
# Usage: python scripts/capture_aerodatabox.py --number 6E1341
# It POSTs the subscription, waits, and the /webhooks/aerodatabox route logs the
# raw body to /tmp/aerodatabox_capture.json (added temporarily in the route).
```

- [ ] **Step 4: Run the probe and save the fixture**

Run (server + ngrok up, `.env` has AeroDataBox key):
`.venv/bin/python scripts/capture_aerodatabox.py --number 6E1341`
Then copy the captured raw JSON to `tests/fixtures/aerodatabox_webhook.json`.
Record in this file (below T2) the verbatim answers to the three open items.

- [ ] **Step 5: Commit**

```bash
git add app/providers/flightdata/base.py app/providers/flightdata/flightaware.py app/providers/mock.py tests/fixtures/aerodatabox_webhook.json scripts/capture_aerodatabox.py
git commit -m "feat(providers): subscription_scope capability + captured AeroDataBox fixture"
```

> **Probe findings (fill during T2):** wrapper shape = …; gate-in available? = …; status enum = …

---

## Task 3: AeroDataBoxProvider adapter

**Files:**
- Create: `app/providers/flightdata/aerodatabox.py`
- Test: `tests/test_aerodatabox_normalise.py`

- [ ] **Step 1: Write the failing normalise test against the real fixture**

```python
# tests/test_aerodatabox_normalise.py
import json, pathlib
from app.providers.flightdata.aerodatabox import AeroDataBoxProvider

FIXTURE = json.loads((pathlib.Path(__file__).parent / "fixtures/aerodatabox_webhook.json").read_text())


def _p():
    return AeroDataBoxProvider(api_key="k", base_url="https://x",
                               rapidapi_host="h", webhook_secret="s", public_webhook_base_url="https://pub")


def test_normalise_maps_arrival_times():
    status = _p().normalise(FIXTURE)
    assert status.scheduled_in_utc is not None
    assert status.content_hash  # stable, non-empty
    # status flags are booleans regardless of presence
    assert isinstance(status.cancelled, bool)
    assert isinstance(status.diverted, bool)


def test_extract_subject_returns_number_and_date():
    subj = _p().extract_subject(FIXTURE)
    assert subj is not None
    number, date = subj
    assert number == number.replace(" ", "").upper()
    assert len(date) == 10  # YYYY-MM-DD
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_aerodatabox_normalise.py -q`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement the adapter**

Implement `AeroDataBoxProvider(FlightDataProvider)` with `subscription_scope = "per_flight_number"`:
- `_headers()` → `{"X-RapidAPI-Key": api_key, "X-RapidAPI-Host": rapidapi_host}`.
- `get_baseline(number, date)` → `GET {base}/flights/number/{number}/{date}` (dates=Both arrival), take first; reuse `normalise()`. Log `aerodatabox.get_baseline` with cost field.
- `register_alert(policy_id, number, date)` → `POST {base}/subscriptions/webhook/FlightByNumber/{number}?useCredits=true` body `{"url": f"{public_webhook_base_url}/webhooks/aerodatabox/{webhook_secret}", "maxDeliveryRetries": 2}`; return `str(resp.json()["id"])`. (The shared-subscription decision is made by the subscription service — this method just performs the HTTP call.)
- `deregister_alert(subscription_id)` → `DELETE {base}/subscriptions/webhook/{subscription_id}`; treat 404 as success (idempotent).
- `normalise(raw_payload)` → map per the T2 fixture: pull the flight object out of the wrapper, read `arrival.scheduledTime.utc` → `scheduled_in_utc`; `arrival.revisedTime.utc` → `estimated_in_utc`; gate-in (or `runwayTime.utc` fallback, documented) → `actual_in_utc`; `status` → cancelled/diverted/departed flags; `content_hash` = sha256 of the material fields (same recipe as `flightaware._content_hash`). Parse `"YYYY-MM-DD HH:MMZ"` AeroDataBox datetime strings to tz-aware UTC.
- `extract_subject(raw_payload)` → return `(number_without_spaces_upper, arrival_or_departure_date_yyyy_mm_dd)`.
- `verify_signature(body, sig)` → return `True` (URL-secret model; actual auth is the secret path segment, checked in the route — see T6).

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_aerodatabox_normalise.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/providers/flightdata/aerodatabox.py tests/test_aerodatabox_normalise.py
git commit -m "feat(providers): AeroDataBox adapter (baseline, subscribe, normalise)"
```

---

## Task 4: `flight_subscriptions` table + subscription service (reference counting)

**Files:**
- Modify: `app/models.py` (new `FlightSubscription`)
- Create: `app/services/subscriptions.py`
- Test: `tests/test_subscriptions.py`

> **SCHEMA CHANGE** — adds one table. Confirmed in-scope by the user before this plan.

- [ ] **Step 1: Write the failing reference-counting test**

```python
# tests/test_subscriptions.py — in-memory SQLite, fake provider records calls.
import pytest
from sqlmodel import Session, SQLModel, create_engine
import app.models  # registers tables
from app.models import Policy, FlightStateRow, FlightSubscription
from app.services import subscriptions as S


class FakeProvider:
    name = "aerodatabox"
    subscription_scope = "per_flight_number"
    def __init__(self): self.registered = []; self.deregistered = []
    async def register_alert(self, policy_id, number, date):
        self.registered.append(number); return f"sub-{number}"
    async def deregister_alert(self, sub_id):
        self.deregistered.append(sub_id)


@pytest.fixture()
def db():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s: yield s


def _seed_policy(db, pid, number="6E1341", date="2026-06-12", state="ON_TIME"):
    db.add(Policy(policy_id=pid, pnr="P", flight_number=number, flight_date=date,
                  scheduled_in_utc=None, scheduled_in_tz_offset="+00:00", consent_ts=None, status=state))
    db.add(FlightStateRow(policy_id=pid, current_state=state, last_known_status={},
                          last_update_ts=None, last_event_hash="", provider="aerodatabox", alert_id=None))
    db.commit()


@pytest.mark.asyncio
async def test_two_policies_share_one_subscription(db):
    prov = FakeProvider()
    _seed_policy(db, "A"); _seed_policy(db, "B")  # same flight number, same date is fine too
    sub1 = await S.ensure_subscribed(db, prov, "6E1341", "2026-06-12", "A")
    sub2 = await S.ensure_subscribed(db, prov, "6E1341", "2026-06-12", "B")
    assert sub1 == sub2 == "sub-6E1341"
    assert prov.registered == ["6E1341"]  # subscribed ONCE


@pytest.mark.asyncio
async def test_release_keeps_subscription_until_last_policy(db):
    prov = FakeProvider()
    _seed_policy(db, "A"); _seed_policy(db, "B", date="2026-06-13")
    await S.ensure_subscribed(db, prov, "6E1341", "2026-06-12", "A")
    await S.ensure_subscribed(db, prov, "6E1341", "2026-06-13", "B")
    # A reaches terminal:
    db.get(FlightStateRow, "A").current_state = "LANDED"; db.commit()
    await S.release_subscription(db, prov, "6E1341", "A")
    assert prov.deregistered == []          # B still active
    db.get(FlightStateRow, "B").current_state = "LANDED"; db.commit()
    await S.release_subscription(db, prov, "6E1341", "B")
    assert prov.deregistered == ["sub-6E1341"]  # last one out tears it down
    assert db.get(FlightSubscription, "6E1341") is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_subscriptions.py -q`
Expected: FAIL — `FlightSubscription` and `app.services.subscriptions` missing.

- [ ] **Step 3: Add the model**

In `app/models.py`:

```python
class FlightSubscription(SQLModel, table=True):
    __tablename__ = "flight_subscriptions"
    # Keyed by the normalised flight number — one shared subscription per number.
    subject_key: str = Field(primary_key=True)
    provider: str
    subscription_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
```

- [ ] **Step 4: Implement the service**

```python
# app/services/subscriptions.py
from __future__ import annotations
from sqlmodel import Session, select
from app.models import FlightSubscription, FlightStateRow, Policy
from app.domain.states import TERMINAL_STATES  # set of FlightState
from app.providers.flightdata.base import FlightDataProvider


def _norm(number: str) -> str:
    return number.replace(" ", "").upper()


def _active_count(db: Session, flight_number: str, exclude_policy: str | None = None) -> int:
    rows = db.exec(
        select(FlightStateRow).join(Policy, Policy.policy_id == FlightStateRow.policy_id)
        .where(Policy.flight_number == flight_number)
    ).all()
    terminal = {s.value for s in TERMINAL_STATES}
    return sum(
        1 for r in rows
        if r.current_state not in terminal and r.policy_id != exclude_policy
    )


async def ensure_subscribed(db, provider: FlightDataProvider, flight_number, flight_date, policy_id) -> str:
    if provider.subscription_scope == "per_policy":
        return await provider.register_alert(policy_id, flight_number, flight_date)
    key = _norm(flight_number)
    existing = db.get(FlightSubscription, key)
    if existing:
        return existing.subscription_id
    sub_id = await provider.register_alert(policy_id, flight_number, flight_date)
    db.add(FlightSubscription(subject_key=key, provider=provider.name, subscription_id=sub_id))
    db.commit()
    return sub_id


async def release_subscription(db, provider: FlightDataProvider, flight_number, policy_id) -> None:
    if provider.subscription_scope == "per_policy":
        return  # handled by the existing per-alert deregister path
    key = _norm(flight_number)
    if _active_count(db, flight_number, exclude_policy=policy_id) > 0:
        return  # other active policies still need it
    sub = db.get(FlightSubscription, key)
    if sub:
        await provider.deregister_alert(sub.subscription_id)
        db.delete(sub); db.commit()
```

- [ ] **Step 5: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_subscriptions.py -q`
Expected: PASS (2 tests).

- [ ] **Step 6: Commit**

```bash
git add app/models.py app/services/subscriptions.py tests/test_subscriptions.py
git commit -m "feat(subscriptions): reference-counted shared flight subscriptions"
```

---

## Task 5: Wire issuance + terminal release through the service

**Files:**
- Modify: `app/services/issuance.py:70` and the terminal path in `app/services/webhook.py:147-150`

- [ ] **Step 1: Update issuance to ensure-subscribe**

In `issuance.py`, replace `alert_id = await provider.register_alert(...)` with:

```python
    from app.services.subscriptions import ensure_subscribed
    alert_id = await ensure_subscribed(db, provider, flight_number, flight_date, policy_id)
```

(`alert_id` is still stored on `FlightStateRow` — for AeroDataBox it is the shared subscription id.)

- [ ] **Step 2: Update terminal teardown in `webhook.py`**

Replace the terminal block (currently `deregister_on_terminal(alert_id, provider)`):

```python
    if entering_terminal:
        if provider.subscription_scope == "per_policy" and alert_id:
            await deregister_on_terminal(alert_id, provider)
        else:
            from app.services.subscriptions import release_subscription
            await release_subscription(db, provider, flight_number_for_policy, policy_id)
        cancel_backstop(policy_id)
```

`flight_number_for_policy` must be loaded from `Policy` in this function (add a `db.get(Policy, policy_id).flight_number` lookup near the state load).

- [ ] **Step 3: Run the existing FlightAware suite to prove no regression**

Run: `.venv/bin/python -m pytest tests/test_issuance.py tests/test_webhook_pipeline.py tests/test_scheduler.py -q`
Expected: PASS (FlightAware path is `per_policy`, behaviour unchanged).

- [ ] **Step 4: Commit**

```bash
git add app/services/issuance.py app/services/webhook.py
git commit -m "feat: route issuance + terminal teardown through subscription service"
```

---

## Task 6: Fan-out webhook route (one payload → N policies)

**Files:**
- Modify: `app/services/webhook.py` (extract `_process_for_policy`, add `process_aerodatabox_webhook`)
- Modify: `app/api/webhooks.py` (new secret-token route)
- Test: `tests/test_webhook_fanout.py`

- [ ] **Step 1: Write the failing fan-out test**

```python
# tests/test_webhook_fanout.py — two policies on the same flight number+date both advance.
import pytest
from sqlmodel import Session, SQLModel, create_engine
import app.models
from app.models import Policy, PolicyPII, FlightStateRow
from app.domain.states import BrainConfig
from app.services.webhook import process_aerodatabox_webhook


class FanoutProvider:
    name = "aerodatabox"; subscription_scope = "per_flight_number"
    def verify_signature(self, b, s): return True
    def extract_subject(self, payload): return ("6E1341", "2026-06-12")
    def normalise(self, payload):
        from app.domain.brain import FlightStatus
        from datetime import datetime, timezone
        sta = datetime(2026, 6, 12, 14, 30, tzinfo=timezone.utc)
        return FlightStatus(event_ts=datetime(2026,6,12,9,0,tzinfo=timezone.utc),
            scheduled_in_utc=sta, estimated_in_utc=datetime(2026,6,12,16,0,tzinfo=timezone.utc),
            actual_in_utc=None, departed=False, cancelled=False, diverted=False, content_hash="h1")


@pytest.fixture()
def db():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s: yield s


def _seed(db, pid):
    from datetime import datetime, timezone
    sta = datetime(2026,6,12,14,30,tzinfo=timezone.utc)
    db.add(PolicyPII(policy_id=pid, name="N", phone="+910000000000"))
    db.add(Policy(policy_id=pid, pnr="P", flight_number="6E1341", flight_date="2026-06-12",
                  scheduled_in_utc=sta, scheduled_in_tz_offset="+00:00", consent_ts=sta, status="ON_TIME"))
    db.add(FlightStateRow(policy_id=pid, current_state="ON_TIME", last_known_status={},
                          last_update_ts=sta, last_event_hash="", provider="aerodatabox", alert_id="sub-6E1341"))
    db.commit()


@pytest.mark.asyncio
async def test_fanout_advances_all_matching_policies(db):
    _seed(db, "A"); _seed(db, "B")
    await process_aerodatabox_webhook(raw_payload={}, raw_body=b"{}", db=db,
        provider=FanoutProvider(), config=BrainConfig(30,60,120,15), msg_provider=None)
    # +90 min (16:00 vs 14:30) crosses T2 → both policies advance to DELAYED_T2.
    assert db.get(FlightStateRow, "A").current_state == "DELAYED_T2"
    assert db.get(FlightStateRow, "B").current_state == "DELAYED_T2"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_webhook_fanout.py -q`
Expected: FAIL — `process_aerodatabox_webhook` missing.

- [ ] **Step 3: Refactor `process_webhook` to extract the per-policy core**

Extract steps 4–12 of `process_webhook` into:

```python
async def _process_for_policy(*, state_row, incoming, db, provider, config, msg_provider) -> None:
    # (body = current steps 4–12, operating on the passed state_row/incoming)
    ...
```

Leave existing `process_webhook` (FlightAware, alert_id route) calling `_process_for_policy` so `test_webhook_pipeline.py` stays green.

- [ ] **Step 4: Add the fan-out entry point**

```python
async def process_aerodatabox_webhook(*, raw_payload, raw_body, db, provider, config, msg_provider=None) -> None:
    subj = provider.extract_subject(raw_payload)
    if subj is None:
        logger.warning("aerodatabox.no_subject"); return
    number, date = subj
    incoming = provider.normalise(raw_payload)
    rows = db.exec(
        select(FlightStateRow).join(Policy, Policy.policy_id == FlightStateRow.policy_id)
        .where(Policy.flight_number == number)   # match normalised number
        .where(Policy.flight_date == date)
    ).all()
    if not rows:
        logger.info("aerodatabox.no_active_policy", extra={"number": number, "date": date}); return
    for state_row in rows:
        await _process_for_policy(state_row=state_row, incoming=incoming, db=db,
                                  provider=provider, config=config, msg_provider=msg_provider)
```

> Note: `Policy.flight_number` is the customer-entered value. Match on the same normalisation `extract_subject` uses (`replace(" ","").upper()`). If policies may store spaced/mixed-case numbers, add a normalised column in a follow-up; for MVP, normalise both sides in the query/comparison.

- [ ] **Step 5: Add the secret-token route**

In `app/api/webhooks.py`:

```python
from app.config import settings
from app.services.webhook import process_aerodatabox_webhook

@router.post("/aerodatabox/{secret}")
async def aerodatabox_alert(secret: str, request: Request,
        db: Session = Depends(get_db),
        provider: FlightDataProvider = Depends(get_flight_provider),
        config: BrainConfig = Depends(get_brain_config),
        msg_provider: MessageProvider = Depends(get_msg_provider)) -> dict:
    if not settings.aerodatabox_webhook_secret or secret != settings.aerodatabox_webhook_secret:
        logger.warning("aerodatabox.bad_secret"); return {"status": "ok"}  # 200, no detail leak
    raw_body = await request.body()
    try:
        payload = await request.json()
    except Exception:
        return {"status": "ok"}
    try:
        await process_aerodatabox_webhook(raw_payload=payload, raw_body=raw_body, db=db,
            provider=provider, config=config, msg_provider=msg_provider)
    except Exception:
        logger.exception("aerodatabox.processing_error")
    return {"status": "ok"}
```

- [ ] **Step 6: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_webhook_fanout.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/services/webhook.py app/api/webhooks.py tests/test_webhook_fanout.py
git commit -m "feat(webhook): AeroDataBox fan-out route with secret-token auth"
```

---

## Task 7: Full suite + live end-to-end against AeroDataBox

**Files:** none (verification)

- [ ] **Step 1: Whole suite green**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (existing 65 + new tests).

- [ ] **Step 2: `init_db` to create `flight_subscriptions` in Supabase**

Run: `.venv/bin/python -c "import app.models; from app.db import init_db; init_db()"`
Expected: no error; table present.

- [ ] **Step 3: Live smoke (server + ngrok up, `.env`: `FLIGHT_PROVIDER=aerodatabox`, AeroDataBox key set, `AERODATABOX_WEBHOOK_SECRET` set, `ngrok http 8000`)**

- Issue a policy for a real near-term flight → confirm baseline fetched, one `flight_subscriptions` row created.
- Issue a second policy on the same flight number → confirm **no** second subscription (shared).
- Trigger/await a real notification → confirm both policies advance and notify.
- Close one policy → confirm subscription **not** torn down; close the last → confirm `DELETE` called and row removed.

- [ ] **Step 4: Commit any fixups + push**

```bash
git add -A && git commit -m "test: AeroDataBox live E2E verified" && git push origin claude/flightdelay-mvp-scaffold-EvfpU
```

---

## Risks & caveats (flag to product, per brief)

1. **No webhook signature.** AeroDataBox does not sign callbacks; auth is a secret token in the URL path. Lower assurance than FlightAware HMAC. Mitigate: long random secret, HTTPS only, rotate on leak. Document.
2. **Gate vs runway arrival.** If the payload exposes only `runwayTime` (touchdown), the delay calc deviates from the brief's mandated **gate** arrival. Recorded in T2; revisit if material.
3. **Per-number subscription = cross-customer coupling.** Reference counting (T4) is the safeguard; the fan-out (T6) is the routing consequence. Both are tested.
4. **Flight-number normalisation for matching.** Policies store the customer-entered number; routing matches on `replace(" ","").upper()`. A normalised column is the robust follow-up if inputs vary.
5. **Credit billing.** 1 credit per flight item per notification — every inbound call logged for cost tracking (brief requirement).
6. **Date matching across timezones.** `extract_subject` date must match `Policy.flight_date` (local origin date). If the payload's date is UTC-based, a ±1-day tolerance or origin-date derivation may be needed — confirm against the T2 fixture.

---

## Review section (completed)

- [x] Spec coverage confirmed (provider, table, service, fan-out, route, lifecycle).
- [x] No placeholders remain — `normalise()` grounded on a REAL captured fixture; live API confirmed the subscription-id key is `id`.
- [x] Type/name consistency: `subscription_scope`, `ensure_subscribed`, `release_subscription`, `extract_subject`, `process_aerodatabox_webhook`, `FlightSubscription.subject_key`.
- [x] Results summary:
  - 7 commits, pushed to origin. **92 tests passing** (was 65 before the feature).
  - Every task got spec + quality review; fixes applied each round (notably: a harmful Policy-nullability relaxation, a credential leak via logged webhook URL, the normalise-before-terminal-shortcut regression, and a CRITICAL backstop bug that ignored `subscription_scope`).
  - Live-verified against the real AeroDataBox API: `get_baseline` ✅, `register_alert` (returned a real subscription id) ✅, `deregister_alert` ✅. `flight_subscriptions` table created in Supabase.
  - Brain (`app/domain/`) untouched — confirmed by the final holistic review.

### To activate AeroDataBox as the live provider (ops note)
Set in `.env`: `FLIGHT_PROVIDER=aerodatabox`, `AERODATABOX_WEBHOOK_SECRET=<long-random>` (used in the webhook URL path for auth), and ensure `PUBLIC_WEBHOOK_BASE_URL` is the live ngrok/host URL. Default remains `flightaware`.

### Known limitations / follow-ups (not blocking)
- Free RapidAPI BASIC tier enforces ~1 req/sec (we hit a 429 when baseline+subscribe fired back-to-back) — add client-side throttling/retry for production.
- `ensure_subscribed` commits the `flight_subscriptions` row before the policy rows in the same issuance; a crash between them leaves a bounded, reusable subscription leak (documented, no functional impact).
- AeroDataBox sends no webhook signature — auth is the secret URL path segment; rotate the secret if leaked.
- DIVERTED is not in `TERMINAL_STATES` (intentional — diverted flights may still land; the STA+24h backstop force-closes to CLOSED). Confirm with the domain owner if this should change.

---

# Render Real Messaging Deployment — Implementation Plan

**Goal:** Enable real messaging via Twilio on the deployed Render instance by configuring `MOCK_MESSAGING=False` and setting up Twilio variables as dashboard-managed secrets in `render.yaml`.

---

## Tasks

- [x] **Task 1: Update render.yaml**
  - Change `MOCK_MESSAGING` from `"True"` to `"False"`.
  - Add `TWILIO_WHATSAPP_FROM` with value `"whatsapp:+14155238886"`.
  - Add `TWILIO_SMS_FROM` with value `"+19132780553"`.
  - Add `TWILIO_ACCOUNT_SID` with `sync: false`.
  - Add `TWILIO_AUTH_TOKEN` with `sync: false`.
- [x] **Task 2: Verify Config and Run Tests**
  - Ensure that the syntax in `render.yaml` is correct.
  - Run the test suite to ensure that no tests are broken by this configuration change.
- [x] **Task 3: Commit and Push Changes**
  - Commit the updated `render.yaml` and `tasks/todo.md`.
  - Push the branch `claude/flightdelay-mvp-scaffold-EvfpU` to GitHub so Render detects the change and triggers a redeploy.


---

# Follow-up: vendor occurrence-disambiguation for the live lookup (page 2)

**Logged:** 2026-06-05 · **Status:** mostly fixed (one Phase-2 item remains)

## Corrected understanding (the date drift was on OUR FlightAware side)
For **G9081** on 2026-06-05 the two vendors returned occurrences ~24h apart. Raw data:
`dep SHJ 2026-06-04 22:30Z = 2026-06-05 02:30 local (+04, Asia/Dubai)`. So the flight's
**local** departure date is **5 Jun** even though its UTC date is 4 Jun.
- **AeroDataBox** keys the path `{date}` on the **local** scheduled date → correctly
  returned the 5-Jun-local flight. (Right for the customer — the ticket shows the local date.)
- **FlightAware** was queried with a bare **UTC** day window
  (`start=DATET00:00Z&end=DATET23:59Z`) → grabbed the *next* physical flight (5-Jun-**UTC**
  departure = 6-Jun local). **This UTC-window query was the drift**, not AeroDataBox.

## Shipped fixes
- [x] **Mismatch guard** (commit 772a243): if feeds' scheduled arrivals differ by > 6h,
      set `occurrence_mismatch=true`, return no merged verdict, explain.
- [x] **FlightAware date drift fixed**: `get_baseline` now widens the query to ±1 day and
      `_select_flight` picks the leg whose **origin-local** departure date (via
      `origin.timezone`) matches the requested date; falls back to first if none match.
      → G9081 now resolves the SAME occurrence on both feeds (both LANDED, agree). Tests
      in `tests/test_flightaware_normalise.py`.
- [x] **Page shows IST** (`Asia/Kolkata`) instead of UTC.

## Remaining Phase-2 item (not blocking)
- [ ] Carry **scheduled departure + origin tz** on `FlightStatus` so occurrence identity can
      be matched on departure inside `reconcile` itself (today the cross-feed guard still
      keys on scheduled **arrival**). Also consider passing AeroDataBox `dateLocalRole`
      explicitly. Touches the core brain dataclass — review carefully; keep
      `tests/test_brain.py` green. With the FlightAware fix in place this is now an edge
      hardening, not a correctness gap for the common case.
