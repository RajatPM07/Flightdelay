from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine
import app.models
from app.models import Policy, PolicyPII, FlightStateRow, EventLog, Notification
from app.main import app
from app.deps import get_db

STA = datetime(2026, 6, 12, 14, 30, tzinfo=timezone.utc)


@pytest.fixture()
def client():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
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
    assert "email" not in d
    assert "phone" not in d
    assert "9876543299" not in r.text
    assert len(d["timeline"]) == 1 and d["timeline"][0]["new_state"] == "DELAYED_T2"
    assert d["timeline"][0]["notified"] is True
    assert len(d["notifications"]) == 1 and d["notifications"][0]["channel"] == "whatsapp"


def test_status_404_for_unknown(client):
    assert client.get("/policies/NOPE").status_code == 404


from app.api.policies import _mask_name, _mask_phone

def test_mask_helpers_edge_cases():
    assert _mask_phone("+919876543299") == "+91 98••••99"
    assert _mask_phone("") == ""
    assert _mask_phone("abc") == ""
    assert _mask_name("Madonna") == "Madonna"
    assert _mask_name("") == ""
    assert _mask_name("Rajat Kumar Sharma") == "Rajat S."
