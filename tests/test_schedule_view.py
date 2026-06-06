"""schedule_view() — localized departure/arrival schedule for the live page.

The brain stays arrival-centric & UTC-only; this presentation data is parsed from the SAME
raw vendor payload the status comes from (no second fetch) and rendered in each airport's
local timezone. These tests pin the per-vendor mapping.
"""
from __future__ import annotations

import json
import pathlib

from app.providers.flightdata.aerodatabox import AeroDataBoxProvider
from app.providers.flightdata.flightaware import FlightAwareProvider

ADB_FIXTURE = json.loads(
    (pathlib.Path(__file__).parent / "fixtures/aerodatabox_flight.json").read_text()
)


def _adb():
    return AeroDataBoxProvider(
        api_key="k", base_url="https://x", rapidapi_host="h",
        webhook_secret="s", public_webhook_base_url="https://pub",
    )


def _fa():
    return FlightAwareProvider(
        api_key="k", base_url="https://x", webhook_secret="s", public_webhook_base_url="https://pub",
    )


def test_aerodatabox_schedule_view_departure_and_arrival():
    view = _adb().schedule_view(ADB_FIXTURE)
    assert view is not None
    # Departure: Mumbai, IST.
    assert view.departure.utc.isoformat() == "2026-06-05T16:45:00+00:00"
    assert view.departure.tz == "Asia/Kolkata"
    assert view.departure.iata == "BOM"
    assert view.departure.city == "Mumbai"
    # Arrival: Doha, Qatar tz — NOT IST.
    assert view.arrival.utc.isoformat() == "2026-06-05T20:10:00+00:00"
    assert view.arrival.tz == "Asia/Qatar"
    assert view.arrival.iata == "DOH"
    assert view.arrival.city == "Doha"


def test_aerodatabox_schedule_view_unwraps_envelope():
    view = _adb().schedule_view({"data": ADB_FIXTURE})
    assert view is not None and view.departure.iata == "BOM"


def test_flightaware_schedule_view():
    flight = {
        "scheduled_out": "2026-06-05T16:45:00Z",
        "scheduled_in": "2026-06-05T20:10:00Z",
        "origin": {"code_iata": "BOM", "city": "Mumbai", "timezone": "Asia/Kolkata"},
        "destination": {"code_iata": "DOH", "city": "Doha", "timezone": "Asia/Qatar"},
    }
    view = _fa().schedule_view(flight)
    assert view is not None
    assert view.departure.utc.isoformat() == "2026-06-05T16:45:00+00:00"
    assert view.departure.tz == "Asia/Kolkata"
    assert view.departure.iata == "BOM"
    assert view.arrival.tz == "Asia/Qatar"
    assert view.arrival.city == "Doha"


def test_flightaware_schedule_view_unwraps_alert_envelope():
    flight = {
        "scheduled_out": "2026-06-05T16:45:00Z",
        "scheduled_in": "2026-06-05T20:10:00Z",
        "origin": {"code_iata": "BOM", "city": "Mumbai", "timezone": "Asia/Kolkata"},
        "destination": {"code_iata": "DOH", "city": "Doha", "timezone": "Asia/Qatar"},
    }
    view = _fa().schedule_view({"flight": flight, "event_code": "arrival"})
    assert view is not None and view.arrival.iata == "DOH"
