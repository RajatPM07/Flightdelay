"""
APScheduler jobs: STA+24h backstop force-close and alert deregistration.

Two responsibilities:
  1. Backstop: at STA + backstop_hours (default 24h) force-close any policy still
     not in a terminal state, deregister the AeroAPI alert, and write an audit row.
     Scheduled per-policy at issuance via schedule_backstop().
  2. Terminal deregister: called directly from the webhook pipeline when a terminal
     state is reached; also cancels the pending backstop job so it doesn't fire empty.

_run_backstop() holds the testable logic; apply_backstop() is the APScheduler
entry point that creates its own session and delegates.
"""
from __future__ import annotations

import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlmodel import Session

from app.domain.states import EventType, FlightState, TERMINAL_STATES
from app.models import EventLog, FlightStateRow, Policy
from app.providers.flightdata.base import FlightDataProvider
from app.services.audit import append_event_log

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone="UTC")


# ------------------------------------------------------------------
# Backstop scheduling
# ------------------------------------------------------------------

def schedule_backstop(policy_id: str, fire_at: datetime) -> None:
    """
    Register a one-shot DateTrigger job for this policy.

    Safe to call even when the scheduler is not yet running (jobs are queued and
    executed once it starts). Does nothing if the scheduler module is imported
    outside a running server (e.g. test processes that don't call scheduler.start()).
    """
    if not scheduler.running:
        logger.warning(
            "scheduler.not_running — backstop not scheduled",
            extra={"policy_id": policy_id, "fire_at": fire_at.isoformat()},
        )
        return

    scheduler.add_job(
        apply_backstop,
        trigger="date",
        run_date=fire_at,
        args=[policy_id],
        id=f"backstop:{policy_id}",
        replace_existing=True,
        misfire_grace_time=3600,  # fire up to 1h late (e.g. after a restart)
    )
    logger.info(
        "scheduler.backstop_scheduled",
        extra={"policy_id": policy_id, "fire_at": fire_at.isoformat()},
    )


def cancel_backstop(policy_id: str) -> None:
    """
    Remove the pending backstop job for this policy (called on normal terminal
    state via webhook so the job doesn't fire unnecessarily).
    Silent if the job has already fired or was never registered.
    """
    try:
        scheduler.remove_job(f"backstop:{policy_id}")
        logger.info("scheduler.backstop_cancelled", extra={"policy_id": policy_id})
    except Exception:
        pass  # already fired, never scheduled, or scheduler not running


# ------------------------------------------------------------------
# Backstop job
# ------------------------------------------------------------------

async def apply_backstop(policy_id: str) -> None:
    """
    APScheduler entry point. Creates its own session and delegates to _run_backstop.
    Errors are logged but not re-raised (APScheduler would retry on unhandled raises).
    """
    from app.db import engine
    from app.deps import get_flight_provider

    try:
        with Session(engine) as db:
            await _run_backstop(policy_id, db, get_flight_provider())
    except Exception:
        logger.exception("scheduler.backstop_error", extra={"policy_id": policy_id})


async def _run_backstop(
    policy_id: str, db: Session, provider: FlightDataProvider
) -> None:
    """
    Core backstop logic — testable without APScheduler running.

    If the flight is already in a terminal state, this is a no-op (the webhook
    pipeline beat us). Otherwise: transition to CLOSED, deregister the alert,
    update Policy.status, and append an audit row.
    """
    state_row = db.get(FlightStateRow, policy_id)
    if state_row is None:
        logger.info("scheduler.backstop_noop.not_found", extra={"policy_id": policy_id})
        return

    current_state = FlightState(state_row.current_state)
    if current_state in TERMINAL_STATES:
        logger.info(
            "scheduler.backstop_noop.already_terminal",
            extra={"policy_id": policy_id, "state": current_state.value},
        )
        return

    prev_state = current_state.value

    # Deregister first — if this fails, we still force-close in the DB so no
    # more webhooks are processed; AeroAPI will eventually time-out the subscription.
    if state_row.alert_id:
        try:
            await provider.deregister_alert(state_row.alert_id)
        except Exception:
            logger.exception(
                "scheduler.backstop.deregister_failed",
                extra={"policy_id": policy_id, "alert_id": state_row.alert_id},
            )

    state_row.current_state = FlightState.CLOSED.value
    db.add(state_row)

    policy = db.get(Policy, policy_id)
    if policy:
        policy.status = FlightState.CLOSED.value
        db.add(policy)

    append_event_log(
        db=db,
        policy_id=policy_id,
        source="backstop",
        raw_payload={},
        prev_state=prev_state,
        new_state=FlightState.CLOSED.value,
        decided_event_type=EventType.NONE.value,
        notified=False,
    )

    db.commit()
    logger.info(
        "scheduler.backstop_applied",
        extra={"policy_id": policy_id, "prev_state": prev_state},
    )


# ------------------------------------------------------------------
# Terminal deregister (called from webhook pipeline)
# ------------------------------------------------------------------

async def deregister_on_terminal(alert_id: str, provider: FlightDataProvider) -> None:
    """
    Deregister the AeroAPI alert subscription when the webhook pipeline
    transitions to a terminal state. Errors are logged, never raised.
    """
    try:
        await provider.deregister_alert(alert_id)
        logger.info("scheduler.deregister_on_terminal.ok", extra={"alert_id": alert_id})
    except Exception:
        logger.exception("scheduler.deregister_on_terminal.failed", extra={"alert_id": alert_id})
