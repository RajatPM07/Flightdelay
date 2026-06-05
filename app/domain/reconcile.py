"""Two-feed reconciliation — pure, I/O-free.

Vendors disagree: one may report a flight airborne ("EnRoute") while another has not
yet logged the departure. The product's value is the in-house decision layer that
reconciles them. This mirrors the brain's forward-only priority
(landed > cancelled > diverted > departed > delay tiers): a lifecycle signal seen by ANY
feed wins, and the freshest arrival estimate is used.
"""
from __future__ import annotations

from app.domain.brain import FlightStatus


def reconcile(statuses: list[FlightStatus]) -> FlightStatus:
    """Merge one or more vendor snapshots into a single, most-progressed FlightStatus.

    Rules (most-progressed wins, matching the brain's priority):
      - departed / cancelled / diverted: True if ANY feed reports it.
      - actual_in (gate arrival / landed): the most-recently-observed non-null value.
      - estimated_in: from the most-recently-observed feed that has one.
      - scheduled_in / event_ts: taken from the most-recently-observed feed.
    """
    if not statuses:
        raise ValueError("reconcile() requires at least one FlightStatus")
    if len(statuses) == 1:
        return statuses[0]

    by_recent = sorted(statuses, key=lambda s: s.event_ts, reverse=True)
    base = by_recent[0]

    actual_in = next((s.actual_in_utc for s in by_recent if s.actual_in_utc is not None), None)
    estimated_in = next((s.estimated_in_utc for s in by_recent if s.estimated_in_utc is not None), None)

    return FlightStatus(
        event_ts=base.event_ts,
        scheduled_in_utc=base.scheduled_in_utc,
        estimated_in_utc=estimated_in,
        actual_in_utc=actual_in,
        departed=any(s.departed for s in statuses),
        cancelled=any(s.cancelled for s in statuses),
        diverted=any(s.diverted for s in statuses),
    )
