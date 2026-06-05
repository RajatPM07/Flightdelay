"""Page 2 — live multi-vendor flight-status lookup.

Read-only: given a flight number, fetch the real current status from a chosen vendor
via the existing one-shot ``get_baseline`` lookup. No policy, monitoring, subscription,
audit, or messaging side-effects. Gated behind DEMO_MODE like the rest of the demo UI.
"""
from __future__ import annotations

from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from app.config import settings
from app.deps import get_brain_config, get_lookup_provider
from app.domain.brain import _delay_minutes
from app.providers.flightdata.carriers import normalize_flight_number, resolve_icao_ident
from app.services.issuance import _state_from_baseline

router = APIRouter(tags=["live"])

_PAGE = Path(__file__).resolve().parent.parent / "static" / "live.html"
_VENDORS = ("flightaware", "aerodatabox")


def _require_demo() -> None:
    if not settings.demo_mode:
        raise HTTPException(status_code=404, detail="Not found")


def _resolved_ident(vendor: str, flight: str) -> str:
    """The vendor-specific ident the lookup actually queries (surfaces IATA->ICAO)."""
    if vendor == "flightaware":
        return resolve_icao_ident(flight)
    return normalize_flight_number(flight)


@router.get("/live")
async def live_page() -> FileResponse:
    _require_demo()
    return FileResponse(_PAGE, media_type="text/html")


@router.get("/live/lookup")
async def live_lookup(
    flight: str = Query(..., min_length=2),
    date: str = Query(..., description="YYYY-MM-DD"),
    vendor: str = Query("flightaware"),
) -> dict:
    _require_demo()

    if vendor not in _VENDORS:
        raise HTTPException(status_code=400, detail=f"Unknown vendor '{vendor}'.")

    flight = flight.strip().upper()
    provider = get_lookup_provider(vendor)

    try:
        status = await provider.get_baseline(flight, date)
    except ValueError as exc:
        # No matching flight / unparseable response.
        raise HTTPException(
            status_code=404,
            detail=f"No flight found for {flight} on {date}.",
        ) from exc
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        if code == 400:
            raise HTTPException(
                status_code=400,
                detail="FlightAware resolves flights only ~2 days out — try a nearer date.",
            ) from exc
        if code == 429:
            raise HTTPException(
                status_code=429,
                detail="AeroDataBox is rate-limited (free tier ~1 req/s). Wait a moment and retry.",
            ) from exc
        if code in (401, 403):
            raise HTTPException(
                status_code=502,
                detail=f"{vendor} rejected the request (check API key / plan tier).",
            ) from exc
        raise HTTPException(status_code=502, detail="Upstream flight provider error.") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Could not reach the flight provider.") from exc

    config = get_brain_config()
    delay = _delay_minutes(status)
    state = _state_from_baseline(status, config)

    return {
        "vendor": vendor,
        "requested_ident": flight,
        "resolved_ident": _resolved_ident(vendor, flight),
        "flight_date": date,
        "status": {
            "scheduled_in_utc": status.scheduled_in_utc,
            "estimated_in_utc": status.estimated_in_utc,
            "actual_in_utc": status.actual_in_utc,
            "departed": status.departed,
            "cancelled": status.cancelled,
            "diverted": status.diverted,
        },
        "delay_minutes": delay,
        "state": state.value,
        "notes": "Delay is computed from the best gate-arrival estimate (not runway touchdown).",
    }
