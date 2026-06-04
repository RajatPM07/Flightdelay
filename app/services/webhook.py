"""
Webhook service: normalise → load state → brain.decide() → audit → persist → notify.

Idempotency contract:
  - The brain's dedup guard (event_ts / content_hash) is the primary idempotency mechanism.
  - Dedup events: audit row written, last_known_status NOT advanced (keeps the brain's
    reference point stable for the next real event).
  - Genuine silent events (new timestamp, new hash, but not material): audit written,
    last_known_status advanced so the brain has the freshest snapshot next time.
  - Notification idempotency: notifier checks EventLog.notification_id before sending.

Error handling:
  - Any exception is caught at the route level and logged; the route always returns 200
    so AeroAPI does not enter a retry storm.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from sqlmodel import Session, select

from app.domain.brain import FlightStatus, decide
from app.domain.states import BrainConfig, EventType, FlightState, TERMINAL_STATES
from app.models import EventLog, FlightStateRow, Policy
from app.providers.flightdata.base import FlightDataProvider
from app.providers.messaging.base import MessageProvider
from app.services.audit import append_event_log
from app.services.notifier import send_notification
from app.services.scheduler import cancel_backstop, deregister_on_terminal

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Public entry point
# ------------------------------------------------------------------

async def process_webhook(
    *,
    raw_payload: dict,
    raw_body: bytes,
    signature_header: str,
    db: Session,
    provider: FlightDataProvider,
    config: BrainConfig,
    msg_provider: Optional[MessageProvider] = None,
) -> None:
    """
    Full webhook pipeline. Raises nothing — callers should catch all exceptions and
    still return HTTP 200 to the provider.
    """
    # 1. Signature verification.
    if not provider.verify_signature(raw_body, signature_header):
        logger.warning("webhook.signature_invalid", extra={"sig": signature_header[:16]})
        return

    # 2. Extract alert_id from payload (used to look up which policy this is for).
    alert_id = str(raw_payload.get("alert_id", "")).strip()
    if not alert_id:
        logger.warning("webhook.missing_alert_id", extra={"keys": list(raw_payload.keys())})
        return

    # 3. Load state row.
    state_row: Optional[FlightStateRow] = db.exec(
        select(FlightStateRow).where(FlightStateRow.alert_id == alert_id)
    ).first()
    if state_row is None:
        logger.info("webhook.unknown_alert", extra={"alert_id": alert_id})
        return

    policy_id = state_row.policy_id
    policy = db.get(Policy, policy_id)
    flight_number_for_policy = policy.flight_number if policy else ""
    current_state = FlightState(state_row.current_state)

    # 4. Terminal-state shortcut — nothing left to decide; ack and exit.
    if current_state in TERMINAL_STATES:
        logger.info(
            "webhook.terminal_noop",
            extra={"policy_id": policy_id, "state": current_state.value},
        )
        return

    # 5. Normalise raw payload into vendor-agnostic FlightStatus.
    incoming: FlightStatus = provider.normalise(raw_payload)

    # 6. Reconstruct last_known from persisted snapshot.
    last_known: Optional[FlightStatus] = (
        _dict_to_status(state_row.last_known_status)
        if state_row.last_known_status
        else None
    )

    # 7. Classify whether this is a duplicate before calling the brain (needed for
    #    deciding whether to advance last_known_status after a silent decision).
    is_dedup = _is_dedup(incoming, last_known)

    # 8. Brain decision.
    decision = decide(current_state, last_known, incoming, config)

    logger.info(
        "webhook.decision",
        extra={
            "policy_id": policy_id,
            "prev": current_state.value,
            "next": decision.new_state.value,
            "event_type": decision.event_type.value,
            "notify": decision.should_notify,
            "dedup": is_dedup,
        },
    )

    # 9. Audit log — always written, even for silent/dedup events.
    log = append_event_log(
        db=db,
        policy_id=policy_id,
        source="flightaware_webhook",
        raw_payload=raw_payload,
        prev_state=current_state.value,
        new_state=decision.new_state.value,
        decided_event_type=decision.event_type.value,
        notified=decision.should_notify,
    )

    # 10. Persist state update.
    #     - State changed → always update snapshot.
    #     - Silent but NOT a dedup → advance snapshot so brain has a fresh reference.
    #     - True dedup → leave snapshot untouched (keep the brain's reference stable).
    if decision.new_state != current_state or not is_dedup:
        state_row.current_state = decision.new_state.value
        state_row.last_update_ts = incoming.event_ts
        state_row.last_event_hash = incoming.content_hash
        state_row.last_known_status = _status_to_dict(incoming)
        db.add(state_row)

    # Capture alert_id before commit (object expires after commit).
    alert_id = state_row.alert_id
    entering_terminal = (
        decision.new_state in TERMINAL_STATES and current_state not in TERMINAL_STATES
    )

    # Flush to assign log.id before commit; the notifier needs it for idempotency.
    db.flush()
    log_id: int = log.id
    db.commit()

    # 11. Deregister alert + cancel backstop on terminal transition.
    if entering_terminal:
        if provider.subscription_scope == "per_policy":
            if alert_id:
                await deregister_on_terminal(alert_id, provider)
        else:
            from app.services.subscriptions import release_subscription
            await release_subscription(db, provider, flight_number_for_policy, policy_id)
        cancel_backstop(policy_id)

    # 12. Notify via MessageProvider (idempotent: notifier checks EventLog.notification_id).
    if decision.should_notify and msg_provider is not None:
        await send_notification(
            policy_id=policy_id,
            event_type=decision.event_type,
            message_context=decision.message_context,
            event_log_id=log_id,
            db=db,
            msg_provider=msg_provider,
        )


# ------------------------------------------------------------------
# Private helpers
# ------------------------------------------------------------------

def _is_dedup(incoming: FlightStatus, last_known: Optional[FlightStatus]) -> bool:
    """Mirror the brain's dedup guard so we can decide whether to advance last_known_status."""
    if last_known is None:
        return False
    if incoming.event_ts <= last_known.event_ts:
        return True
    if incoming.content_hash and incoming.content_hash == last_known.content_hash:
        return True
    return False


def _status_to_dict(status: FlightStatus) -> dict:
    return {
        "event_ts": status.event_ts.isoformat(),
        "scheduled_in_utc": status.scheduled_in_utc.isoformat(),
        "estimated_in_utc": status.estimated_in_utc.isoformat() if status.estimated_in_utc else None,
        "actual_in_utc": status.actual_in_utc.isoformat() if status.actual_in_utc else None,
        "departed": status.departed,
        "cancelled": status.cancelled,
        "diverted": status.diverted,
        "content_hash": status.content_hash,
    }


def _dict_to_status(d: dict) -> FlightStatus:
    return FlightStatus(
        event_ts=datetime.fromisoformat(d["event_ts"]),
        scheduled_in_utc=datetime.fromisoformat(d["scheduled_in_utc"]),
        estimated_in_utc=datetime.fromisoformat(d["estimated_in_utc"]) if d.get("estimated_in_utc") else None,
        actual_in_utc=datetime.fromisoformat(d["actual_in_utc"]) if d.get("actual_in_utc") else None,
        departed=d.get("departed", False),
        cancelled=d.get("cancelled", False),
        diverted=d.get("diverted", False),
        content_hash=d.get("content_hash", ""),
    )
