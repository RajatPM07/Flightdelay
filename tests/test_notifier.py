"""
Notifier tests. All offline: in-memory SQLite + StubMessageProvider.

Covers:
  - correct template rendered per EventType (body contains key phrases)
  - Notification row written with correct fields
  - EventLog.notification_id linked after send
  - idempotency: second call with same event_log_id → no-op, same notification_id
  - channel selection: phone → WHATSAPP, no phone + email → EMAIL
  - send failure → notification.status = 'failed', returns None
  - render_message: delay_label edge cases (on-time, late)
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.domain.states import EventType
from app.models import EventLog, Notification, Policy, PolicyPII
from app.providers.messaging.base import Channel, MessageProvider
from app.services.notifier import _channel_chain, render_message, send_notification

STA = datetime(2026, 7, 1, 14, 30, tzinfo=timezone.utc)
POLICY_ID = "pol-notif-001"


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _seed(db: Session, phone: str = "+911234567890", email: str = "user@example.com"):
    db.add(PolicyPII(policy_id=POLICY_ID, name="Test User", phone=phone, email=email))
    db.add(Policy(
        policy_id=POLICY_ID, pnr="ABC123", flight_number="AI101",
        flight_date="2026-07-01", scheduled_in_utc=STA,
        scheduled_in_tz_offset="+00:00", consent_ts=STA,
    ))
    db.commit()


def _seed_log(db: Session, event_type: EventType = EventType.DELAY_TIER_2) -> int:
    log = EventLog(
        policy_id=POLICY_ID,
        source="test",
        raw_payload={},
        prev_state="ON_TIME",
        new_state="DELAYED_T2",
        decided_event_type=event_type.value,
        notified=True,
    )
    db.add(log)
    db.flush()
    log_id = log.id
    db.commit()
    return log_id


class StubMessageProvider(MessageProvider):
    def __init__(self, should_fail: bool = False, fail_channels: Optional[set[Channel]] = None):
        self.calls: list[dict] = []
        self.should_fail = should_fail
        self.fail_channels = fail_channels or set()

    async def send(self, channel: Channel, to: str, body: str, subject: str | None = None) -> str:
        if self.should_fail or channel in self.fail_channels:
            raise RuntimeError(f"send failed on {channel.value}")
        msg_id = f"stub-{len(self.calls)}"
        self.calls.append({"channel": channel, "to": to, "body": body, "subject": subject})
        return msg_id


# ------------------------------------------------------------------
# render_message tests (pure, no DB)
# ------------------------------------------------------------------

def test_render_delay_t2_contains_flight_and_delay():
    _, body, subject = render_message(
        EventType.DELAY_TIER_2, {"flight_number": "AI101", "delay_min": 70}
    )
    assert "AI101" in body
    assert "70" in body
    assert "AI101" in subject


def test_render_departed_delay_label():
    _, body, _ = render_message(
        EventType.DEPARTED, {"flight_number": "6E202", "delay_min": 45}
    )
    assert "45 min late" in body


def test_render_landed_on_time():
    _, body, _ = render_message(
        EventType.LANDED, {"flight_number": "SG100", "delay_min": 0}
    )
    assert "on time" in body


def test_render_landed_late():
    _, body, _ = render_message(
        EventType.LANDED, {"flight_number": "SG100", "delay_min": 138}
    )
    assert "138 min late" in body


def test_render_cancelled_no_payment_language():
    _, body, _ = render_message(EventType.CANCELLED, {"flight_number": "AI101"})
    for bad in ("payout", "claim", "compensation", "refund", "money", "₹", "INR"):
        assert bad.lower() not in body.lower(), f"Payment language found: {bad!r}"


def test_render_unknown_event_raises():
    with pytest.raises(KeyError):
        render_message(EventType.NONE, {})


# ------------------------------------------------------------------
# _channel_chain tests
# ------------------------------------------------------------------

def test_channel_chain_phone_prefers_whatsapp_then_sms():
    pii = PolicyPII(policy_id="x", name="N", phone="+911234567890")
    chain = _channel_chain(pii)
    assert chain == [
        (Channel.WHATSAPP, "+911234567890"),
        (Channel.SMS, "+911234567890"),
    ]


def test_channel_chain_no_phone_gives_email_only():
    pii = PolicyPII(policy_id="x", name="N", phone="", email="a@b.com")
    chain = _channel_chain(pii)
    assert chain == [(Channel.EMAIL, "a@b.com")]


def test_channel_chain_no_contact_raises():
    pii = PolicyPII(policy_id="x", name="N", phone="", email=None)
    with pytest.raises(ValueError):
        _channel_chain(pii)


# ------------------------------------------------------------------
# send_notification integration tests
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_notification_writes_row(db: Session):
    _seed(db)
    log_id = _seed_log(db)
    provider = StubMessageProvider()

    notif_id = await send_notification(
        policy_id=POLICY_ID,
        event_type=EventType.DELAY_TIER_2,
        message_context={"delay_min": 70},
        event_log_id=log_id,
        db=db,
        msg_provider=provider,
    )

    assert notif_id is not None
    notif = db.get(Notification, notif_id)
    assert notif.status == "sent"
    assert notif.template == "delay_t2"
    assert notif.channel == Channel.WHATSAPP.value
    assert "AI101" in notif.body


@pytest.mark.asyncio
async def test_send_notification_links_event_log(db: Session):
    _seed(db)
    log_id = _seed_log(db)
    provider = StubMessageProvider()

    notif_id = await send_notification(
        policy_id=POLICY_ID,
        event_type=EventType.DELAY_TIER_2,
        message_context={"delay_min": 70},
        event_log_id=log_id,
        db=db,
        msg_provider=provider,
    )

    log = db.get(EventLog, log_id)
    assert log.notification_id == notif_id


@pytest.mark.asyncio
async def test_send_notification_idempotent(db: Session):
    _seed(db)
    log_id = _seed_log(db)
    provider = StubMessageProvider()

    id1 = await send_notification(
        policy_id=POLICY_ID, event_type=EventType.DELAY_TIER_2,
        message_context={"delay_min": 70}, event_log_id=log_id,
        db=db, msg_provider=provider,
    )
    id2 = await send_notification(
        policy_id=POLICY_ID, event_type=EventType.DELAY_TIER_2,
        message_context={"delay_min": 70}, event_log_id=log_id,
        db=db, msg_provider=provider,
    )

    assert id1 == id2
    assert len(provider.calls) == 1  # second call was no-op


@pytest.mark.asyncio
async def test_send_notification_email_channel(db: Session):
    _seed(db, phone="", email="traveller@example.com")
    log_id = _seed_log(db)
    provider = StubMessageProvider()

    notif_id = await send_notification(
        policy_id=POLICY_ID, event_type=EventType.LANDED,
        message_context={"delay_min": 30}, event_log_id=log_id,
        db=db, msg_provider=provider,
    )

    notif = db.get(Notification, notif_id)
    assert notif.channel == Channel.EMAIL.value
    assert provider.calls[0]["to"] == "traveller@example.com"


@pytest.mark.asyncio
async def test_send_notification_falls_back_to_sms(db: Session):
    """WhatsApp send fails -> notifier retries on SMS and records the SMS send."""
    _seed(db)
    log_id = _seed_log(db)
    provider = StubMessageProvider(fail_channels={Channel.WHATSAPP})

    notif_id = await send_notification(
        policy_id=POLICY_ID, event_type=EventType.DELAY_TIER_2,
        message_context={"delay_min": 70}, event_log_id=log_id,
        db=db, msg_provider=provider,
    )

    assert notif_id is not None
    notif = db.get(Notification, notif_id)
    assert notif.status == "sent"
    assert notif.channel == Channel.SMS.value
    # WhatsApp raised (not recorded); the successful SMS attempt is the only call.
    assert [c["channel"] for c in provider.calls] == [Channel.SMS]
    # The EventLog is linked only after the successful fallback send.
    assert db.get(EventLog, log_id).notification_id == notif_id


@pytest.mark.asyncio
async def test_send_notification_all_channels_fail(db: Session):
    _seed(db)
    log_id = _seed_log(db)
    provider = StubMessageProvider(should_fail=True)

    result = await send_notification(
        policy_id=POLICY_ID, event_type=EventType.DELAY_TIER_2,
        message_context={"delay_min": 70}, event_log_id=log_id,
        db=db, msg_provider=provider,
    )

    assert result is None
    notifs = db.exec(select(Notification).where(Notification.policy_id == POLICY_ID)).all()
    assert len(notifs) == 1
    assert notifs[0].status == "failed"
    # On total failure the row reflects the last channel tried (SMS).
    assert notifs[0].channel == Channel.SMS.value


@pytest.mark.asyncio
async def test_send_notification_no_pii(db: Session):
    """If PII row is missing, return None gracefully."""
    # Don't seed PII
    log_id = _seed_log(db)
    provider = StubMessageProvider()

    result = await send_notification(
        policy_id=POLICY_ID, event_type=EventType.DELAY_TIER_2,
        message_context={"delay_min": 70}, event_log_id=log_id,
        db=db, msg_provider=provider,
    )
    assert result is None
    assert len(provider.calls) == 0
