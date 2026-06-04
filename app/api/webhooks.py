"""Webhook receiver for FlightAware AeroAPI alerts."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from sqlmodel import Session

from app.config import settings
from app.deps import get_brain_config, get_db, get_flight_provider, get_msg_provider
from app.domain.states import BrainConfig
from app.providers.flightdata.base import FlightDataProvider
from app.providers.messaging.base import MessageProvider
from app.services.webhook import process_aerodatabox_webhook, process_webhook

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

_SIG_HEADER = "x-fa-signature"


@router.post("/flightaware")
async def flightaware_alert(
    request: Request,
    db: Session = Depends(get_db),
    provider: FlightDataProvider = Depends(get_flight_provider),
    config: BrainConfig = Depends(get_brain_config),
    msg_provider: MessageProvider = Depends(get_msg_provider),
) -> dict:
    """
    Receive a FlightAware AeroAPI push alert.

    Always returns 200 so AeroAPI does not enter a retry storm on transient errors.
    Signature verification, dedup, and all state logic happen inside process_webhook().
    """
    raw_body = await request.body()
    signature = request.headers.get(_SIG_HEADER, "")

    try:
        payload = await request.json()
    except Exception:
        logger.warning("webhook.bad_json")
        return {"status": "ok"}

    try:
        await process_webhook(
            raw_payload=payload,
            raw_body=raw_body,
            signature_header=signature,
            db=db,
            provider=provider,
            config=config,
            msg_provider=msg_provider,
        )
    except Exception:
        logger.exception("webhook.processing_error")

    return {"status": "ok"}


@router.post("/aerodatabox/{secret}")
async def aerodatabox_alert(
    secret: str,
    request: Request,
    db: Session = Depends(get_db),
    provider: FlightDataProvider = Depends(get_flight_provider),
    config: BrainConfig = Depends(get_brain_config),
    msg_provider: MessageProvider = Depends(get_msg_provider),
) -> dict:
    """AeroDataBox push alert. Auth is the secret path segment (AeroDataBox does not sign).
    Always returns 200 to avoid provider retry storms."""
    if not settings.aerodatabox_webhook_secret or secret != settings.aerodatabox_webhook_secret:
        logger.warning("aerodatabox.bad_secret")
        return {"status": "ok"}
    raw_body = await request.body()
    try:
        payload = await request.json()
    except Exception:
        logger.warning("aerodatabox.bad_json")
        return {"status": "ok"}
    try:
        await process_aerodatabox_webhook(
            raw_payload=payload,
            raw_body=raw_body,
            signature_header="",
            db=db,
            provider=provider,
            config=config,
            msg_provider=msg_provider,
        )
    except Exception:
        logger.exception("aerodatabox.processing_error")
    return {"status": "ok"}
