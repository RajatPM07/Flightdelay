"""
Materiality helpers — pure functions used by the brain.

Keep all threshold logic here so it is independently testable and free of magic numbers.
"""
from __future__ import annotations

from app.domain.states import BrainConfig, FlightState

_TIER_ORDER = [
    FlightState.ON_TIME,
    FlightState.DELAYED_T1,
    FlightState.DELAYED_T2,
    FlightState.DELAYED_T3,
]

_DELAYED_STATES = {FlightState.DELAYED_T1, FlightState.DELAYED_T2, FlightState.DELAYED_T3}


def tier_for_delay(delay_minutes: int, config: BrainConfig) -> FlightState:
    """< T1 -> ON_TIME; [T1,T2) -> DELAYED_T1; [T2,T3) -> DELAYED_T2; >= T3 -> DELAYED_T3."""
    if delay_minutes >= config.delay_t3_min:
        return FlightState.DELAYED_T3
    if delay_minutes >= config.delay_t2_min:
        return FlightState.DELAYED_T2
    if delay_minutes >= config.delay_t1_min:
        return FlightState.DELAYED_T1
    return FlightState.ON_TIME


def crossed_upward(prev_state: FlightState, new_tier: FlightState) -> bool:
    """True only when new_tier is a strictly higher delay tier than prev_state (escalation)."""
    if prev_state not in _TIER_ORDER or new_tier not in _TIER_ORDER:
        return False
    return _TIER_ORDER.index(new_tier) > _TIER_ORDER.index(prev_state)


def recovered_to_on_time(delay_minutes: int, prev_state: FlightState, config: BrainConfig) -> bool:
    """True only when a previously-delayed flight drops below (T1 - recovery_buffer). Hysteresis."""
    if prev_state not in _DELAYED_STATES:
        return False
    return delay_minutes < config.delay_t1_min - config.recovery_buffer_min
