"""FlightDataProvider interface. The brain never imports concrete providers; adapters depend
inward on the normalised FlightStatus, not the other way round."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from app.domain.brain import FlightStatus


@dataclass(frozen=True)
class EndpointTime:
    """One end of a flight (departure or arrival) for DISPLAY only.

    Deliberately NOT part of FlightStatus / the brain — the brain is arrival-centric and
    UTC-only. This carries the local-timezone context the live page needs to show the
    customer the time they'd read off their ticket.
    """
    utc: Optional[datetime]   # scheduled time, UTC-aware
    tz: Optional[str]         # IANA zone of THIS airport, e.g. "Asia/Kolkata"
    iata: Optional[str]       # e.g. "BOM"
    city: Optional[str]       # e.g. "Mumbai"


@dataclass(frozen=True)
class ScheduleView:
    """Localized departure + arrival schedule for a single feed (presentation only)."""
    departure: EndpointTime
    arrival: EndpointTime


class FlightDataProvider(ABC):
    """A swappable flight-status source (FlightAware now; AeroDataBox / Cirium later)."""

    name: str = "base"

    # "per_policy": one subscription per policy (FlightAware).
    # "per_flight_number": one shared subscription per flight number (AeroDataBox).
    subscription_scope: str = "per_policy"

    def extract_subject(self, raw_payload: dict) -> tuple[str, str] | None:
        """
        Return (normalised_flight_number, flight_date_yyyy_mm_dd) used to route an
        inbound webhook to active policies. Default None = route by alert_id instead.
        """
        return None

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

    # ------------------------------------------------------------------
    # Presentation seam (live page only) — optional, default no-op.
    # ------------------------------------------------------------------

    def schedule_view(self, raw_payload: dict) -> Optional[ScheduleView]:
        """Localized departure/arrival schedule for display. Default: not available."""
        return None

    async def lookup_snapshot(
        self, flight_number: str, flight_date: str
    ) -> tuple[FlightStatus, Optional[ScheduleView]]:
        """Read-only lookup for the live page: brain status + localized schedule.

        Real vendors override this to fetch ONCE and parse both from the same payload.
        The default keeps callers working for sources without schedule context (mock).
        """
        return (await self.get_baseline(flight_number, flight_date), None)
