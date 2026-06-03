"""FlightDataProvider interface. The brain never imports concrete providers; adapters depend
inward on the normalised FlightStatus, not the other way round."""
from __future__ import annotations

from abc import ABC, abstractmethod

from app.domain.brain import FlightStatus


class FlightDataProvider(ABC):
    """A swappable flight-status source (FlightAware now; AeroDataBox / Cirium later)."""

    name: str = "base"

    @abstractmethod
    async def get_baseline(self, flight_number: str, flight_date: str) -> FlightStatus:
        """One-shot lookup at issuance to establish the starting snapshot."""

    @abstractmethod
    async def register_alert(self, policy_id: str, flight_number: str, flight_date: str) -> str:
        """Subscribe to push alerts for this flight. Returns the provider alert id."""

    @abstractmethod
    async def deregister_alert(self, alert_id: str) -> None:
        """Stop watching (called on terminal states / backstop)."""

    @abstractmethod
    def normalise(self, raw_payload: dict) -> FlightStatus:
        """Map a provider webhook/result payload into the vendor-agnostic FlightStatus."""
