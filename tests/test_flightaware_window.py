"""_baseline_window — FlightAware AeroAPI only serves ~2 days into the future.

The baseline query widens to +/-1 day around the ticket date so a flight departing near
local midnight still resolves. But the widened `end` must be clamped to AeroAPI's ~2-day
horizon, or a flight exactly 2 days out is pushed over the edge into a guaranteed 400.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.providers.flightdata.flightaware import _baseline_window

NOW = datetime(2026, 6, 6, 9, 0, tzinfo=timezone.utc)


def test_window_within_horizon_keeps_plus_one_day():
    start, end = _baseline_window("2026-06-06", NOW)  # today
    assert start == "2026-06-05T00:00:00Z"
    assert end == "2026-06-07T23:59:59Z"  # +1 day preserved (inside horizon)


def test_window_clamps_end_to_two_day_horizon():
    # Flight exactly 2 days out: naive +1 widening would ask for 2026-06-09 -> 400.
    start, end = _baseline_window("2026-06-08", NOW)
    assert end == "2026-06-08T23:59:59Z"  # clamped to now + 2d, not 06-09
    assert start == "2026-06-07T00:00:00Z"


def test_window_never_emits_inverted_range_for_far_future():
    # Genuinely unservable date: window stays valid (start <= end), FA will simply 404/400.
    start, end = _baseline_window("2026-06-20", NOW)
    assert start <= end
