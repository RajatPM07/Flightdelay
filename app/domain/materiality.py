"""
Materiality helpers — pure functions used by the brain.

Keep all threshold logic here so it is independently testable and free of magic numbers.
"""
from __future__ import annotations

from app.domain.states import FlightState
from app.domain.brain import BrainConfig


def tier_for_delay(delay_minutes: int, config: BrainConfig) -> FlightState:
    """Map a delay in minutes to the corresponding on-time / delay-tier state.

    Implement: < T1 -> ON_TIME; [T1,T2) -> DELAYED_T1; [T2,T3) -> DELAYED_T2; >= T3 -> DELAYED_T3.
    """
    raise NotImplementedError("Milestone 2.")


def crossed_upward(prev_state: FlightState, new_tier: FlightState) -> bool:
    """True only when new_tier is a strictly higher delay tier than prev_state (escalation)."""
    raise NotImplementedError("Milestone 2.")


def recovered_to_on_time(delay_minutes: int, prev_state: FlightState, config: BrainConfig) -> bool:
    """True only when a previously-delayed flight drops below (T1 - recovery_buffer). Hysteresis."""
    raise NotImplementedError("Milestone 2.")
