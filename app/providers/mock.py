"""Mock provider implementations for offline local development and testing."""
from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from app.domain.brain import FlightStatus
from app.providers.flightdata.base import FlightDataProvider
from app.providers.messaging.base import Channel, MessageProvider

logger = logging.getLogger(__name__)


class MockFlightDataProvider(FlightDataProvider):
    """
    Mock flight status provider.
    
    Returns generated, valid mock baselines and simulates subscription registration.
    """
    name = "mock"

    def __init__(self, webhook_secret: str = "") -> None:
        self.webhook_secret = webhook_secret

    async def get_baseline(self, flight_number: str, flight_date: str) -> FlightStatus:
        """Return a mock flight baseline scheduled for ~14:30 UTC on the flight date."""
        logger.info(
            "mock.get_baseline",
            extra={"flight_number": flight_number, "flight_date": flight_date},
        )
        
        try:
            parsed_date = datetime.strptime(flight_date, "%Y-%m-%d")
        except ValueError:
            parsed_date = datetime.now(timezone.utc)
            
        sta = datetime(
            parsed_date.year,
            parsed_date.month,
            parsed_date.day,
            14,
            30,
            tzinfo=timezone.utc,
        )
        
        # Default to an on-time baseline
        return FlightStatus(
            event_ts=datetime.now(timezone.utc),
            scheduled_in_utc=sta,
            estimated_in_utc=sta,
            actual_in_utc=None,
            departed=False,
            cancelled=False,
            diverted=False,
            content_hash=hashlib.sha256(
                f"mock-{flight_number}-{flight_date}-ontime".encode()
            ).hexdigest(),
        )

    async def register_alert(
        self, policy_id: str, flight_number: str, flight_date: str
    ) -> str:
        alert_id = f"mock-alert-{uuid.uuid4().hex[:12]}"
        logger.info(
            "mock.register_alert",
            extra={"policy_id": policy_id, "flight_number": flight_number, "alert_id": alert_id},
        )
        return alert_id

    async def deregister_alert(self, alert_id: str) -> None:
        logger.info("mock.deregister_alert", extra={"alert_id": alert_id})

    def normalise(self, raw_payload: dict) -> FlightStatus:
        """
        Normalise raw mock webhook payloads.
        Supports both wrapped FlightAware-style envelopes and simple flat inputs.
        """
        logger.info("mock.normalise", extra={"raw_payload": raw_payload})
        
        if "flight" in raw_payload:
            flight = raw_payload["flight"]
            envelope_ts = raw_payload.get("timestamp")
        else:
            flight = raw_payload
            envelope_ts = None
            
        def _parse_iso(v: Optional[str]) -> Optional[datetime]:
            if not v:
                return None
            return datetime.fromisoformat(v.replace("Z", "+00:00"))

        scheduled_in = _parse_iso(flight.get("scheduled_in")) or datetime.now(timezone.utc)
        estimated_in = _parse_iso(flight.get("estimated_in"))
        actual_in = _parse_iso(flight.get("actual_in"))
        actual_off = flight.get("actual_off")
        
        departed = actual_off is not None or bool(flight.get("departed", False))
        cancelled = bool(flight.get("cancelled", False))
        diverted = bool(flight.get("diverted", False))
        
        event_ts = (
            _parse_iso(envelope_ts)
            or _parse_iso(raw_payload.get("event_ts"))
            or datetime.now(timezone.utc)
        )
        
        # Build stable content hash
        payload_data = f"{estimated_in}-{actual_in}-{departed}-{cancelled}-{diverted}"
        content_hash = flight.get("content_hash") or hashlib.sha256(payload_data.encode()).hexdigest()
        
        return FlightStatus(
            event_ts=event_ts,
            scheduled_in_utc=scheduled_in,
            estimated_in_utc=estimated_in,
            actual_in_utc=actual_in,
            departed=departed,
            cancelled=cancelled,
            diverted=diverted,
            content_hash=content_hash,
        )

    def verify_signature(self, body_bytes: bytes, signature_header: str) -> bool:
        """In mock mode, accept all signatures."""
        logger.info("mock.verify_signature.always_passes", extra={"sig": signature_header})
        return True


class MockMessageProvider(MessageProvider):
    """
    Mock message delivery provider.
    
    Logs messaging requests and prints rendered text directly to standard output.
    """
    async def send(
        self, channel: Channel, to: str, body: str, subject: Optional[str] = None
    ) -> str:
        msg_id = f"mock-msg-{uuid.uuid4().hex[:12]}"
        
        # Pretty print mock notification output
        print(f"\n📢 --- [MOCK NOTIFICATION DELIVERED] --- 📢")
        print(f"  Channel: {channel.value.upper()}")
        print(f"  To:      {to}")
        if subject:
            print(f"  Subject: {subject}")
        print(f"  Body:    {body}")
        print(f"  Msg ID:  {msg_id}")
        print(f"----------------------------------------\n")
        
        logger.info(
            "mock.send_notification",
            extra={"channel": channel.value, "to": to, "msg_id": msg_id},
        )
        return msg_id
