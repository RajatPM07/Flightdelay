"""
Notifier: maps a brain EventType + message_context to a customer-facing message,
picks the right channel, sends via MessageProvider, and writes the Notification row.

Idempotency: if EventLog.notification_id is already set for this event_log_id,
the send is skipped and the existing notification_id is returned — safe on retry.

No payment language anywhere in this file. (Phase-2 seam: payout_recommendation
is always None and is never read here.)
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Session

from app.domain.states import EventType
from app.models import EventLog, Notification, Policy, PolicyPII
from app.providers.messaging.base import Channel, MessageProvider

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Templates
# ------------------------------------------------------------------

# Each entry: (template_name, body_template, email_subject_template)
# Placeholders: {flight_number}, {delay_min}, {delay_label}
_TEMPLATES: dict[EventType, tuple[str, str, str]] = {
    EventType.DELAY_TIER_1: (
        "delay_t1",
        "Your flight {flight_number} is running about {delay_min} minutes late. "
        "We're monitoring it and will update you if anything changes.",
        "Flight {flight_number} — minor delay ({delay_min} min)",
    ),
    EventType.DELAY_TIER_2: (
        "delay_t2",
        "Update: your flight {flight_number} is now delayed by {delay_min} minutes. "
        "TripSecure+ is tracking your flight closely.",
        "Flight {flight_number} — delayed {delay_min} min",
    ),
    EventType.DELAY_TIER_3: (
        "delay_t3",
        "Important update: your flight {flight_number} is significantly delayed "
        "by {delay_min} minutes. We will notify you of any further changes.",
        "Flight {flight_number} — major delay ({delay_min} min)",
    ),
    EventType.BACK_ON_SCHEDULE: (
        "back_on_schedule",
        "Good news! Your flight {flight_number} is back on schedule. Safe travels!",
        "Flight {flight_number} — back on schedule",
    ),
    EventType.DEPARTED: (
        "departed",
        "Your flight {flight_number} has departed and is expected to arrive "
        "{delay_label}. We'll notify you when it lands.",
        "Flight {flight_number} — departed ({delay_label})",
    ),
    EventType.LANDED: (
        "landed",
        "Your flight {flight_number} has landed {delay_label}. "
        "Thank you for travelling with TripSecure+ protection.",
        "Flight {flight_number} — landed",
    ),
    EventType.CANCELLED: (
        "cancelled",
        "Your flight {flight_number} has been cancelled. "
        "Please contact your airline or visit the TripSecure+ helpline for assistance.",
        "Flight {flight_number} — cancelled",
    ),
    EventType.DIVERTED: (
        "diverted",
        "Your flight {flight_number} has been diverted. "
        "Please contact your airline or the TripSecure+ helpline for the latest information.",
        "Flight {flight_number} — diverted",
    ),
}


def _delay_label(delay_min: Optional[int]) -> str:
    if delay_min is None or delay_min <= 0:
        return "on time"
    return f"{delay_min} min late"


def render_message(
    event_type: EventType, context: dict
) -> tuple[str, str, str]:
    """
    Render a notification message.

    Returns (template_name, body, email_subject).
    Raises KeyError if event_type has no template (e.g. EventType.NONE).
    """
    template_name, body_tpl, subject_tpl = _TEMPLATES[event_type]
    ctx = {
        "flight_number": context.get("flight_number", "your flight"),
        "delay_min": context.get("delay_min", 0) or 0,
        "delay_label": _delay_label(context.get("delay_min")),
    }
    return template_name, body_tpl.format(**ctx), subject_tpl.format(**ctx)


# ------------------------------------------------------------------
# Channel selection
# ------------------------------------------------------------------

def _pick_channel(pii: PolicyPII) -> tuple[Channel, str]:
    """
    WhatsApp is preferred (higher open rate). Falls back to SMS if WhatsApp
    fails at send time. Email is used when no phone is on file.
    """
    if pii.phone:
        return Channel.WHATSAPP, pii.phone
    if pii.email:
        return Channel.EMAIL, pii.email
    raise ValueError(f"No contact method available for policy {pii.policy_id}")


# ------------------------------------------------------------------
# Public entry point
# ------------------------------------------------------------------

async def send_notification(
    *,
    policy_id: str,
    event_type: EventType,
    message_context: dict,
    event_log_id: int,
    db: Session,
    msg_provider: MessageProvider,
) -> Optional[str]:
    """
    Send a customer notification and persist the Notification row.

    Returns notification_id on success, None on failure or if already sent.
    Idempotent: re-calling with the same event_log_id is a no-op if already sent.
    """
    # 1. Idempotency guard.
    log = db.get(EventLog, event_log_id)
    if log and log.notification_id:
        logger.info("notifier.already_sent", extra={"policy_id": policy_id, "notif_id": log.notification_id})
        return log.notification_id

    # 2. Fetch PII for contact details.
    pii = db.get(PolicyPII, policy_id)
    if pii is None:
        logger.error("notifier.pii_missing", extra={"policy_id": policy_id})
        return None

    # 3. Fetch Policy for flight_number (not in brain context — kept out of PII).
    policy = db.get(Policy, policy_id)
    flight_number = policy.flight_number if policy else "your flight"

    # 4. Render template.
    try:
        template_name, body, subject = render_message(
            event_type, {**message_context, "flight_number": flight_number}
        )
    except KeyError:
        logger.warning("notifier.no_template", extra={"event_type": event_type.value})
        return None

    # 5. Pick channel.
    try:
        channel, to = _pick_channel(pii)
    except ValueError:
        logger.error("notifier.no_contact", extra={"policy_id": policy_id})
        return None

    # 6. Create Notification row (pending) before send — for auditability even on failure.
    notification_id = uuid.uuid4().hex
    notification = Notification(
        id=notification_id,
        policy_id=policy_id,
        channel=channel.value,
        template=template_name,
        body=body,
        status="pending",
    )
    db.add(notification)
    db.flush()

    # 7. Send.
    try:
        provider_msg_id = await msg_provider.send(channel, to, body, subject)
        notification.status = "sent"
        notification.provider_msg_id = provider_msg_id
        notification.sent_ts = datetime.now(timezone.utc)
        logger.info(
            "notifier.sent",
            extra={"policy_id": policy_id, "channel": channel.value, "event_type": event_type.value},
        )
    except Exception:
        logger.exception("notifier.send_failed", extra={"policy_id": policy_id})
        notification.status = "failed"
        db.add(notification)
        db.commit()
        return None

    # 8. Link notification back to the audit log row.
    if log:
        log.notification_id = notification_id
        db.add(log)

    db.add(notification)
    db.commit()
    return notification_id
