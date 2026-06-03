"""
The Brain — pure, I/O-free decision engine. THE ASSET.

MUST NOT import any vendor SDK, database, or httpx. It is given the current state and a
normalised status snapshot, and returns a Decision. Everything is unit-testable offline.

Implement `decide()` so that tests/test_brain.py (the canonical 7-event sequence) passes
BEFORE any external integration is built.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from app.domain.states import EventType, FlightState


@dataclass(frozen=True)
class BrainConfig:
    """Thresholds in minutes. Populated from app.config.settings — never hardcoded in logic."""
    delay_t1_min: int
    delay_t2_min: int
    delay_t3_min: int
    recovery_buffer_min: int


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


def decide(
    current_state: FlightState,
    last_known: Optional[FlightStatus],
    incoming: FlightStatus,
    config: BrainConfig,
) -> Decision:
    """
    Decide the next state and whether to notify, given an incoming status update.

    Required behaviour (see tests/test_brain.py and claude_code_prompt.md):
      - Dedup / out-of-order: drop incoming if event_ts <= last_known.event_ts, or if
        content_hash matches the last processed one -> Decision(current_state, False, NONE).
      - delay_minutes = (estimated_in_utc or actual_in_utc) - scheduled_in_utc.
        Use gate arrival; ignore runway touchdown.
      - Tier escalation with hysteresis; BACK_ON_SCHEDULE only below (tier - recovery buffer).
      - cancelled / diverted / departed / landed -> their notifiable event types.
      - Anything not material -> Decision(current_state, False, NONE) (silent log).
    """
    raise NotImplementedError("Milestone 2: implement the brain and make test_brain.py pass.")
