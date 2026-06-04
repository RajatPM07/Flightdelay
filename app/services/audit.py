"""Audit: append-only writes to event_log. Never update or delete rows."""
from __future__ import annotations

from typing import Optional

from sqlmodel import Session

from app.models import EventLog


def append_event_log(
    *,
    db: Session,
    policy_id: str,
    source: str,
    raw_payload: dict,
    prev_state: Optional[str],
    new_state: str,
    decided_event_type: str,
    notified: bool,
    notification_id: Optional[str] = None,
) -> EventLog:
    """
    Append one immutable row to event_log. Does NOT commit — caller owns the transaction
    so the audit write is atomic with any state-row update.
    """
    log = EventLog(
        policy_id=policy_id,
        source=source,
        raw_payload=raw_payload,
        prev_state=prev_state,
        new_state=new_state,
        decided_event_type=decided_event_type,
        notified=notified,
        notification_id=notification_id,
    )
    db.add(log)
    return log
