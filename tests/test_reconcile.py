"""Pure two-feed reconciliation tests — no DB, no network."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.domain.brain import FlightStatus
from app.domain.reconcile import reconcile
from app.services.issuance import _state_from_baseline

SCHED = datetime(2026, 6, 5, 20, 10, tzinfo=timezone.utc)


def _status(**kw) -> FlightStatus:
    base = dict(event_ts=SCHED, scheduled_in_utc=SCHED)
    base.update(kw)
    return FlightStatus(**base)


def test_airborne_in_one_feed_wins(config):
    # The real divergence: AeroDataBox EnRoute (departed) vs FlightAware not-yet-departed.
    adb = _status(event_ts=SCHED, departed=True, estimated_in_utc=SCHED)
    fa = _status(event_ts=SCHED - timedelta(minutes=2), departed=False,
                 estimated_in_utc=SCHED + timedelta(minutes=10))
    merged = reconcile([fa, adb])
    assert merged.departed is True
    assert _state_from_baseline(merged, config).value == "DEPARTED"


def test_landed_beats_departed():
    landed = _status(event_ts=SCHED + timedelta(minutes=1), departed=True,
                     actual_in_utc=SCHED + timedelta(minutes=5))
    departed = _status(event_ts=SCHED, departed=True)
    merged = reconcile([departed, landed])
    assert merged.actual_in_utc is not None  # landed signal preserved


def test_cancelled_if_any_feed_cancels():
    a = _status(cancelled=True)
    b = _status(estimated_in_utc=SCHED)
    assert reconcile([b, a]).cancelled is True


def test_freshest_estimate_used():
    older = _status(event_ts=SCHED - timedelta(minutes=10), estimated_in_utc=SCHED)
    newer = _status(event_ts=SCHED, estimated_in_utc=SCHED + timedelta(minutes=45))
    merged = reconcile([older, newer])
    assert merged.estimated_in_utc == SCHED + timedelta(minutes=45)


def test_single_status_returns_itself():
    s = _status(departed=True)
    assert reconcile([s]) is s
