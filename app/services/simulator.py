"""Demo simulator: turn a button press into a synthetic FlightStatus and run it
through the REAL webhook pipeline (_process_for_policy), so the demo exercises the
same brain/persistence/notify path as production. No external calls."""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from sqlmodel import Session

from app.domain.brain import FlightStatus
from app.domain.states import BrainConfig
from app.models import FlightStateRow, Policy
from app.services.webhook import _process_for_policy

VALID_EVENTS = ("delay_t1", "delay_t2", "delay_t3", "recover", "depart", "land", "cancel", "divert")


def _build_status(event: str, scheduled: datetime, config: BrainConfig, now: datetime) -> FlightStatus:
    est = scheduled
    actual = None
    departed = cancelled = diverted = False
    t1, t2, t3 = config.delay_t1_min, config.delay_t2_min, config.delay_t3_min
    buf = config.recovery_buffer_min
    if event == "delay_t1":
        est = scheduled + timedelta(minutes=(t1 + t2) // 2)              # safely between T1 and T2
    elif event == "delay_t2":
        est = scheduled + timedelta(minutes=(t2 + t3) // 2)              # safely between T2 and T3 (=90 on defaults)
    elif event == "delay_t3":
        est = scheduled + timedelta(minutes=t3 + max(1, (t3 - t2) // 2)) # safely above T3
    elif event == "recover":
        est = scheduled + timedelta(minutes=max(0, (t1 - buf) // 2))     # below the recovery threshold -> ON_TIME
    elif event == "depart":
        departed = True
        est = scheduled + timedelta(minutes=(t2 + t3) // 2)
    elif event == "land":
        departed = True
        actual = scheduled + timedelta(minutes=(t2 + t3) // 2)           # any delay; LANDED triggers on actual_in
    elif event == "cancel":
        cancelled = True
    elif event == "divert":
        diverted = True                                                  # NOTE: DIVERTED is intentionally NOT terminal
    else:
        raise ValueError(f"unknown event: {event!r}")
    content_hash = hashlib.sha256(f"{event}-{now.isoformat()}".encode()).hexdigest()
    return FlightStatus(event_ts=now, scheduled_in_utc=scheduled, estimated_in_utc=est,
                        actual_in_utc=actual, departed=departed, cancelled=cancelled,
                        diverted=diverted, content_hash=content_hash)


def _display_payload(event: str, s: FlightStatus) -> dict:
    """Human-readable synthetic provider payload for the raw-webhook console."""
    return {
        "event": event,
        "received_at": s.event_ts.isoformat(),
        "flight": {
            "scheduled_in": s.scheduled_in_utc.isoformat(),
            "estimated_in": s.estimated_in_utc.isoformat() if s.estimated_in_utc else None,
            "actual_in": s.actual_in_utc.isoformat() if s.actual_in_utc else None,
            "departed": s.departed, "cancelled": s.cancelled, "diverted": s.diverted,
        },
    }


async def simulate_event(*, policy_id: str, event: str, db: Session,
                         provider, config: BrainConfig, msg_provider=None) -> dict:
    if event not in VALID_EVENTS:
        raise ValueError(f"unknown event: {event!r}. valid: {VALID_EVENTS}")
    policy = db.get(Policy, policy_id)
    state_row = db.get(FlightStateRow, policy_id)
    if policy is None or state_row is None:
        raise ValueError(f"policy not found: {policy_id}")
    # Ensure the synthetic event_ts is strictly after whatever is stored as last_known,
    # regardless of wall-clock time (flight dates may be in the future during demos).
    wall_now = datetime.now(timezone.utc)
    raw = state_row.last_known_status.get("event_ts") if state_row.last_known_status else None
    if isinstance(raw, datetime):
        last_known_ts = raw
    elif isinstance(raw, str):
        last_known_ts = datetime.fromisoformat(raw)
    else:
        last_known_ts = None
    if last_known_ts is not None and last_known_ts.tzinfo is None:
        last_known_ts = last_known_ts.replace(tzinfo=timezone.utc)
    now = max(wall_now, last_known_ts + timedelta(seconds=1)) if last_known_ts is not None else wall_now
    incoming = _build_status(event, policy.scheduled_in_utc, config, now)
    payload = _display_payload(event, incoming)
    await _process_for_policy(state_row=state_row, incoming=incoming, raw_payload=payload,
                              db=db, provider=provider, config=config, msg_provider=msg_provider)
    fresh = db.get(FlightStateRow, policy_id)
    return {"current_state": fresh.current_state, "payload": payload}
