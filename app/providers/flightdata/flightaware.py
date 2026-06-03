"""FlightAware AeroAPI adapter (live feed). Implements FlightDataProvider.

Endpoints: GET /flights/{ident} for baseline; Alerts API for push subscriptions.
Verify inbound webhooks against FLIGHTAWARE_WEBHOOK_SECRET. Log every external call for cost.
"""
from __future__ import annotations

from app.domain.brain import FlightStatus
from app.providers.flightdata.base import FlightDataProvider


class FlightAwareProvider(FlightDataProvider):
    name = "flightaware"

    def __init__(self, api_key: str, base_url: str, webhook_secret: str, public_webhook_base_url: str):
        self.api_key = api_key
        self.base_url = base_url
        self.webhook_secret = webhook_secret
        self.public_webhook_base_url = public_webhook_base_url

    async def get_baseline(self, flight_number: str, flight_date: str) -> FlightStatus:
        raise NotImplementedError("Milestone 3.")

    async def register_alert(self, policy_id: str, flight_number: str, flight_date: str) -> str:
        raise NotImplementedError("Milestone 3.")

    async def deregister_alert(self, alert_id: str) -> None:
        raise NotImplementedError("Milestone 3 / 7.")

    def normalise(self, raw_payload: dict) -> FlightStatus:
        raise NotImplementedError("Milestone 3/5: map AeroAPI fields -> FlightStatus (use actual_in).")
