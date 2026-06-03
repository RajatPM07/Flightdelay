"""
CANONICAL SPEC for the brain. Build app.domain.brain.decide() until this passes — BEFORE any I/O.

Scenario: one flight, STA (gate) = 2026-07-01 14:30 UTC.
Feed 7 events; assert EXACTLY 3 customer notifications and a final state of LANDED.

  1. baseline on-time            -> no notify
  2. estimate +25 min            -> no notify (under T1=30)
  3. estimate +70 min            -> NOTIFY (crosses T2),  state DELAYED_T2
  4. estimate +65 min            -> no notify (same-tier wobble; hysteresis)
  5. duplicate of #4             -> no notify (dedup)
  6. departed, estimate +140 min -> NOTIFY (crosses T3),  state DEPARTED
  7. landed +138 min             -> NOTIFY landed,        state LANDED
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.domain.brain import FlightStatus, decide
from app.domain.states import FlightState

STA = datetime(2026, 7, 1, 14, 30, tzinfo=timezone.utc)


def _status(minute_offset_observed: int, est_delay_min: int | None = None,
            departed: bool = False, landed_delay_min: int | None = None,
            cancelled: bool = False, diverted: bool = False, h: str = "") -> FlightStatus:
    est = STA + timedelta(minutes=est_delay_min) if est_delay_min is not None else None
    actual = STA + timedelta(minutes=landed_delay_min) if landed_delay_min is not None else None
    return FlightStatus(
        event_ts=STA - timedelta(hours=3) + timedelta(minutes=minute_offset_observed),
        scheduled_in_utc=STA,
        estimated_in_utc=est,
        actual_in_utc=actual,
        departed=departed,
        cancelled=cancelled,
        diverted=diverted,
        content_hash=h or f"h{minute_offset_observed}-{est_delay_min}-{departed}-{landed_delay_min}",
    )


def test_seven_event_sequence(config):
    sequence = [
        _status(0, est_delay_min=0),                       # 1 baseline on-time
        _status(10, est_delay_min=25),                     # 2 +25
        _status(20, est_delay_min=70),                     # 3 +70 -> T2 NOTIFY
        _status(30, est_delay_min=65),                     # 4 +65 wobble
        _status(30, est_delay_min=65, h="dupe"),           # 5 duplicate (same/older ts)
        _status(60, est_delay_min=140, departed=True),     # 6 departed +140 -> T3 NOTIFY
        _status(150, landed_delay_min=138),                # 7 landed -> NOTIFY
    ]
    # make event #5 a true duplicate/out-of-order of #4
    sequence[4] = FlightStatus(**{**sequence[3].__dict__, "content_hash": sequence[3].content_hash})

    state = FlightState.ON_TIME
    last = sequence[0]
    notifications = 0
    for incoming in sequence[1:]:
        decision = decide(state, last, incoming, config)
        if decision.should_notify:
            notifications += 1
        state = decision.new_state
        last = incoming

    assert notifications == 3, f"expected 3 notifications, got {notifications}"
    assert state == FlightState.LANDED
