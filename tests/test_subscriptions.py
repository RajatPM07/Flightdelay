# tests/test_subscriptions.py — in-memory SQLite, fake provider records calls.
import pytest
from datetime import datetime, timezone
from sqlmodel import Session, SQLModel, create_engine
import app.models  # registers tables
from app.models import Policy, FlightStateRow, FlightSubscription
from app.services import subscriptions as S


class FakeProvider:
    name = "aerodatabox"
    subscription_scope = "per_flight_number"
    def __init__(self):
        self.registered = []
        self.deregistered = []
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
                  scheduled_in_utc=datetime(2026, 6, 12, 6, 0, tzinfo=timezone.utc),
                  scheduled_in_tz_offset="+00:00",
                  consent_ts=datetime.now(timezone.utc),
                  status=state))
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


@pytest.mark.asyncio
async def test_release_without_subscription_row_is_noop(db):
    prov = FakeProvider()
    _seed_policy(db, "A")
    db.get(FlightStateRow, "A").current_state = "LANDED"
    db.commit()
    await S.release_subscription(db, prov, "6E1341", "A")  # no FlightSubscription row exists
    assert prov.deregistered == []


@pytest.mark.asyncio
async def test_normalisation_handles_spaced_flight_numbers(db):
    prov = FakeProvider()
    _seed_policy(db, "A", number="6E 1341")  # spaced
    sub = await S.ensure_subscribed(db, prov, "6E1341", "2026-06-12", "A")  # compact
    db.get(FlightStateRow, "A").current_state = "LANDED"
    db.commit()
    await S.release_subscription(db, prov, "6E1341", "A")
    assert prov.deregistered == [sub]  # spaced policy still counted, teardown happens
