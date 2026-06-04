"""
Issuance service tests. All offline: in-memory SQLite + stub FlightDataProvider.

Covers:
  - on-time baseline → ON_TIME state
  - delayed baseline → correct tier
  - cancelled baseline → CANCELLED state
  - consent=False → ValueError
  - DB rows written: PolicyPII, Policy, FlightStateRow, EventLog
  - PII isolation: only policy_pii holds name/phone/email
  - alert_id stored in flight_state
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.domain.brain import BrainConfig, FlightStatus
from app.domain.states import EventType, FlightState
from app.models import EventLog, FlightStateRow, Policy, PolicyPII
from app.providers.flightdata.base import FlightDataProvider
from app.services.issuance import issue_policy

STA = datetime(2026, 7, 1, 14, 30, tzinfo=timezone.utc)
NOW = datetime(2026, 7, 1, 11, 0, tzinfo=timezone.utc)

CONFIG = BrainConfig(delay_t1_min=30, delay_t2_min=60, delay_t3_min=120, recovery_buffer_min=15)

BASE_REQ = dict(
    pnr="XYZ123",
    flight_number="AI101",
    flight_date="2026-07-01",
    name="Rajat Sharma",
    phone="+911234567890",
    email="rajat@example.com",
    consent=True,
)


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


class StubProvider(FlightDataProvider):
    """Offline stub. Baseline is configurable per test."""
    name = "stub"

    def __init__(self, baseline: FlightStatus, alert_id: str = "alert-stub-001"):
        self._baseline = baseline
        self._alert_id = alert_id

    async def get_baseline(self, flight_number: str, flight_date: str) -> FlightStatus:
        return self._baseline

    async def register_alert(self, policy_id: str, flight_number: str, flight_date: str) -> str:
        return self._alert_id

    async def deregister_alert(self, alert_id: str) -> None:
        pass

    def normalise(self, raw_payload: dict) -> FlightStatus:
        raise NotImplementedError


def _make_status(est_delay_min: int | None = 0, **kwargs) -> FlightStatus:
    eta = (STA.replace(second=0, microsecond=0).__class__(
        STA.year, STA.month, STA.day,
        STA.hour + (est_delay_min or 0) // 60,
        STA.minute + (est_delay_min or 0) % 60,
        tzinfo=timezone.utc,
    )) if est_delay_min is not None else None
    # simpler: just use timedelta
    from datetime import timedelta
    eta = STA + timedelta(minutes=est_delay_min) if est_delay_min is not None else None
    return FlightStatus(
        event_ts=NOW,
        scheduled_in_utc=STA,
        estimated_in_utc=eta,
        content_hash=f"hash-{est_delay_min}",
        **kwargs,
    )


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_issue_policy_on_time(db: Session):
    provider = StubProvider(_make_status(est_delay_min=10))
    policy_id, state = await issue_policy(**BASE_REQ, db=db, provider=provider, config=CONFIG)

    assert state == FlightState.ON_TIME.value
    assert policy_id  # non-empty hex token

    policy = db.get(Policy, policy_id)
    assert policy is not None
    assert policy.flight_number == "AI101"
    assert policy.status == FlightState.ON_TIME.value
    # SQLite strips tzinfo on round-trip; compare naive (Postgres preserves it).
    assert policy.scheduled_in_utc.replace(tzinfo=None) == STA.replace(tzinfo=None)


@pytest.mark.asyncio
async def test_issue_policy_delayed_t2(db: Session):
    provider = StubProvider(_make_status(est_delay_min=70))
    policy_id, state = await issue_policy(**BASE_REQ, db=db, provider=provider, config=CONFIG)

    assert state == FlightState.DELAYED_T2.value
    policy = db.get(Policy, policy_id)
    assert policy.status == FlightState.DELAYED_T2.value


@pytest.mark.asyncio
async def test_issue_policy_cancelled_baseline(db: Session):
    provider = StubProvider(_make_status(est_delay_min=None, cancelled=True))
    policy_id, state = await issue_policy(**BASE_REQ, db=db, provider=provider, config=CONFIG)

    assert state == FlightState.CANCELLED.value


@pytest.mark.asyncio
async def test_pii_isolation(db: Session):
    """PII must be in policy_pii only — Policy row must NOT hold name/phone/email."""
    provider = StubProvider(_make_status(est_delay_min=0))
    policy_id, _ = await issue_policy(**BASE_REQ, db=db, provider=provider, config=CONFIG)

    pii = db.get(PolicyPII, policy_id)
    assert pii.name == "Rajat Sharma"
    assert pii.phone == "+911234567890"
    assert pii.email == "rajat@example.com"

    policy = db.get(Policy, policy_id)
    assert not hasattr(policy, "name")
    assert not hasattr(policy, "phone")
    assert not hasattr(policy, "email")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raw_phone", "stored_phone"),
    [
        ("7358467199", "+917358467199"),
        ("73584 67199", "+917358467199"),
        ("91 73584 67199", "+917358467199"),
        ("+91 73584 67199", "+917358467199"),
    ],
)
async def test_issue_policy_normalizes_india_phone(db: Session, raw_phone: str, stored_phone: str):
    provider = StubProvider(_make_status(est_delay_min=0))
    policy_id, _ = await issue_policy(
        **{**BASE_REQ, "phone": raw_phone}, db=db, provider=provider, config=CONFIG
    )

    pii = db.get(PolicyPII, policy_id)
    assert pii.phone == stored_phone


@pytest.mark.asyncio
async def test_issue_policy_rejects_invalid_phone(db: Session):
    provider = StubProvider(_make_status(est_delay_min=0))
    with pytest.raises(ValueError, match="valid Indian phone"):
        await issue_policy(
            **{**BASE_REQ, "phone": "12345"}, db=db, provider=provider, config=CONFIG
        )


@pytest.mark.asyncio
async def test_flight_state_row_written(db: Session):
    provider = StubProvider(_make_status(est_delay_min=0), alert_id="fa-alert-xyz")
    policy_id, _ = await issue_policy(**BASE_REQ, db=db, provider=provider, config=CONFIG)

    fs = db.get(FlightStateRow, policy_id)
    assert fs is not None
    assert fs.current_state == FlightState.ON_TIME.value
    assert fs.alert_id == "fa-alert-xyz"
    assert fs.provider == "stub"
    assert fs.last_event_hash == "hash-0"


@pytest.mark.asyncio
async def test_audit_log_written(db: Session):
    provider = StubProvider(_make_status(est_delay_min=0))
    policy_id, state = await issue_policy(**BASE_REQ, db=db, provider=provider, config=CONFIG)

    from sqlmodel import select
    logs = db.exec(select(EventLog).where(EventLog.policy_id == policy_id)).all()
    assert len(logs) == 1
    log = logs[0]
    assert log.source == "issuance"
    assert log.decided_event_type == EventType.MONITORING_ACTIVE.value
    assert log.new_state == state
    assert log.prev_state is None


@pytest.mark.asyncio
async def test_consent_required(db: Session):
    provider = StubProvider(_make_status(est_delay_min=0))
    with pytest.raises(ValueError, match="consent"):
        await issue_policy(**{**BASE_REQ, "consent": False}, db=db, provider=provider, config=CONFIG)


@pytest.mark.asyncio
async def test_email_optional(db: Session):
    provider = StubProvider(_make_status(est_delay_min=0))
    policy_id, _ = await issue_policy(
        **{**BASE_REQ, "email": None}, db=db, provider=provider, config=CONFIG
    )
    pii = db.get(PolicyPII, policy_id)
    assert pii.email is None
