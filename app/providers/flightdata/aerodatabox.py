"""
AeroDataBox (via RapidAPI) flight-data adapter.

Endpoints used:
  GET    /flights/number/{flight_number}/{flight_date}  — baseline snapshot
  POST   /subscriptions/webhook/FlightByNumber/{number} — subscribe (per flight number)
  DELETE /subscriptions/webhook/{id}                    — deregister

Subscriptions are PER FLIGHT NUMBER (not per policy). Reference-counting is handled
by app/services/subscriptions.py; this adapter just performs the raw HTTP calls.

Webhook auth model: the secret is embedded in the path segment of the callback URL,
so verify_signature always returns True here — the route itself validates the path.

Datetime format from AeroDataBox: "2026-06-05 20:10Z" or "2026-06-05 20:10:00Z"
(space separator, trailing Z, optional seconds).
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from app.domain.brain import FlightStatus
from app.providers.flightdata.base import FlightDataProvider
from app.providers.flightdata.carriers import normalize_flight_number

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _parse_adb_dt(value: Optional[str]) -> Optional[datetime]:
    """Parse AeroDataBox datetime strings to a UTC-aware datetime, or return None.

    Handles both "2026-06-05 20:10Z" and "2026-06-05 20:10:00Z" variants.
    """
    if not value:
        return None
    return datetime.fromisoformat(value.replace(" ", "T").replace("Z", "+00:00"))


def _unwrap(raw: dict) -> dict:
    """Normalise the two payload shapes AeroDataBox may deliver.

    1. Direct flight object — already has a "number" key.
    2. Webhook envelope — wraps the flight under "data", "flight", or "result".
    3. List wrappers — "flights" or "data" containing a list.
    """
    if "number" in raw:
        return raw
    for key in ("data", "flight", "result"):
        val = raw.get(key)
        if isinstance(val, dict):
            return val
    for key in ("flights", "data"):
        val = raw.get(key)
        if isinstance(val, list) and val:
            return val[0]
    return raw


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

class AeroDataBoxProvider(FlightDataProvider):
    name = "aerodatabox"
    subscription_scope = "per_flight_number"

    def __init__(
        self,
        api_key: str,
        base_url: str,
        rapidapi_host: str,
        webhook_secret: str,
        public_webhook_base_url: str,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.rapidapi_host = rapidapi_host
        self.webhook_secret = webhook_secret
        self.public_webhook_base_url = public_webhook_base_url.rstrip("/")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {
            "X-RapidAPI-Key": self.api_key,
            "X-RapidAPI-Host": self.rapidapi_host,
        }

    # ------------------------------------------------------------------
    # FlightDataProvider interface
    # ------------------------------------------------------------------

    async def get_baseline(self, flight_number: str, flight_date: str) -> FlightStatus:
        """One-shot lookup at issuance."""
        url = f"{self.base_url}/flights/number/{normalize_flight_number(flight_number)}/{flight_date}"
        params = {"withAircraftImage": "false", "withLocation": "false"}

        logger.info(
            "aerodatabox.get_baseline",
            extra={
                "flight_number": flight_number,
                "flight_date": flight_date,
                "url": url,
            },
        )

        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(url, headers=self._headers(), params=params)
            resp.raise_for_status()
            data = resp.json()

        flights = data if isinstance(data, list) else (data.get("flights") or [data])
        if not flights:
            raise ValueError(
                f"AeroDataBox returned no flights for {flight_number} on {flight_date}."
            )

        status = self.normalise(flights[0])
        logger.info(
            "aerodatabox.get_baseline.ok",
            extra={"flight_number": flight_number, "state_snapshot": status},
        )
        return status

    async def register_alert(
        self, policy_id: str, flight_number: str, flight_date: str
    ) -> str:
        """Subscribe to AeroDataBox webhook for this flight number."""
        normed = normalize_flight_number(flight_number)
        url = f"{self.base_url}/subscriptions/webhook/FlightByNumber/{normed}"
        webhook_url = (
            f"{self.public_webhook_base_url}/webhooks/aerodatabox/{self.webhook_secret}"
        )

        body: dict[str, Any] = {
            "url": webhook_url,
            "maxDeliveryRetries": 2,
        }

        logger.info(
            "aerodatabox.register_alert",
            extra={
                "policy_id": policy_id,
                "flight_number": flight_number,
                # webhook_url omitted — contains the path-embedded secret
            },
        )

        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                url,
                headers=self._headers(),
                params={"useCredits": "true"},
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()

        # AeroDataBox may return the subscription id under different key names;
        # we try the most likely candidates in order.
        raw_id = data.get("id") or data.get("subscriptionId") or data.get("subscriptionid")
        if not raw_id:
            raise ValueError(f"AeroDataBox subscription response missing ID key. Got: {list(data.keys())}")
        subscription_id = str(raw_id)
        logger.info(
            "aerodatabox.register_alert.ok",
            extra={"policy_id": policy_id, "subscription_id": subscription_id},
        )
        return subscription_id

    async def deregister_alert(self, alert_id: str) -> None:
        """Remove an AeroDataBox webhook subscription."""
        url = f"{self.base_url}/subscriptions/webhook/{alert_id}"

        logger.info("aerodatabox.deregister_alert", extra={"alert_id": alert_id})

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.delete(url, headers=self._headers())
            # 200, 204, 404 are all acceptable (idempotent delete).
            if resp.status_code not in (200, 204, 404):
                resp.raise_for_status()

        logger.info("aerodatabox.deregister_alert.ok", extra={"alert_id": alert_id})

    def normalise(self, raw_payload: dict) -> FlightStatus:
        """Map an AeroDataBox flight object (GET response or webhook) to FlightStatus.

        Payload shapes handled:
          1. Direct flight object with a "number" key.
          2. Webhook envelope: {"data": <flight>}, {"flight": <flight>}, etc.

        Key field mapping:
          arrival.scheduledTime.utc   → scheduled_in_utc
          arrival.revisedTime.utc     → estimated_in_utc (preferred; actual once Arrived)
          arrival.predictedTime.utc   → estimated_in_utc fallback (ML prediction)
          status == "Arrived"         → actual_in_utc = revisedTime (gate arrival time)
          status in departed set      → departed = True
          status == "Canceled"        → cancelled = True   (one-L AeroDataBox spelling)
          status == "Diverted"        → diverted = True
        """
        flight = _unwrap(raw_payload)
        arr = flight.get("arrival") or {}
        status = str(flight.get("status") or "")

        # Scheduled arrival (required).
        scheduled = _parse_adb_dt((arr.get("scheduledTime") or {}).get("utc"))
        if scheduled is None:
            raise ValueError(
                "AeroDataBox payload missing required field arrival.scheduledTime.utc"
            )

        # Estimated arrival: revised time takes priority over ML prediction.
        revised = _parse_adb_dt((arr.get("revisedTime") or {}).get("utc"))
        predicted = _parse_adb_dt((arr.get("predictedTime") or {}).get("utc"))
        estimated = revised or predicted

        # Status flags.
        cancelled = status == "Canceled"
        diverted = status == "Diverted"
        arrived = status == "Arrived"
        departed = status in {"Departed", "EnRoute", "Approaching", "Arrived", "Diverted"}

        # actual_in is only set once the flight has arrived (gate chocks-on).
        actual_in = revised if arrived else None

        # event_ts: provider's own last-updated timestamp, or now as fallback.
        event_ts = _parse_adb_dt(flight.get("lastUpdatedUtc")) or datetime.now(timezone.utc)

        # Content hash covers all fields the brain uses for dedup.
        content_hash = hashlib.sha256(
            json.dumps(
                [
                    estimated.isoformat() if estimated else None,
                    actual_in.isoformat() if actual_in else None,
                    departed,
                    cancelled,
                    diverted,
                ]
            ).encode()
        ).hexdigest()

        return FlightStatus(
            event_ts=event_ts,
            scheduled_in_utc=scheduled,
            estimated_in_utc=estimated,
            actual_in_utc=actual_in,
            departed=departed,
            cancelled=cancelled,
            diverted=diverted,
            content_hash=content_hash,
        )

    def extract_subject(self, raw_payload: dict) -> tuple[str, str] | None:
        """Return (normalised_flight_number, origin_local_date) for webhook routing.

        Flight number: "6E 1341" → "6E1341" (space stripped, uppercased).
        Date: taken from departure.scheduledTime.local (origin timezone), e.g.
              "2026-06-05 22:15+05:30" → "2026-06-05".
        Falls back to arrival.scheduledTime.utc date prefix if departure local is absent.
        """
        flight = _unwrap(raw_payload)
        number = flight.get("number")
        if not number:
            return None

        dep = flight.get("departure") or {}
        local = (dep.get("scheduledTime") or {}).get("local")
        if local:
            date = local[:10]
        else:
            # Fallback: arrival UTC date.
            arr = flight.get("arrival") or {}
            utc_str = (arr.get("scheduledTime") or {}).get("utc", "")
            date = utc_str[:10]

        if not date:
            return None

        return (normalize_flight_number(number), date)

    def verify_signature(self, body_bytes: bytes, signature_header: str) -> bool:
        """AeroDataBox uses a URL-secret model: the secret is a path segment in the
        callback URL, validated by the route handler.  No header signature to verify."""
        return True
