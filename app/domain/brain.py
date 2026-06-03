"""
The Brain — pure, I/O-free decision engine. THE ASSET.

MUST NOT import any vendor SDK, database, or httpx. It is given the current state and a
normalised status snapshot, and returns a Decision. Everything is unit-testable offline.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from app.domain.materiality import crossed_upward, recovered_to_on_time, tier_for_delay
from app.domain.states import BrainConfig, EventType, FlightState, TERMINAL_STATES

# Re-export BrainConfig so existing `from app.domain.brain import BrainConfig` imports keep working.
__all__ = ["BrainConfig", "FlightStatus", "Decision", "decide"]


@dataclass(frozen=True)
class FlightStatus:
    """Normalised, vendor-agnostic snapshot. Adapters map provider payloads into this."""
    event_ts: datetime              # when the provider observed this update (UTC)
    scheduled_in_utc: datetime      # STA (gate), UTC
    estimated_in_utc: Optional[datetime] = None
    actual_in_utc: Optional[datetime] = None   # chocks-on gate arrival (NOT runway touchdown)
    departed: bool = False
    cancelled: bool = False
    diverted: bool = False
    content_hash: str = ""          # hash of meaningful fields, for dedup


@dataclass(frozen=True)
class Decision:
    new_state: FlightState
    should_notify: bool
    event_type: EventType
    message_context: dict[str, Any] = field(default_factory=dict)
    # Phase-2 seam ONLY. Always None in the MVP. Build nothing behind this.
    payout_recommendation: None = None


_TIER_EVENT = {
    FlightState.DELAYED_T1: EventType.DELAY_TIER_1,
    FlightState.DELAYED_T2: EventType.DELAY_TIER_2,
    FlightState.DELAYED_T3: EventType.DELAY_TIER_3,
}


def _silent(state: FlightState) -> Decision:
    return Decision(state, False, EventType.NONE)


def _delay_minutes(status: FlightStatus) -> Optional[int]:
    ref = status.estimated_in_utc or status.actual_in_utc
    if ref is None:
        return None
    return int((ref - status.scheduled_in_utc).total_seconds() / 60)


def decide(
    current_state: FlightState,
    last_known: Optional[FlightStatus],
    incoming: FlightStatus,
    config: BrainConfig,
) -> Decision:
    """
    Decide the next state and whether to notify, given an incoming status update.

    Rules:
    - Terminal state: ignore all further events.
    - Dedup: drop if event_ts <= last_known.event_ts (out-of-order), or content_hash matches.
    - Priority: landed > cancelled > diverted > first-departure > delay tiers > silent.
    - Tier escalation notifies; same-tier wobble is silent. Recovery to ON_TIME only when
      delay drops below (T1 - recovery_buffer); no intermediate tier-step notifications.
    """
    if current_state in TERMINAL_STATES:
        return _silent(current_state)

    if last_known is not None:
        if incoming.event_ts <= last_known.event_ts:
            return _silent(current_state)
        if incoming.content_hash and incoming.content_hash == last_known.content_hash:
            return _silent(current_state)

    # Landed: actual gate-arrival time is set (chocks-on, not runway touchdown).
    if incoming.actual_in_utc is not None:
        return Decision(
            FlightState.LANDED, True, EventType.LANDED,
            {"delay_min": _delay_minutes(incoming)},
        )

    if incoming.cancelled:
        return Decision(FlightState.CANCELLED, True, EventType.CANCELLED)

    if incoming.diverted:
        return Decision(FlightState.DIVERTED, True, EventType.DIVERTED)

    # First departure event: notify with updated ETA context.
    if incoming.departed and current_state != FlightState.DEPARTED:
        return Decision(
            FlightState.DEPARTED, True, EventType.DEPARTED,
            {"delay_min": _delay_minutes(incoming)},
        )

    # Once departed, only a landed event (above) matters.
    if current_state == FlightState.DEPARTED:
        return _silent(current_state)

    # Delay tier logic (flight not yet departed).
    delay = _delay_minutes(incoming)
    if delay is None:
        return _silent(current_state)

    new_tier = tier_for_delay(delay, config)

    if crossed_upward(current_state, new_tier):
        return Decision(new_tier, True, _TIER_EVENT[new_tier], {"delay_min": delay})

    if recovered_to_on_time(delay, current_state, config):
        return Decision(FlightState.ON_TIME, True, EventType.BACK_ON_SCHEDULE, {"delay_min": delay})

    return _silent(current_state)
