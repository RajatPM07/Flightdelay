"""Webhook receiver for FlightAware AeroAPI alerts."""
from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/flightaware")
async def flightaware_alert(request: Request) -> dict:
    # Milestone 5: verify signature (FLIGHTAWARE_WEBHOOK_SECRET); normalise payload;
    # load state; call brain.decide(); persist new state + append event_log; if should_notify,
    # hand to notifier. Must be idempotent on retried deliveries.
    raise NotImplementedError("Milestone 5.")
