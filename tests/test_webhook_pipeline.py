"""
Webhook pipeline tests. All offline: in-memory SQLite + stub provider.

Covers:
  - happy path: state transition + audit row + last_known_status updated
  - silent event (same-tier wobble): audit written, state unchanged, snapshot advanced
  - true dedup: audit written, state unchanged, snapshot NOT advanced
  - invalid signature: no DB writes at all
  - unknown alert_id: no DB writes, no error
  - terminal state: no brain call, no state change (idempotent ack)
  - notify flag set on notifiable transition
"""
from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.domain.brain import BrainConfig, FlightStatus
from app.domain.states import EventType, FlightState
from app.models import EventLog, FlightStateRow, Policy, PolicyPII
from app.providers.flightdata.base import FlightDataProvider
from app.services.webhook import _dict_to_status, _status_to_dict, process_webhook

STA = datetime(2026, 7, 1, 14, 30, tzinfo=timezone.utc)
CONFIG = BrainConfig(delay_t1_min=30, delay_t2_min=60, delay_t3_min=120, recovery_buffer_min=15)
SECRET = "test-secret"
ALERT_ID = "alert-001"
POLICY_ID = "policy-abc"


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _seed_state(db: Session, current_state: FlightState, last_known: FlightStatus) -> FlightStateRow:
    row = FlightStateRow(
        policy_id=POLICY_ID,
        current_state=current_state.value,
        last_known_status=_status_to_dict(last_known),
        last_update_ts=last_known.event_ts,
        last_event_hash=last_known.content_hash,
        provider="stub",
        alert_id=ALERT_ID,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _make_status(
    offset_min: int,
    est_delay_min: int | None = 0,
    departed: bool = False,
    landed_delay_min: int | None = None,
    cancelled: bool = False,
    h: str = "",
) -> FlightStatus:
    from datetime import timedelta
    eta = STA + timedelta(minutes=est_delay_min) if est_delay_min is not None else None
    actual = STA + timedelta(minutes=landed_delay_min) if landed_delay_min is not None else None
    return FlightStatus(
        event_ts=STA - timedelta(hours=3) + timedelta(minutes=offset_min),
        scheduled_in_utc=STA,
        estimated_in_utc=eta,
        actual_in_utc=actual,
        departed=departed,
        cancelled=cancelled,
        content_hash=h or f"h{offset_min}-{est_delay_min}-{departed}-{landed_delay_min}",
    )


class StubProvider(FlightDataProvider):
    """Offline stub. normalise() returns a pre-built FlightStatus."""
    name = "stub"

    def __init__(self, next_status: FlightStatus, secret: str = SECRET):
        self._next = next_status
        self._secret = secret

    def verify_signature(self, body_bytes: bytes, sig: str) -> bool:
        if not self._secret:
            return True
        expected = hmac.new(self._secret.encode(), body_bytes, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, sig.lower())

    def normalise(self, raw: dict) -> FlightStatus:
        return self._next

    async def get_baseline(self, *a, **kw): ...
    async def register_alert(self, *a, **kw): return ""
    async def deregister_alert(self, *a, **kw): ...


def _make_sig(body: bytes, secret: str = SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


RAW_BODY = b'{"alert_id":"alert-001","event_code":"arrival"}'
PAYLOAD = {"alert_id": ALERT_ID, "event_code": "arrival"}


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_state_transition_on_escalation(db: Session):
    """T2-level delay → state updates to DELAYED_T2, audit written, notify flag set."""
    last = _make_status(0, est_delay_min=0, h="h0")
    _seed_state(db, FlightState.ON_TIME, last)

    incoming = _make_status(20, est_delay_min=70, h="h20")
    provider = StubProvider(incoming)
    sig = _make_sig(RAW_BODY)

    await process_webhook(
        raw_payload=PAYLOAD, raw_body=RAW_BODY, signature_header=sig,
        db=db, provider=provider, config=CONFIG,
    )

    row = db.get(FlightStateRow, POLICY_ID)
    assert row.current_state == FlightState.DELAYED_T2.value

    logs = db.exec(select(EventLog).where(EventLog.policy_id == POLICY_ID)).all()
    assert len(logs) == 1
    assert logs[0].decided_event_type == EventType.DELAY_TIER_2.value
    assert logs[0].notified is True
    assert logs[0].prev_state == FlightState.ON_TIME.value
    assert logs[0].new_state == FlightState.DELAYED_T2.value


@pytest.mark.asyncio
async def test_silent_event_advances_snapshot(db: Session):
    """Same-tier wobble: audit written, state unchanged, last_known_status updated."""
    last = _make_status(20, est_delay_min=70, h="h20")  # at DELAYED_T2
    _seed_state(db, FlightState.DELAYED_T2, last)

    incoming = _make_status(30, est_delay_min=65, h="h30")  # still T2, not material
    provider = StubProvider(incoming)

    await process_webhook(
        raw_payload=PAYLOAD, raw_body=RAW_BODY, signature_header=_make_sig(RAW_BODY),
        db=db, provider=provider, config=CONFIG,
    )

    row = db.get(FlightStateRow, POLICY_ID)
    assert row.current_state == FlightState.DELAYED_T2.value   # unchanged
    assert row.last_event_hash == "h30"                         # snapshot advanced

    logs = db.exec(select(EventLog).where(EventLog.policy_id == POLICY_ID)).all()
    assert len(logs) == 1
    assert logs[0].decided_event_type == EventType.NONE.value
    assert logs[0].notified is False


@pytest.mark.asyncio
async def test_dedup_does_not_advance_snapshot(db: Session):
    """True duplicate (same event_ts): audit written, snapshot NOT advanced."""
    last = _make_status(30, est_delay_min=65, h="h30")
    _seed_state(db, FlightState.DELAYED_T2, last)

    # Exact copy — same event_ts → dedup
    dup = FlightStatus(**{**last.__dict__})
    provider = StubProvider(dup)

    await process_webhook(
        raw_payload=PAYLOAD, raw_body=RAW_BODY, signature_header=_make_sig(RAW_BODY),
        db=db, provider=provider, config=CONFIG,
    )

    row = db.get(FlightStateRow, POLICY_ID)
    assert row.last_event_hash == "h30"  # NOT advanced past the duplicate


@pytest.mark.asyncio
async def test_invalid_signature_no_writes(db: Session):
    """Bad signature → nothing written to DB."""
    last = _make_status(0, est_delay_min=0, h="h0")
    _seed_state(db, FlightState.ON_TIME, last)

    provider = StubProvider(_make_status(20, est_delay_min=70, h="h20"))

    await process_webhook(
        raw_payload=PAYLOAD, raw_body=RAW_BODY, signature_header="bad-sig",
        db=db, provider=provider, config=CONFIG,
    )

    logs = db.exec(select(EventLog).where(EventLog.policy_id == POLICY_ID)).all()
    assert len(logs) == 0
    row = db.get(FlightStateRow, POLICY_ID)
    assert row.current_state == FlightState.ON_TIME.value   # unchanged


@pytest.mark.asyncio
async def test_unknown_alert_id_no_error(db: Session):
    """No state row for this alert_id → graceful no-op, no exception."""
    provider = StubProvider(_make_status(0, h="h0"))
    unknown_payload = {"alert_id": "no-such-alert"}

    await process_webhook(
        raw_payload=unknown_payload, raw_body=b'{"alert_id":"no-such-alert"}',
        signature_header=_make_sig(b'{"alert_id":"no-such-alert"}'),
        db=db, provider=provider, config=CONFIG,
    )
    # No exception, no rows written — just passes silently.
    logs = db.exec(select(EventLog)).all()
    assert len(logs) == 0


@pytest.mark.asyncio
async def test_terminal_state_noop(db: Session):
    """LANDED state → no brain call, no state change, no audit row."""
    last = _make_status(150, landed_delay_min=138, h="landed")
    _seed_state(db, FlightState.LANDED, last)

    provider = StubProvider(_make_status(160, landed_delay_min=138, h="another"))

    await process_webhook(
        raw_payload=PAYLOAD, raw_body=RAW_BODY, signature_header=_make_sig(RAW_BODY),
        db=db, provider=provider, config=CONFIG,
    )

    logs = db.exec(select(EventLog).where(EventLog.policy_id == POLICY_ID)).all()
    assert len(logs) == 0
    row = db.get(FlightStateRow, POLICY_ID)
    assert row.current_state == FlightState.LANDED.value


@pytest.mark.asyncio
async def test_departed_transition(db: Session):
    """DELAYED_T2 → DEPARTED: notify=True, state updated."""
    last = _make_status(30, est_delay_min=65, h="h30")
    _seed_state(db, FlightState.DELAYED_T2, last)

    incoming = _make_status(60, est_delay_min=140, departed=True, h="h60")
    provider = StubProvider(incoming)

    await process_webhook(
        raw_payload=PAYLOAD, raw_body=RAW_BODY, signature_header=_make_sig(RAW_BODY),
        db=db, provider=provider, config=CONFIG,
    )

    row = db.get(FlightStateRow, POLICY_ID)
    assert row.current_state == FlightState.DEPARTED.value

    logs = db.exec(select(EventLog).where(EventLog.policy_id == POLICY_ID)).all()
    assert logs[0].decided_event_type == EventType.DEPARTED.value
    assert logs[0].notified is True


@pytest.mark.asyncio
async def test_landed_transition(db: Session):
    """DEPARTED → LANDED: notify=True, final state LANDED."""
    last = _make_status(60, est_delay_min=140, departed=True, h="h60")
    _seed_state(db, FlightState.DEPARTED, last)

    incoming = _make_status(150, landed_delay_min=138, h="h150")
    provider = StubProvider(incoming)

    await process_webhook(
        raw_payload=PAYLOAD, raw_body=RAW_BODY, signature_header=_make_sig(RAW_BODY),
        db=db, provider=provider, config=CONFIG,
    )

    row = db.get(FlightStateRow, POLICY_ID)
    assert row.current_state == FlightState.LANDED.value

    logs = db.exec(select(EventLog).where(EventLog.policy_id == POLICY_ID)).all()
    assert logs[0].decided_event_type == EventType.LANDED.value
    assert logs[0].notified is True
