"""Page 2 — live multi-vendor flight-status lookup + two-feed reconciliation.

Read-only: given a flight number, fetch the real current status from the vendors via the
existing one-shot ``get_baseline`` lookup, and reconcile both feeds through the brain's
priority. No policy, monitoring, subscription, audit, or messaging side-effects. Gated
behind DEMO_MODE like the rest of the demo UI.
"""
from __future__ import annotations

from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from app.config import settings
from app.deps import get_brain_config, get_lookup_provider
from app.domain.brain import FlightStatus, _delay_minutes
from app.domain.reconcile import reconcile
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


def _http_error_detail(vendor: str, exc: httpx.HTTPStatusError) -> tuple[int, str]:
    code = exc.response.status_code
    if code == 400:
        return 400, "FlightAware resolves flights only ~2 days out — try a nearer date."
    if code == 429:
        return 429, "AeroDataBox is rate-limited (free tier ~1 req/s). Wait a moment and retry."
    if code in (401, 403):
        return 502, f"{vendor} rejected the request (check API key / plan tier)."
    return 502, "Upstream flight provider error."


async def _fetch(vendor: str, flight: str, date: str) -> FlightStatus:
    """Raise httpx/ValueError up to the caller, which maps them to responses."""
    return await get_lookup_provider(vendor).get_baseline(flight, date)


def _status_payload(status: FlightStatus) -> dict:
    config = get_brain_config()
    return {
        "scheduled_in_utc": status.scheduled_in_utc,
        "estimated_in_utc": status.estimated_in_utc,
        "actual_in_utc": status.actual_in_utc,
        "departed": status.departed,
        "cancelled": status.cancelled,
        "diverted": status.diverted,
        "delay_minutes": _delay_minutes(status),
        "state": _state_from_baseline(status, config).value,
    }


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
    """Single-vendor lookup (used by the edge-case demos)."""
    _require_demo()
    if vendor not in _VENDORS:
        raise HTTPException(status_code=400, detail=f"Unknown vendor '{vendor}'.")
    flight = flight.strip().upper()

    try:
        status = await _fetch(vendor, flight, date)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=f"No flight found for {flight} on {date}.") from exc
    except httpx.HTTPStatusError as exc:
        code, detail = _http_error_detail(vendor, exc)
        raise HTTPException(status_code=code, detail=detail) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Could not reach the flight provider.") from exc

    return {
        "vendor": vendor,
        "requested_ident": flight,
        "resolved_ident": _resolved_ident(vendor, flight),
        "flight_date": date,
        "status": _status_payload(status),
        "notes": "Delay is computed from the best gate-arrival estimate (not runway touchdown).",
    }


@router.get("/live/reconcile")
async def live_reconcile(
    flight: str = Query(..., min_length=2),
    date: str = Query(..., description="YYYY-MM-DD"),
) -> dict:
    """Query BOTH vendors and reconcile them into one verdict (the product's core value)."""
    _require_demo()
    flight = flight.strip().upper()

    feeds: list[dict] = []
    statuses: list[FlightStatus] = []
    for vendor in _VENDORS:
        try:
            status = await _fetch(vendor, flight, date)
        except ValueError:
            feeds.append({"vendor": vendor, "ok": False, "error": "No flight found for this date."})
            continue
        except httpx.HTTPStatusError as exc:
            _, detail = _http_error_detail(vendor, exc)
            feeds.append({"vendor": vendor, "ok": False, "error": detail})
            continue
        except httpx.HTTPError:
            feeds.append({"vendor": vendor, "ok": False, "error": "Could not reach this provider."})
            continue
        statuses.append(status)
        feeds.append({
            "vendor": vendor,
            "ok": True,
            "resolved_ident": _resolved_ident(vendor, flight),
            "status": _status_payload(status),
        })

    if not statuses:
        raise HTTPException(status_code=404, detail=f"No flight found for {flight} on {date} from any vendor.")

    merged = reconcile(statuses)
    reconciled = _status_payload(merged)

    ok_states = {f["vendor"]: f["status"]["state"] for f in feeds if f.get("ok")}
    agreement = len(set(ok_states.values())) <= 1
    if agreement:
        explanation = f"All available feeds agree: {reconciled['state'].replace('_', ' ')}."
    else:
        disagree = ", ".join(f"{v} = {s.replace('_',' ')}" for v, s in ok_states.items())
        explanation = (
            f"Feeds disagree ({disagree}). Reconciled to {reconciled['state'].replace('_',' ')} "
            "using the engine's priority (landed > cancelled > diverted > departed > delay tiers) — "
            "a lifecycle signal seen by any feed wins; a diverging feed is usually just lagging."
        )

    return {
        "requested_ident": flight,
        "flight_date": date,
        "feeds": feeds,
        "reconciled": reconciled,
        "agreement": agreement,
        "explanation": explanation,
        "notes": "Delay is computed from the best gate-arrival estimate (not runway touchdown).",
    }
