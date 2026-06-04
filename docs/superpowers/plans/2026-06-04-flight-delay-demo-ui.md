# Flight Delay Live-Pipeline Demo UI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A polished, self-contained demo page (served by FastAPI) that issues a monitoring policy and lets the user drive synthetic flight events to watch the real brain/state-machine/notifier react live.

**Architecture:** Two new backend endpoints (`GET /policies/{id}` read model, `POST /demo/simulate`) plus a page route, all in the existing FastAPI app. The simulator builds a synthetic `FlightStatus` and runs it through the SAME `_process_for_policy` the webhooks use. The page is one static HTML file (Tailwind Play CDN + vanilla JS, no build step). Demo runs with `DEMO_MODE=True` + `MOCK_PROVIDERS=True` — self-contained, no real API calls, no real notifications. Brain (`app/domain/`) untouched.

**Tech Stack:** FastAPI, SQLModel, pytest; Tailwind (CDN) + Google Fonts (Space Grotesk, Inter) + vanilla JS.

---

## File structure
| File | Responsibility | Action |
|---|---|---|
| `app/config.py` | `demo_mode: bool = False` | Modify |
| `app/api/demo.py` | page route `GET /` + `POST /demo/simulate`, both `DEMO_MODE`-gated | Create |
| `app/api/policies.py` | `GET /policies/{policy_id}` read model + masking helpers | Modify |
| `app/services/simulator.py` | build synthetic `FlightStatus` per event; run `_process_for_policy` | Create |
| `app/static/demo.html` | the single polished page | Create |
| `app/main.py` | include the demo router | Modify |
| `tests/test_policy_read.py` | read-model shape, masking, 404 | Create |
| `tests/test_demo_simulate.py` | each event → transition + audit; demo-mode gating | Create |
| `tests/test_demo_page.py` | page served when DEMO_MODE on, 404 off | Create |

Reference reading for implementers: `app/services/webhook.py` (`_process_for_policy`, `_status_to_dict`), `app/domain/brain.py` (`FlightStatus`, `decide`), `app/domain/states.py` (`BrainConfig`, states), `app/models.py`, `app/api/policies.py` (router style).

---

## Task 1: `demo_mode` config + page route (gated) + router wiring

**Files:** Modify `app/config.py`, `app/main.py`; Create `app/api/demo.py`, `app/static/demo.html` (placeholder), `tests/test_demo_page.py`.

- [ ] **Step 1: Write the failing test** — `tests/test_demo_page.py`
```python
import pytest
from fastapi.testclient import TestClient
import app.config as cfg
from app.main import app

client = TestClient(app)


def test_page_served_when_demo_mode_on(monkeypatch):
    monkeypatch.setattr(cfg.settings, "demo_mode", True)
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "TripSecure" in r.text


def test_page_404_when_demo_mode_off(monkeypatch):
    monkeypatch.setattr(cfg.settings, "demo_mode", False)
    r = client.get("/")
    assert r.status_code == 404
```

- [ ] **Step 2: Run to verify it fails** — `.venv/bin/python -m pytest tests/test_demo_page.py -q` → FAIL (route missing).

- [ ] **Step 3: Add the setting** — in `app/config.py` `Settings`, add near `app_env`:
```python
    demo_mode: bool = False
```

- [ ] **Step 4: Create `app/api/demo.py`**
```python
"""Demo UI: the live-pipeline page and the synthetic-event simulator.

Both routes are gated behind DEMO_MODE so production deploys expose neither.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.config import settings

router = APIRouter(tags=["demo"])

_PAGE = Path(__file__).resolve().parent.parent / "static" / "demo.html"


def _require_demo() -> None:
    if not settings.demo_mode:
        raise HTTPException(status_code=404, detail="Not found")


@router.get("/")
async def demo_page() -> FileResponse:
    _require_demo()
    return FileResponse(_PAGE, media_type="text/html")
```

- [ ] **Step 5: Placeholder page** — create `app/static/demo.html` with minimal content (full build in Task 5):
```html
<!doctype html><html><head><meta charset="utf-8"><title>TripSecure+ Demo</title></head>
<body>TripSecure+ Flight Delay — Live Pipeline Demo (placeholder)</body></html>
```

- [ ] **Step 6: Wire the router** — in `app/main.py`, import and include it:
```python
from app.api import demo, policies, webhooks
...
app.include_router(demo.router)
```

- [ ] **Step 7: Run** — `.venv/bin/python -m pytest tests/test_demo_page.py -q` → 2 passed. Full suite still green.

- [ ] **Step 8: Commit**
```bash
git add app/config.py app/api/demo.py app/static/demo.html app/main.py tests/test_demo_page.py
git commit -m "feat(demo): DEMO_MODE-gated demo page route"
```

---

## Task 2: `GET /policies/{policy_id}` read model + PII masking

**Files:** Modify `app/api/policies.py`; Create `tests/test_policy_read.py`.

- [ ] **Step 1: Write the failing test** — `tests/test_policy_read.py`
```python
from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
import app.models
from app.models import Policy, PolicyPII, FlightStateRow, EventLog, Notification
from app.main import app
from app.deps import get_db

STA = datetime(2026, 6, 12, 14, 30, tzinfo=timezone.utc)


@pytest.fixture()
def client():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    def _seed():
        with Session(engine) as db:
            db.add(PolicyPII(policy_id="P1", name="Rajat Sharma", phone="+919876543299", email="r@x.com"))
            db.add(Policy(policy_id="P1", pnr="AB1", flight_number="6E1341", flight_date="2026-06-12",
                          scheduled_in_utc=STA, scheduled_in_tz_offset="+00:00", consent_ts=STA, status="DELAYED_T2"))
            db.add(FlightStateRow(policy_id="P1", current_state="DELAYED_T2", last_known_status={},
                                  last_update_ts=STA, last_event_hash="h", provider="mock", alert_id="a"))
            db.add(EventLog(policy_id="P1", source="test", raw_payload={}, prev_state="ON_TIME",
                            new_state="DELAYED_T2", decided_event_type="DELAY_TIER_2", notified=True))
            db.add(Notification(id="N1", policy_id="P1", channel="whatsapp", template="delay_t2",
                                body="Your flight 6E1341 is delayed", status="sent", sent_ts=STA))
            db.commit()
    _seed()
    def _get_db():
        with Session(engine) as s:
            yield s
    app.dependency_overrides[get_db] = _get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_status_read_shape_and_masking(client):
    r = client.get("/policies/P1")
    assert r.status_code == 200
    d = r.json()
    assert d["flight_number"] == "6E1341"
    assert d["current_state"] == "DELAYED_T2"
    assert d["contact"]["name"] == "Rajat S."          # masked
    assert d["contact"]["phone_masked"].startswith("+91 98")
    assert "9876543" not in d["contact"]["phone_masked"]  # middle hidden
    assert d["contact"]["phone_masked"].endswith("99")
    assert len(d["timeline"]) == 1 and d["timeline"][0]["new_state"] == "DELAYED_T2"
    assert d["timeline"][0]["notified"] is True
    assert len(d["notifications"]) == 1 and d["notifications"][0]["channel"] == "whatsapp"


def test_status_404_for_unknown(client):
    assert client.get("/policies/NOPE").status_code == 404
```

- [ ] **Step 2: Run to verify it fails** — `.venv/bin/python -m pytest tests/test_policy_read.py -q` → FAIL (route missing).

- [ ] **Step 3: Implement** — add to `app/api/policies.py` (reuse the existing `router`, prefix `/policies`):
```python
from app.models import EventLog, FlightStateRow, Notification, Policy, PolicyPII
from sqlmodel import select


def _mask_name(name: str) -> str:
    parts = (name or "").strip().split()
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return f"{parts[0]} {parts[-1][0]}."


def _mask_phone(phone: str) -> str:
    p = (phone or "").strip()
    if len(p) < 4:
        return "••" if p else ""
    # keep a leading chunk and the last 2 digits; mask the middle.
    head = p[:5] if p.startswith("+") else p[:2]
    return f"{head}••••{p[-2:]}"


@router.get("/{policy_id}")
async def policy_status(policy_id: str, db: Session = Depends(get_db)) -> dict:
    policy = db.get(Policy, policy_id)
    fs = db.get(FlightStateRow, policy_id)
    if policy is None or fs is None:
        raise HTTPException(status_code=404, detail="policy not found")
    pii = db.get(PolicyPII, policy_id)
    logs = db.exec(select(EventLog).where(EventLog.policy_id == policy_id)).all()
    logs.sort(key=lambda l: l.received_ts, reverse=True)
    notifs = db.exec(select(Notification).where(Notification.policy_id == policy_id)).all()
    notifs.sort(key=lambda n: (n.sent_ts or n.id))
    return {
        "policy_id": policy_id,
        "flight_number": policy.flight_number,
        "flight_date": policy.flight_date,
        "current_state": fs.current_state,
        "scheduled_in_utc": policy.scheduled_in_utc.isoformat() if policy.scheduled_in_utc else None,
        "last_update_ts": fs.last_update_ts.isoformat() if fs.last_update_ts else None,
        "contact": {
            "name": _mask_name(pii.name) if pii else "",
            "phone_masked": _mask_phone(pii.phone) if pii else "",
        },
        "timeline": [
            {"ts": l.received_ts.isoformat() if l.received_ts else None,
             "prev_state": l.prev_state, "new_state": l.new_state,
             "event_type": l.decided_event_type, "notified": l.notified}
            for l in logs
        ],
        "notifications": [
            {"channel": n.channel, "body": n.body, "status": n.status,
             "sent_ts": n.sent_ts.isoformat() if n.sent_ts else None}
            for n in notifs
        ],
    }
```
Check `app/models.py` for the exact field names (`EventLog.received_ts`, `Notification.sent_ts`, etc.) and adjust if they differ. Ensure `HTTPException` is imported (it already is for the POST handler).

- [ ] **Step 4: Run** — `.venv/bin/python -m pytest tests/test_policy_read.py -q` → 2 passed; full suite green.

- [ ] **Step 5: Commit**
```bash
git add app/api/policies.py tests/test_policy_read.py
git commit -m "feat(api): GET /policies/{id} status read model with PII masking"
```

---

## Task 3: simulator service (synthetic event → real pipeline)

**Files:** Create `app/services/simulator.py`, `tests/test_demo_simulate.py` (service-level tests here; endpoint tests in Task 4).

- [ ] **Step 1: Write the failing test** — `tests/test_demo_simulate.py`
```python
from datetime import datetime, timezone
import pytest
from sqlmodel import Session, SQLModel, create_engine
import app.models
from app.models import Policy, PolicyPII, FlightStateRow
from app.domain.states import BrainConfig
from app.services.simulator import simulate_event, VALID_EVENTS

STA = datetime(2026, 6, 12, 14, 30, tzinfo=timezone.utc)
CFG = BrainConfig(delay_t1_min=30, delay_t2_min=60, delay_t3_min=120, recovery_buffer_min=15)


class StubProvider:
    name = "mock"
    subscription_scope = "per_policy"
    async def deregister_alert(self, alert_id): pass


@pytest.fixture()
def db():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        yield s


def _seed(db):
    db.add(PolicyPII(policy_id="P1", name="N", phone="+919876543299"))
    db.add(Policy(policy_id="P1", pnr="A", flight_number="6E1341", flight_date="2026-06-12",
                  scheduled_in_utc=STA, scheduled_in_tz_offset="+00:00", consent_ts=STA, status="ON_TIME"))
    db.add(FlightStateRow(policy_id="P1", current_state="ON_TIME",
                          last_known_status={"event_ts": STA.isoformat(), "scheduled_in_utc": STA.isoformat(),
                                             "estimated_in_utc": STA.isoformat(), "actual_in_utc": None,
                                             "departed": False, "cancelled": False, "diverted": False,
                                             "content_hash": "base"},
                          last_update_ts=STA, last_event_hash="base", provider="mock", alert_id="a"))
    db.commit()


@pytest.mark.asyncio
async def test_delay_t2_transitions_and_audits(db):
    _seed(db)
    out = await simulate_event(policy_id="P1", event="delay_t2", db=db,
                               provider=StubProvider(), config=CFG, msg_provider=None)
    assert out["current_state"] == "DELAYED_T2"
    assert "flight" in out["payload"]            # raw payload returned for the console
    assert db.get(FlightStateRow, "P1").current_state == "DELAYED_T2"


@pytest.mark.asyncio
async def test_cancel_and_land(db):
    _seed(db)
    out = await simulate_event(policy_id="P1", event="cancel", db=db,
                               provider=StubProvider(), config=CFG, msg_provider=None)
    assert out["current_state"] == "CANCELLED"


@pytest.mark.asyncio
async def test_unknown_event_rejected(db):
    _seed(db)
    with pytest.raises(ValueError):
        await simulate_event(policy_id="P1", event="explode", db=db,
                             provider=StubProvider(), config=CFG, msg_provider=None)
    assert "delay_t2" in VALID_EVENTS
```

- [ ] **Step 2: Run to verify it fails** — `.venv/bin/python -m pytest tests/test_demo_simulate.py -q` → FAIL (module missing).

- [ ] **Step 3: Implement `app/services/simulator.py`**
```python
"""Demo simulator: turn a button press into a synthetic FlightStatus and run it
through the REAL webhook pipeline (_process_for_policy), so the demo exercises the
same brain/persistence/notify path as production. No external calls."""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlmodel import Session

from app.domain.brain import FlightStatus
from app.domain.states import BrainConfig
from app.models import FlightStateRow, Policy
from app.services.webhook import _process_for_policy

VALID_EVENTS = ("delay_t1", "delay_t2", "delay_t3", "recover", "depart", "land", "cancel", "divert")


def _build_status(event: str, scheduled: datetime, config: BrainConfig, now: datetime) -> FlightStatus:
    est = scheduled
    actual = None
    departed = cancelled = diverted = False
    if event == "delay_t1":
        est = scheduled + timedelta(minutes=config.delay_t1_min + 10)
    elif event == "delay_t2":
        est = scheduled + timedelta(minutes=config.delay_t2_min + 30)
    elif event == "delay_t3":
        est = scheduled + timedelta(minutes=config.delay_t3_min + 30)
    elif event == "recover":
        est = scheduled + timedelta(minutes=max(0, config.delay_t1_min - config.recovery_buffer_min - 5))
    elif event == "depart":
        departed = True
        est = scheduled + timedelta(minutes=config.delay_t2_min + 30)
    elif event == "land":
        departed = True
        actual = scheduled + timedelta(minutes=config.delay_t2_min + 18)
    elif event == "cancel":
        cancelled = True
    elif event == "divert":
        diverted = True
    else:
        raise ValueError(f"unknown event: {event!r}")
    content_hash = hashlib.sha256(f"{event}-{now.isoformat()}".encode()).hexdigest()
    return FlightStatus(event_ts=now, scheduled_in_utc=scheduled, estimated_in_utc=est,
                        actual_in_utc=actual, departed=departed, cancelled=cancelled,
                        diverted=diverted, content_hash=content_hash)


def _display_payload(event: str, s: FlightStatus) -> dict:
    """Human-readable synthetic provider payload for the raw-webhook console."""
    return {
        "event": event,
        "received_at": s.event_ts.isoformat(),
        "flight": {
            "scheduled_in": s.scheduled_in_utc.isoformat(),
            "estimated_in": s.estimated_in_utc.isoformat() if s.estimated_in_utc else None,
            "actual_in": s.actual_in_utc.isoformat() if s.actual_in_utc else None,
            "departed": s.departed, "cancelled": s.cancelled, "diverted": s.diverted,
        },
    }


async def simulate_event(*, policy_id: str, event: str, db: Session,
                         provider, config: BrainConfig, msg_provider=None) -> dict:
    if event not in VALID_EVENTS:
        raise ValueError(f"unknown event: {event!r}. valid: {VALID_EVENTS}")
    policy = db.get(Policy, policy_id)
    state_row = db.get(FlightStateRow, policy_id)
    if policy is None or state_row is None:
        raise ValueError(f"policy not found: {policy_id}")
    now = datetime.now(timezone.utc)
    incoming = _build_status(event, policy.scheduled_in_utc, config, now)
    payload = _display_payload(event, incoming)
    await _process_for_policy(state_row=state_row, incoming=incoming, raw_payload=payload,
                              db=db, provider=provider, config=config, msg_provider=msg_provider)
    fresh = db.get(FlightStateRow, policy_id)
    return {"current_state": fresh.current_state, "payload": payload}
```
Verify `_process_for_policy`'s keyword signature in `app/services/webhook.py` and the `FlightStatus` field names in `app/domain/brain.py`; adjust if they differ.

- [ ] **Step 4: Run** — `.venv/bin/python -m pytest tests/test_demo_simulate.py -q` → 3 passed.

- [ ] **Step 5: Commit**
```bash
git add app/services/simulator.py tests/test_demo_simulate.py
git commit -m "feat(demo): synthetic-event simulator over the real pipeline"
```

---

## Task 4: `POST /demo/simulate` endpoint (gated)

**Files:** Modify `app/api/demo.py`; add tests to `tests/test_demo_simulate.py`.

- [ ] **Step 1: Write the failing test** — append to `tests/test_demo_simulate.py`
```python
from fastapi.testclient import TestClient
from app.main import app
from app.deps import get_db, get_flight_provider, get_msg_provider
import app.config as cfg


def _override(engine_seeded):
    def _get_db():
        with Session(engine_seeded) as s:
            yield s
    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_flight_provider] = lambda: StubProvider()
    app.dependency_overrides[get_msg_provider] = lambda: None


@pytest.fixture()
def seeded_engine():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        _seed(s)
    yield eng


def test_simulate_endpoint_demo_on(monkeypatch, seeded_engine):
    monkeypatch.setattr(cfg.settings, "demo_mode", True)
    _override(seeded_engine)
    try:
        r = TestClient(app).post("/demo/simulate", json={"policy_id": "P1", "event": "delay_t2"})
        assert r.status_code == 200
        assert r.json()["current_state"] == "DELAYED_T2"
    finally:
        app.dependency_overrides.clear()


def test_simulate_endpoint_404_when_demo_off(monkeypatch, seeded_engine):
    monkeypatch.setattr(cfg.settings, "demo_mode", False)
    _override(seeded_engine)
    try:
        r = TestClient(app).post("/demo/simulate", json={"policy_id": "P1", "event": "delay_t2"})
        assert r.status_code == 404
    finally:
        app.dependency_overrides.clear()
```

- [ ] **Step 2: Run to verify it fails** — `.venv/bin/python -m pytest tests/test_demo_simulate.py -q` → the two new tests FAIL (endpoint missing).

- [ ] **Step 3: Implement** — add to `app/api/demo.py`:
```python
from fastapi import Depends
from pydantic import BaseModel
from sqlmodel import Session

from app.deps import get_brain_config, get_db, get_flight_provider, get_msg_provider
from app.domain.states import BrainConfig
from app.services.simulator import simulate_event


class SimulateRequest(BaseModel):
    policy_id: str
    event: str


@router.post("/demo/simulate")
async def demo_simulate(
    req: SimulateRequest,
    db: Session = Depends(get_db),
    provider=Depends(get_flight_provider),
    config: BrainConfig = Depends(get_brain_config),
    msg_provider=Depends(get_msg_provider),
) -> dict:
    _require_demo()
    try:
        return await simulate_event(policy_id=req.policy_id, event=req.event, db=db,
                                    provider=provider, config=config, msg_provider=msg_provider)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
```

- [ ] **Step 4: Run** — `.venv/bin/python -m pytest tests/test_demo_simulate.py -q` → all pass; full suite green.

- [ ] **Step 5: Commit**
```bash
git add app/api/demo.py tests/test_demo_simulate.py
git commit -m "feat(demo): POST /demo/simulate endpoint (DEMO_MODE-gated)"
```

---

## Task 5: the polished demo page (`app/static/demo.html`)

**Files:** Replace `app/static/demo.html` with the full page; extend `tests/test_demo_page.py`.

- [ ] **Step 1: Add markers test** — append to `tests/test_demo_page.py`:
```python
def test_page_has_key_elements(monkeypatch):
    monkeypatch.setattr(cfg.settings, "demo_mode", True)
    html = client.get("/").text
    for marker in ['id="issue-form"', 'id="dashboard"', 'id="stepper"',
                   'id="sim-controls"', 'id="timeline"', 'id="notifications"',
                   'id="payload-console"', 'Space Grotesk', '#B02A30']:
        assert marker in html, f"missing {marker}"
```

- [ ] **Step 2: Run to verify it fails** — `.venv/bin/python -m pytest tests/test_demo_page.py::test_page_has_key_elements -q` → FAIL (placeholder).

- [ ] **Step 3: Build the page.** Replace `app/static/demo.html` with a complete page meeting the spec. It MUST include the element IDs from the test and satisfy the brand/behaviour requirements below. Concretely:

  **Head:** Tailwind Play CDN (`https://cdn.tailwindcss.com`) + inline config setting `colors.primary=#B02A30`, `secondary=#F99D27`, `tertiary=#005B75`, `emerald` for on-time, fonts `Space Grotesk` (headings) + `Inter` (body) via Google Fonts `<link>`. Background `#0B0F19`, cards `#111625`.

  **Layout:** two columns.
  - **Left** `id="issue-form"` inside a CSS **mobile phone frame** (rounded, notch, shadow) — the ICICI Lombard customer portal. Fields: PNR, Flight Number, Date, Passenger Name, Phone, DPDP consent checkbox (unchecked by default). Crimson CTA **"Purchase TripSecure+"**.
  - **Right** `id="dashboard"` — starts with `filter: blur` + a dimming overlay `"Awaiting Active Policy…"`. Contains `id="stepper"`, `id="sim-controls"`, `id="payload-console"` (collapsible), `id="timeline"`, `id="notifications"`.

  **Stepper** `id="stepper"`: nodes `Registered → On-Time → Delayed → Departed → Landed`; active node glows/pulses; `Cancelled`/`Diverted` shown as terminal chips. Color map: ON_TIME→emerald, DELAYED_T1/T2→`secondary`, DELAYED_T3/CANCELLED→`primary`, DEPARTED/LANDED→`tertiary`.

  **Sim controls** `id="sim-controls"`: buttons `Delay +90m`(`delay_t2`), `Depart`(`depart`), `Land`(`land`), `Recover`(`recover`), `Cancel`(`cancel`), `Divert`(`divert`). Optionally also `Delay +45m`(`delay_t1`) / `Delay +150m`(`delay_t3`). Each does `POST /demo/simulate {policy_id, event}` then refreshes.

  **JS behaviour (vanilla):**
  1. On submit: `POST /policies` with the form values + `consent:true`; on success store `policyId`, show the minting spinner→checkmark "Policy Minted!", trigger the **data-flow animation** (a glowing line/particles from the phone to the dashboard via CSS/canvas), then un-blur the dashboard with a pulse. Begin polling.
  2. `refresh()` = `GET /policies/{policyId}` → render stepper (highlight `current_state`), timeline (newest first; notified rows flagged with 🔔), notification cards (channel icon + body + masked contact from `contact.phone_masked`), and update the flight summary.
  3. Poll `refresh()` every 2s while a policy is active; also call it immediately after each simulate click.
  4. Simulate click → `POST /demo/simulate` → put the returned `payload` into `id="payload-console"` (pretty-printed JSON) → `refresh()`.
  5. Subtle highlight animation when the state badge or timeline changes.

  Keep it a single self-contained file; no external JS deps beyond the Tailwind CDN. Aim for genuinely polished visuals (spacing, shadows, motion) per the brand.

- [ ] **Step 4: Run** — `.venv/bin/python -m pytest tests/test_demo_page.py -q` → all pass.

- [ ] **Step 5: Commit**
```bash
git add app/static/demo.html tests/test_demo_page.py
git commit -m "feat(demo): polished live-pipeline demo page (ICICI Lombard theme)"
```

---

## Task 6: full-suite + live E2E walkthrough

**Files:** none (verification).

- [ ] **Step 1: Full suite** — `.venv/bin/python -m pytest -q` → all green (existing 93 + new tests).

- [ ] **Step 2: Live run** — start the demo server:
```bash
DEMO_MODE=True MOCK_PROVIDERS=True .venv/bin/python -m uvicorn app.main:app --port 8000
```
Open `http://localhost:8000/`. Verify the full story by hand:
- Fill the form, click **Purchase TripSecure+** → minting → data-flow animation → dashboard reveals, stepper at `On-Time`.
- Click `Delay +90m` → stepper → `Delayed` (orange), a `DELAY_TIER_2` timeline row appears with 🔔, a WhatsApp notification card renders with a masked number, and the payload console shows the synthetic JSON.
- Click `Depart` → `Departed` (blue); `Land` → `Landed` (blue). Try `Cancel` / `Divert` on a fresh policy → crimson terminal chip.

- [ ] **Step 3: Commit any fixups, then push**
```bash
git add -A && git commit -m "test: demo UI live E2E verified" && git push origin claude/flightdelay-mvp-scaffold-EvfpU
```

---

## Self-review notes
- **Spec coverage:** brand/colors/fonts (Task 5 head), split layout + phone frame (T5), data-flow animation + reveal (T5), stepper with color map (T5), sim controls→`/demo/simulate` (T4/T5), payload console (T5), audit+notification cards with masking (T2/T5), `GET /policies/{id}` (T2), `POST /demo/simulate` gated (T4), `DEMO_MODE` gating (T1/T4), mock-mode self-contained (T6 run cmd). All covered.
- **No placeholders:** backend tasks carry complete code; the HTML task lists exact required IDs + behaviours (the implementer writes the markup/CSS, which is inherently bespoke — the test pins the contract).
- **Name consistency:** `demo_mode`, `_require_demo`, `simulate_event`, `VALID_EVENTS`, `_build_status`, `SimulateRequest`, `policy_status`, `_mask_phone`/`_mask_name`, element IDs (`issue-form`, `dashboard`, `stepper`, `sim-controls`, `timeline`, `notifications`, `payload-console`) — consistent across tasks.
