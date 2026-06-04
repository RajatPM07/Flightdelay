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
async def test_unknown_event_rejected(db):
    _seed(db)
    with pytest.raises(ValueError):
        await simulate_event(policy_id="P1", event="explode", db=db,
                             provider=StubProvider(), config=CFG, msg_provider=None)
    assert "delay_t2" in VALID_EVENTS


@pytest.mark.asyncio
@pytest.mark.parametrize("event,expected_state", [
    ("delay_t1", "DELAYED_T1"),
    ("delay_t2", "DELAYED_T2"),
    ("delay_t3", "DELAYED_T3"),
    ("recover",  "ON_TIME"),
    ("depart",   "DEPARTED"),
    ("land",     "LANDED"),
    ("cancel",   "CANCELLED"),
    ("divert",   "DIVERTED"),
])
async def test_all_events_from_on_time(db, event, expected_state):
    _seed(db)
    out = await simulate_event(policy_id="P1", event=event, db=db,
                               provider=StubProvider(), config=CFG, msg_provider=None)
    assert out["current_state"] == expected_state


@pytest.mark.asyncio
async def test_delay_t2_respects_narrow_tier_config(db):
    _seed(db)
    narrow = BrainConfig(delay_t1_min=30, delay_t2_min=60, delay_t3_min=80, recovery_buffer_min=15)
    out = await simulate_event(policy_id="P1", event="delay_t2", db=db,
                               provider=StubProvider(), config=narrow, msg_provider=None)
    assert out["current_state"] == "DELAYED_T2"   # (60+80)//2 = 70, between T2 and T3
