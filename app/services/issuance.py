"""
Issuance service: orchestrates create-policy flow.

Sequence:
  1. Validate consent (DPDP — must be affirmative, not pre-ticked).
  2. Fetch baseline snapshot from the flight data provider.
  3. Derive initial flight state from that snapshot.
  4. Register the push-alert subscription; capture alert_id.
  5. Persist PolicyPII (isolated), Policy, FlightStateRow, and an audit EventLog row.

No brain logic here — state derivation is a one-liner via materiality helpers.
No PII leaves this function after the split; only policy_id propagates.
"""
from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Optional

from sqlmodel import Session

from app.domain.brain import FlightStatus
from app.domain.materiality import tier_for_delay
from datetime import timedelta

from app.config import settings
from app.domain.states import BrainConfig, EventType, FlightState
from app.models import EventLog, FlightStateRow, Policy, PolicyPII
from app.providers.flightdata.base import FlightDataProvider
from app.services.scheduler import schedule_backstop


# ------------------------------------------------------------------
# Public entry point
# ------------------------------------------------------------------

async def issue_policy(
    *,
    pnr: str,
    flight_number: str,
    flight_date: str,
    name: str,
    phone: str,
    email: Optional[str],
    consent: bool,
    db: Session,
    provider: FlightDataProvider,
    config: BrainConfig,
) -> tuple[str, str]:
    """
    Create a policy end-to-end.

    Returns (policy_id, baseline_state_value).
    Raises ValueError for invalid inputs (consent missing, no flight found).
    """
    if not consent:
        raise ValueError("Explicit consent is required to issue a policy (DPDP).")

    policy_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc)

    # --- 1. Baseline snapshot ------------------------------------------------
    baseline: FlightStatus = await provider.get_baseline(flight_number, flight_date)

    # --- 2. Initial state ----------------------------------------------------
    initial_state = _state_from_baseline(baseline, config)

    # --- 3. Alert registration -----------------------------------------------
    from app.services.subscriptions import ensure_subscribed
    alert_id = await ensure_subscribed(db, provider, flight_number, flight_date, policy_id)

    # --- 4. Persist (PII isolation is the invariant) -------------------------
    db.add(PolicyPII(policy_id=policy_id, name=name, phone=phone, email=email))

    db.add(Policy(
        policy_id=policy_id,
        pnr=pnr,
        flight_number=flight_number,
        flight_date=flight_date,
        scheduled_in_utc=baseline.scheduled_in_utc,
        # AeroAPI returns UTC; local offset is unavailable without resolving the
        # destination IATA timezone — stored as UTC for MVP, Phase 2 to refine.
        scheduled_in_tz_offset="+00:00",
        consent_ts=now,
        status=initial_state.value,
    ))

    db.add(FlightStateRow(
        policy_id=policy_id,
        current_state=initial_state.value,
        last_known_status=_status_to_dict(baseline),
        last_update_ts=baseline.event_ts,
        last_event_hash=baseline.content_hash,
        provider=provider.name,
        alert_id=alert_id,
    ))

    # Immutable audit entry for the issuance event.
    db.add(EventLog(
        policy_id=policy_id,
        source="issuance",
        raw_payload={},
        prev_state=None,
        new_state=initial_state.value,
        decided_event_type=EventType.MONITORING_ACTIVE.value,
        notified=False,
    ))

    db.commit()

    # Schedule the STA+24h backstop. Safe when scheduler is not running (e.g. tests).
    schedule_backstop(
        policy_id=policy_id,
        fire_at=baseline.scheduled_in_utc + timedelta(hours=settings.backstop_hours),
    )

    return policy_id, initial_state.value


# ------------------------------------------------------------------
# Private helpers
# ------------------------------------------------------------------

def _state_from_baseline(baseline: FlightStatus, config: BrainConfig) -> FlightState:
    """Derive the starting FlightState directly from a baseline snapshot."""
    if baseline.cancelled:
        return FlightState.CANCELLED
    if baseline.diverted:
        return FlightState.DIVERTED
    if baseline.actual_in_utc is not None:
        return FlightState.LANDED
    if baseline.departed:
        return FlightState.DEPARTED

    ref = baseline.estimated_in_utc or baseline.actual_in_utc
    if ref is None:
        return FlightState.ON_TIME

    delay = int((ref - baseline.scheduled_in_utc).total_seconds() / 60)
    return tier_for_delay(delay, config)


def _status_to_dict(status: FlightStatus) -> dict[str, Any]:
    """Serialize FlightStatus to a JSON-safe dict for storage in last_known_status."""
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
