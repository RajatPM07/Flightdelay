import pytest
from sqlmodel import Session, SQLModel, create_engine
import app.models
from app.models import Policy, PolicyPII, FlightStateRow
from app.domain.states import BrainConfig
from app.services.webhook import process_aerodatabox_webhook
from datetime import datetime, timezone


class FanoutProvider:
    name = "aerodatabox"
    subscription_scope = "per_flight_number"
    def verify_signature(self, b, s): return True
    def extract_subject(self, payload): return ("6E1341", "2026-06-12")
    def normalise(self, payload):
        from app.domain.brain import FlightStatus
        sta = datetime(2026, 6, 12, 14, 30, tzinfo=timezone.utc)
        return FlightStatus(event_ts=datetime(2026, 6, 12, 9, 0, tzinfo=timezone.utc),
            scheduled_in_utc=sta, estimated_in_utc=datetime(2026, 6, 12, 16, 0, tzinfo=timezone.utc),
            actual_in_utc=None, departed=False, cancelled=False, diverted=False, content_hash="h1")


@pytest.fixture()
def db():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        yield s


def _seed(db, pid, number="6E1341", date="2026-06-12"):
    sta = datetime(2026, 6, 12, 14, 30, tzinfo=timezone.utc)
    db.add(PolicyPII(policy_id=pid, name="N", phone="+910000000000"))
    db.add(Policy(policy_id=pid, pnr="P", flight_number=number, flight_date=date,
                  scheduled_in_utc=sta, scheduled_in_tz_offset="+00:00", consent_ts=sta, status="ON_TIME"))
    db.add(FlightStateRow(policy_id=pid, current_state="ON_TIME", last_known_status={},
                          last_update_ts=sta, last_event_hash="", provider="aerodatabox", alert_id="sub-6E1341"))
    db.commit()


@pytest.mark.asyncio
async def test_fanout_advances_all_matching_policies(db):
    _seed(db, "A")
    _seed(db, "B")
    await process_aerodatabox_webhook(raw_payload={}, raw_body=b"{}", signature_header="",
        db=db, provider=FanoutProvider(), config=BrainConfig(delay_t1_min=30, delay_t2_min=60, delay_t3_min=120, recovery_buffer_min=15), msg_provider=None)
    assert db.get(FlightStateRow, "A").current_state == "DELAYED_T2"
    assert db.get(FlightStateRow, "B").current_state == "DELAYED_T2"


@pytest.mark.asyncio
async def test_fanout_matches_spaced_flight_number(db):
    _seed(db, "C", number="6E 1341")  # stored with a space; subject normalises to 6E1341
    await process_aerodatabox_webhook(raw_payload={}, raw_body=b"{}", signature_header="",
        db=db, provider=FanoutProvider(), config=BrainConfig(delay_t1_min=30, delay_t2_min=60, delay_t3_min=120, recovery_buffer_min=15), msg_provider=None)
    assert db.get(FlightStateRow, "C").current_state == "DELAYED_T2"


@pytest.mark.asyncio
async def test_fanout_no_active_policy_is_noop(db):
    _seed(db, "D", date="2026-06-13")  # different date — should not match 2026-06-12
    await process_aerodatabox_webhook(raw_payload={}, raw_body=b"{}", signature_header="",
        db=db, provider=FanoutProvider(), config=BrainConfig(delay_t1_min=30, delay_t2_min=60, delay_t3_min=120, recovery_buffer_min=15), msg_provider=None)
    assert db.get(FlightStateRow, "D").current_state == "ON_TIME"  # unchanged


@pytest.mark.asyncio
async def test_fanout_skips_terminal_policy(db):
    _seed(db, "A")  # ON_TIME
    _seed(db, "B")
    db.get(FlightStateRow, "B").current_state = "LANDED"
    db.commit()
    await process_aerodatabox_webhook(raw_payload={}, raw_body=b"{}", signature_header="",
        db=db, provider=FanoutProvider(), config=BrainConfig(delay_t1_min=30, delay_t2_min=60, delay_t3_min=120, recovery_buffer_min=15), msg_provider=None)
    assert db.get(FlightStateRow, "A").current_state == "DELAYED_T2"
    assert db.get(FlightStateRow, "B").current_state == "LANDED"  # untouched


@pytest.mark.asyncio
async def test_fanout_invokes_notifier_per_matching_policy(db):
    _seed(db, "A")
    _seed(db, "B")
    sent = []
    class MsgProv:
        async def send(self, channel, to, body, subject=None):
            sent.append(to); return "mid"
    await process_aerodatabox_webhook(raw_payload={}, raw_body=b"{}", signature_header="",
        db=db, provider=FanoutProvider(), config=BrainConfig(delay_t1_min=30, delay_t2_min=60, delay_t3_min=120, recovery_buffer_min=15), msg_provider=MsgProv())
    assert len(sent) == 2  # both policies crossed T2 -> notified
