"""
FlightAware AeroAPI v4 adapter.

Endpoints used:
  GET  /flights/{ident}          — baseline snapshot at issuance
  POST /alerts                   — subscribe to push webhooks
  DELETE /alerts/{alert_id}      — deregister on terminal state / backstop

Every outbound call is logged at INFO with cost-tracking fields so we can measure
per-policy API spend. Inbound webhooks are HMAC-verified before processing.

NOTE (real-world friction): AeroAPI Alerts require a publicly reachable webhook URL.
Use ngrok locally: `ngrok http 8000` and set PUBLIC_WEBHOOK_BASE_URL in .env.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from app.domain.brain import FlightStatus
from app.providers.flightdata.base import FlightDataProvider

logger = logging.getLogger(__name__)

# AeroAPI returns datetimes as ISO 8601 strings in UTC (ending in Z or +00:00).
# We parse them uniformly and always store UTC-aware datetimes.


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO 8601 UTC string to a timezone-aware datetime, or return None."""
    if not value:
        return None
    # AeroAPI uses 'Z' suffix; fromisoformat only handles it in 3.11+.
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _content_hash(
    estimated_in: Optional[str],
    actual_in: Optional[str],
    actual_off: Optional[str],
    cancelled: bool,
    diverted: bool,
) -> str:
    """SHA-256 of the fields the brain cares about, for dedup / idempotency."""
    payload = json.dumps(
        [estimated_in, actual_in, actual_off, cancelled, diverted], sort_keys=True
    ).encode()
    return hashlib.sha256(payload).hexdigest()


class FlightAwareProvider(FlightDataProvider):
    name = "flightaware"

    def __init__(
        self,
        api_key: str,
        base_url: str,
        webhook_secret: str,
        public_webhook_base_url: str,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.webhook_secret = webhook_secret
        self.public_webhook_base_url = public_webhook_base_url.rstrip("/")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {"x-apikey": self.api_key}

    def _flight_from_response(self, data: dict) -> dict:
        """Extract the first matching flight dict from a /flights response."""
        flights = data.get("flights", [])
        if not flights:
            raise ValueError("AeroAPI returned no flights for the requested ident/date window.")
        return flights[0]

    # ------------------------------------------------------------------
    # FlightDataProvider interface
    # ------------------------------------------------------------------

    async def get_baseline(self, flight_number: str, flight_date: str) -> FlightStatus:
        """
        One-shot lookup at issuance.

        flight_date must be YYYY-MM-DD (local origin date). We query a 24-hour UTC window
        centred on that date; the first returned flight is used as the baseline.
        """
        url = f"{self.base_url}/flights/{flight_number}"
        params = {"start": f"{flight_date}T00:00:00Z", "end": f"{flight_date}T23:59:59Z"}

        logger.info(
            "aeroapi.get_baseline",
            extra={"flight_number": flight_number, "flight_date": flight_date, "url": url},
        )

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, headers=self._headers(), params=params)
            resp.raise_for_status()
            data = resp.json()

        flight = self._flight_from_response(data)
        status = self.normalise(flight)
        logger.info(
            "aeroapi.get_baseline.ok",
            extra={"flight_number": flight_number, "state_snapshot": status},
        )
        return status

    async def register_alert(
        self, policy_id: str, flight_number: str, flight_date: str
    ) -> str:
        """
        Subscribe to AeroAPI push alerts for this flight.

        The webhook URL is {PUBLIC_WEBHOOK_BASE_URL}/webhooks/flightaware.
        We rely on alert_id (stored in flight_state) to route the inbound webhook
        back to the correct policy_id.
        """
        url = f"{self.base_url}/alerts"
        webhook_url = f"{self.public_webhook_base_url}/webhooks/flightaware"

        body: dict[str, Any] = {
            "ident": flight_number,
            "start": f"{flight_date}T00:00:00Z",
            "end": f"{flight_date}T23:59:59Z",
            "events": {
                "departure": True,
                "arrival": True,
                "cancelled": True,
                "diverted": True,
                "flightplan": False,
            },
            "channels": [
                {
                    "channel": "url",
                    "config": {
                        "url": webhook_url,
                        # AeroAPI uses this secret to sign the X-Fa-Signature header.
                        **({"secret": self.webhook_secret} if self.webhook_secret else {}),
                    },
                }
            ],
        }

        logger.info(
            "aeroapi.register_alert",
            extra={"policy_id": policy_id, "flight_number": flight_number, "webhook_url": webhook_url},
        )

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, headers=self._headers(), json=body)
            resp.raise_for_status()
            data = resp.json()

        alert_id = str(data["alert_id"])
        logger.info("aeroapi.register_alert.ok", extra={"policy_id": policy_id, "alert_id": alert_id})
        return alert_id

    async def deregister_alert(self, alert_id: str) -> None:
        """Stop watching this flight (called on terminal states and by the backstop)."""
        url = f"{self.base_url}/alerts/{alert_id}"

        logger.info("aeroapi.deregister_alert", extra={"alert_id": alert_id})

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.delete(url, headers=self._headers())
            # 404 is fine — already deleted (idempotent).
            if resp.status_code not in (200, 204, 404):
                resp.raise_for_status()

        logger.info("aeroapi.deregister_alert.ok", extra={"alert_id": alert_id})

    def normalise(self, raw_payload: dict) -> FlightStatus:
        """
        Map an AeroAPI flight object (from GET /flights or an alert webhook) to FlightStatus.

        Handles two payload shapes:
          1. Direct flight object: {"scheduled_in": "...", "actual_in": "...", ...}
          2. Alert webhook envelope: {"flight": {...}, "event_code": "...", "timestamp": "..."}

        Key field mapping (use GATE arrival — actual_in — NOT runway touchdown — actual_on):
          scheduled_in  → scheduled_in_utc
          estimated_in  → estimated_in_utc
          actual_in     → actual_in_utc   ← chocks-on gate arrival (what we want)
          actual_off    → departed flag    ← aircraft airborne; NOT actual_on (touchdown)
          cancelled     → cancelled
          diverted      → diverted
        """
        envelope_ts: Optional[str] = None

        # Unwrap alert webhook envelope if present.
        if "flight" in raw_payload:
            envelope_ts = raw_payload.get("timestamp")
            flight = raw_payload["flight"]
        else:
            flight = raw_payload

        scheduled_in: Optional[str] = flight.get("scheduled_in")
        estimated_in: Optional[str] = flight.get("estimated_in")
        actual_in: Optional[str] = flight.get("actual_in")        # chocks-on (gate)
        actual_off: Optional[str] = flight.get("actual_off")      # takeoff — departed signal
        cancelled: bool = bool(flight.get("cancelled", False))
        diverted: bool = bool(flight.get("diverted", False))

        if not scheduled_in:
            raise ValueError("AeroAPI payload missing required field 'scheduled_in'.")

        scheduled_in_utc = _parse_dt(scheduled_in)
        assert scheduled_in_utc is not None  # guaranteed above

        # event_ts: use the envelope timestamp if present, otherwise now (fetch time).
        event_ts = _parse_dt(envelope_ts) or datetime.now(timezone.utc)

        return FlightStatus(
            event_ts=event_ts,
            scheduled_in_utc=scheduled_in_utc,
            estimated_in_utc=_parse_dt(estimated_in),
            actual_in_utc=_parse_dt(actual_in),
            departed=actual_off is not None,
            cancelled=cancelled,
            diverted=diverted,
            content_hash=_content_hash(estimated_in, actual_in, actual_off, cancelled, diverted),
        )

    # ------------------------------------------------------------------
    # Webhook security
    # ------------------------------------------------------------------

    def verify_signature(self, body_bytes: bytes, signature_header: str) -> bool:
        """
        Verify the HMAC-SHA256 signature that AeroAPI attaches to alert webhooks.

        AeroAPI sends the digest in the X-Fa-Signature header as a hex string.
        Returns True only when the signatures match. Always use hmac.compare_digest
        to prevent timing attacks.

        If no webhook_secret is configured, returns True (dev/open mode).
        Raise a ValueError in production if the secret is missing.
        """
        if not self.webhook_secret:
            return True  # open mode — only acceptable in local dev
        expected = hmac.new(
            self.webhook_secret.encode(),
            body_bytes,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, signature_header.lower())
