"""Flight lifecycle states and customer-facing event types. Pure data — no I/O."""
from __future__ import annotations

from enum import Enum


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
