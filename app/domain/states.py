"""Flight lifecycle states, event types, and brain config. Pure data — no I/O."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


@dataclass(frozen=True)
class BrainConfig:
    """Thresholds in minutes. Populated from app.config.settings — never hardcoded in logic."""
    delay_t1_min: int
    delay_t2_min: int
    delay_t3_min: int
    recovery_buffer_min: int


class FlightState(str, Enum):
    REGISTERED = "REGISTERED"
    ON_TIME = "ON_TIME"
    DELAYED_T1 = "DELAYED_T1"   # >= T1 and < T2
    DELAYED_T2 = "DELAYED_T2"   # >= T2 and < T3
    DELAYED_T3 = "DELAYED_T3"   # >= T3
    DEPARTED = "DEPARTED"
    LANDED = "LANDED"
    CANCELLED = "CANCELLED"
    DIVERTED = "DIVERTED"
    CLOSED = "CLOSED"


TERMINAL_STATES = {FlightState.LANDED, FlightState.CANCELLED, FlightState.CLOSED}


class EventType(str, Enum):
    """What the customer is told. None of these implies any payment."""
    MONITORING_ACTIVE = "MONITORING_ACTIVE"
    DELAY_TIER_1 = "DELAY_TIER_1"
    DELAY_TIER_2 = "DELAY_TIER_2"
    DELAY_TIER_3 = "DELAY_TIER_3"
    BACK_ON_SCHEDULE = "BACK_ON_SCHEDULE"
    DEPARTED = "DEPARTED"
    LANDED = "LANDED"
    CANCELLED = "CANCELLED"
    DIVERTED = "DIVERTED"
    NONE = "NONE"  # silent: log only, do not notify
