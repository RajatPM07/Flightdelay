"""
Scheduler tests. Test _run_backstop() directly — no APScheduler running needed.

Covers:
  - backstop transitions non-terminal flight → CLOSED + audit row written
  - backstop is a no-op when flight already in terminal state
  - backstop calls provider.deregister_alert when alert_id is set
  - backstop skips deregister gracefully when provider raises
  - Policy.status is updated to CLOSED
  - webhook pipeline deregisters alert on terminal transition (LANDED)
  - webhook pipeline does NOT deregister on silent/non-terminal event
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.domain.brain import BrainConfig, FlightStatus
from app.domain.states import EventType, FlightState
from app.models import EventLog, FlightStateRow, Policy, PolicyPII
from app.providers.flightdata.base import FlightDataProvider
from app.services.scheduler import _run_backstop
from app.services.webhook import _status_to_dict, process_webhook

STA = datetime(2026, 7, 1, 14, 30, tzinfo=timezone.utc)
CONFIG = BrainConfig(delay_t1_min=30, delay_t2_min=60, delay_t3_min=120, recovery_buffer_min=15)
POLICY_ID = "pol-sched-001"
ALERT_ID = "fa-alert-sched"


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _seed(db: Session, current_state: FlightState, alert_id: str = ALERT_ID):
    last = FlightStatus(
        event_ts=STA - timedelta(hours=3),
        scheduled_in_utc=STA,
        estimated_in_utc=STA,
        content_hash="baseline",
    )
    db.add(PolicyPII(policy_id=POLICY_ID, name="Test", phone="+911234567890"))
    db.add(Policy(
        policy_id=POLICY_ID, pnr="P1", flight_number="AI101", flight_date="2026-07-01",
        scheduled_in_utc=STA, scheduled_in_tz_offset="+00:00",
        consent_ts=STA, status=current_state.value,
    ))
    db.add(FlightStateRow(
        policy_id=POLICY_ID,
        current_state=current_state.value,
        last_known_status=_status_to_dict(last),
        last_update_ts=last.event_ts,
        last_event_hash="baseline",
        provider="stub",
        alert_id=alert_id,
    ))
    db.commit()


class StubFlightProvider(FlightDataProvider):
    name = "stub"

    def __init__(self, raise_on_deregister: bool = False):
        self.deregistered: list[str] = []
        self.raise_on_deregister = raise_on_deregister

    async def deregister_alert(self, alert_id: str) -> None:
        if self.raise_on_deregister:
            raise RuntimeError("deregister failed")
        self.deregistered.append(alert_id)

    async def get_baseline(self, *a, **kw): ...
    async def register_alert(self, *a, **kw): return ""
    def normalise(self, raw): ...
    def verify_signature(self, *a, **kw): return True


# ------------------------------------------------------------------
# _run_backstop tests
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_backstop_closes_non_terminal(db: Session):
    _seed(db, FlightState.ON_TIME)
    provider = StubFlightProvider()

    await _run_backstop(POLICY_ID, db, provider)

    row = db.get(FlightStateRow, POLICY_ID)
    assert row.current_state == FlightState.CLOSED.value

    policy = db.get(Policy, POLICY_ID)
    assert policy.status == FlightState.CLOSED.value


@pytest.mark.asyncio
async def test_backstop_writes_audit_row(db: Session):
    _seed(db, FlightState.DELAYED_T2)
    provider = StubFlightProvider()

    await _run_backstop(POLICY_ID, db, provider)

    logs = db.exec(select(EventLog).where(EventLog.policy_id == POLICY_ID)).all()
    assert len(logs) == 1
    log = logs[0]
    assert log.source == "backstop"
    assert log.new_state == FlightState.CLOSED.value
    assert log.prev_state == FlightState.DELAYED_T2.value
    assert log.decided_event_type == EventType.NONE.value
    assert log.notified is False


@pytest.mark.asyncio
async def test_backstop_deregisters_alert(db: Session):
    _seed(db, FlightState.ON_TIME, alert_id="fa-99")
    provider = StubFlightProvider()

    await _run_backstop(POLICY_ID, db, provider)

    assert "fa-99" in provider.deregistered


@pytest.mark.asyncio
async def test_backstop_noop_when_already_landed(db: Session):
    _seed(db, FlightState.LANDED)
    provider = StubFlightProvider()

    await _run_backstop(POLICY_ID, db, provider)

    # State unchanged; no audit row; no deregister call.
    row = db.get(FlightStateRow, POLICY_ID)
    assert row.current_state == FlightState.LANDED.value
    logs = db.exec(select(EventLog).where(EventLog.policy_id == POLICY_ID)).all()
    assert len(logs) == 0
    assert len(provider.deregistered) == 0


@pytest.mark.asyncio
async def test_backstop_noop_when_already_closed(db: Session):
    _seed(db, FlightState.CLOSED)
    provider = StubFlightProvider()

    await _run_backstop(POLICY_ID, db, provider)

    row = db.get(FlightStateRow, POLICY_ID)
    assert row.current_state == FlightState.CLOSED.value
    logs = db.exec(select(EventLog).where(EventLog.policy_id == POLICY_ID)).all()
    assert len(logs) == 0


@pytest.mark.asyncio
async def test_backstop_survives_deregister_failure(db: Session):
    """Provider error on deregister must not prevent the DB close from happening."""
    _seed(db, FlightState.ON_TIME)
    provider = StubFlightProvider(raise_on_deregister=True)

    await _run_backstop(POLICY_ID, db, provider)

    row = db.get(FlightStateRow, POLICY_ID)
    assert row.current_state == FlightState.CLOSED.value


@pytest.mark.asyncio
async def test_backstop_noop_when_policy_missing(db: Session):
    """No FlightStateRow → silent no-op, no exception."""
    provider = StubFlightProvider()
    await _run_backstop("no-such-policy", db, provider)  # must not raise


# ------------------------------------------------------------------
# Webhook pipeline — deregister on terminal transition
# ------------------------------------------------------------------

def _make_incoming(offset_min: int, h: str = "", **kwargs) -> FlightStatus:
    return FlightStatus(
        event_ts=STA - timedelta(hours=3) + timedelta(minutes=offset_min),
        scheduled_in_utc=STA,
        content_hash=h or f"h{offset_min}",
        **kwargs,
    )


import hashlib, hmac as _hmac

SECRET = "test-secret"

def _make_sig(body: bytes) -> str:
    return _hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


class StubFlightProviderWithNormalise(StubFlightProvider):
    def __init__(self, next_status: FlightStatus, **kw):
        super().__init__(**kw)
        self._next = next_status

    def normalise(self, raw):
        return self._next

    def verify_signature(self, body_bytes, sig):
        expected = _hmac.new(SECRET.encode(), body_bytes, hashlib.sha256).hexdigest()
        return _hmac.compare_digest(expected, sig.lower())


RAW_BODY = b'{"alert_id":"fa-alert-sched"}'
PAYLOAD = {"alert_id": ALERT_ID}


@pytest.mark.asyncio
async def test_webhook_deregisters_on_landed(db: Session):
    last = _make_incoming(60, estimated_in_utc=STA + timedelta(minutes=140), departed=True)
    _seed(db, FlightState.DEPARTED)
    db.get(FlightStateRow, POLICY_ID).last_known_status = _status_to_dict(last)
    db.commit()

    landed = _make_incoming(150, actual_in_utc=STA + timedelta(minutes=138), h="land")
    provider = StubFlightProviderWithNormalise(landed)

    await process_webhook(
        raw_payload=PAYLOAD, raw_body=RAW_BODY, signature_header=_make_sig(RAW_BODY),
        db=db, provider=provider, config=CONFIG,
    )

    assert ALERT_ID in provider.deregistered


# ------------------------------------------------------------------
# Backstop — per_flight_number scope (AeroDataBox) does NOT tear down
# shared subscription while a second policy is still active.
# ------------------------------------------------------------------

POLICY_ID_B = "pol-sched-002"
SHARED_SUB_ID = "adb-sub-shared"
FLIGHT_NUMBER = "AI101"


class StubAeroDataBoxProvider(FlightDataProvider):
    """Stub with subscription_scope = 'per_flight_number' (AeroDataBox-like)."""
    name = "aerodatabox"
    subscription_scope = "per_flight_number"

    def __init__(self):
        self.deregistered: list[str] = []

    async def deregister_alert(self, alert_id: str) -> None:
        self.deregistered.append(alert_id)

    async def get_baseline(self, *a, **kw): ...
    async def register_alert(self, *a, **kw): return SHARED_SUB_ID
    def normalise(self, raw): ...
    def verify_signature(self, *a, **kw): return True


def _seed_two_policies(db: Session):
    """Seed two active policies on the same flight number + a shared FlightSubscription."""
    last = FlightStatus(
        event_ts=STA - timedelta(hours=3),
        scheduled_in_utc=STA,
        estimated_in_utc=STA,
        content_hash="baseline",
    )
    from app.providers.flightdata.carriers import normalize_flight_number
    norm_key = normalize_flight_number(FLIGHT_NUMBER)

    for pid in (POLICY_ID, POLICY_ID_B):
        db.add(PolicyPII(policy_id=pid, name="Test", phone="+911234567890"))
        db.add(Policy(
            policy_id=pid, pnr="P1", flight_number=FLIGHT_NUMBER, flight_date="2026-07-01",
            scheduled_in_utc=STA, scheduled_in_tz_offset="+00:00",
            consent_ts=STA, status=FlightState.ON_TIME.value,
        ))
        db.add(FlightStateRow(
            policy_id=pid,
            current_state=FlightState.ON_TIME.value,
            last_known_status=_status_to_dict(last),
            last_update_ts=last.event_ts,
            last_event_hash="baseline",
            provider="aerodatabox",
            alert_id=None,  # per_flight_number providers don't use per-row alert_id
        ))

    from app.models import FlightSubscription
    db.add(FlightSubscription(
        subject_key=norm_key,
        provider="aerodatabox",
        subscription_id=SHARED_SUB_ID,
    ))
    db.commit()


@pytest.mark.asyncio
async def test_backstop_per_flight_number_does_not_deregister_shared_subscription(db: Session):
    """
    AeroDataBox (per_flight_number) backstop for one policy must NOT tear down the
    shared FlightSubscription while the second policy is still active.
    """
    _seed_two_policies(db)
    provider = StubAeroDataBoxProvider()

    # Run backstop for only POLICY_ID — POLICY_ID_B remains active.
    await _run_backstop(POLICY_ID, db, provider)

    # Policy A is now CLOSED.
    row_a = db.get(FlightStateRow, POLICY_ID)
    assert row_a.current_state == FlightState.CLOSED.value

    # Policy B is still active (ON_TIME).
    row_b = db.get(FlightStateRow, POLICY_ID_B)
    assert row_b.current_state == FlightState.ON_TIME.value

    # The shared AeroDataBox subscription was NOT deregistered.
    assert len(provider.deregistered) == 0

    # The FlightSubscription row still exists.
    from app.models import FlightSubscription
    from app.providers.flightdata.carriers import normalize_flight_number
    norm_key = normalize_flight_number(FLIGHT_NUMBER)
    sub = db.get(FlightSubscription, norm_key)
    assert sub is not None
    assert sub.subscription_id == SHARED_SUB_ID


@pytest.mark.asyncio
async def test_webhook_does_not_deregister_on_silent(db: Session):
    last = _make_incoming(20, estimated_in_utc=STA + timedelta(minutes=70))
    _seed(db, FlightState.DELAYED_T2)
    db.get(FlightStateRow, POLICY_ID).last_known_status = _status_to_dict(last)
    db.commit()

    wobble = _make_incoming(30, estimated_in_utc=STA + timedelta(minutes=65), h="h30")
    provider = StubFlightProviderWithNormalise(wobble)

    await process_webhook(
        raw_payload=PAYLOAD, raw_body=RAW_BODY, signature_header=_make_sig(RAW_BODY),
        db=db, provider=provider, config=CONFIG,
    )

    assert len(provider.deregistered) == 0
