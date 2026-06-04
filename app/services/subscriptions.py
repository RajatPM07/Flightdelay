# app/services/subscriptions.py
from __future__ import annotations
from sqlmodel import Session, select
from sqlalchemy.exc import IntegrityError
from app.models import FlightSubscription, FlightStateRow, Policy
from app.domain.states import TERMINAL_STATES  # set of FlightState
from app.providers.flightdata.base import FlightDataProvider
from app.providers.flightdata.carriers import normalize_flight_number


def _norm(number: str) -> str:
    return normalize_flight_number(number)


def _active_count(db: Session, flight_number: str, exclude_policy: str | None = None) -> int:
    target = _norm(flight_number)
    rows = db.exec(
        select(FlightStateRow, Policy).join(Policy, Policy.policy_id == FlightStateRow.policy_id)
    ).all()
    terminal = {s.value for s in TERMINAL_STATES}
    return sum(
        1
        for state_row, policy in rows
        if _norm(policy.flight_number) == target
        and state_row.current_state not in terminal
        and state_row.policy_id != exclude_policy
    )


async def ensure_subscribed(db: Session, provider: FlightDataProvider, flight_number: str, flight_date: str, policy_id: str) -> str:
    if provider.subscription_scope == "per_policy":
        return await provider.register_alert(policy_id, flight_number, flight_date)
    key = _norm(flight_number)
    existing = db.get(FlightSubscription, key)
    if existing:
        return existing.subscription_id
    sub_id = await provider.register_alert(policy_id, flight_number, flight_date)
    db.add(FlightSubscription(subject_key=key, provider=provider.name, subscription_id=sub_id))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.get(FlightSubscription, key)
        if existing:
            return existing.subscription_id
        raise
    return sub_id


async def release_subscription(db: Session, provider: FlightDataProvider, flight_number: str, policy_id: str) -> None:
    if provider.subscription_scope == "per_policy":
        return  # handled by the existing per-alert deregister path
    key = _norm(flight_number)
    if _active_count(db, flight_number, exclude_policy=policy_id) > 0:
        return  # other active policies still need it
    sub = db.get(FlightSubscription, key)
    if sub:
        await provider.deregister_alert(sub.subscription_id)
        db.delete(sub)
        db.commit()
