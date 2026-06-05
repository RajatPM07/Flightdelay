"""Tests for the read-only multi-vendor status lookup (page 2). No network."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

import app.api.live as live
import app.config as cfg
from app.domain.brain import FlightStatus
from app.main import app
from app.providers.flightdata.carriers import resolve_icao_ident

client = TestClient(app)

_SCHED = datetime(2026, 6, 10, 14, 30, tzinfo=timezone.utc)


class _FakeProvider:
    def __init__(self, *, result=None, exc=None):
        self._result = result
        self._exc = exc

    async def get_baseline(self, flight_number: str, flight_date: str) -> FlightStatus:
        if self._exc is not None:
            raise self._exc
        return self._result


def _inject(monkeypatch, **kwargs):
    monkeypatch.setattr(cfg.settings, "demo_mode", True)
    monkeypatch.setattr(live, "get_lookup_provider", lambda vendor: _FakeProvider(**kwargs))


def _http_error(status: int) -> httpx.HTTPStatusError:
    req = httpx.Request("GET", "https://vendor.example/flights")
    resp = httpx.Response(status, request=req)
    return httpx.HTTPStatusError("boom", request=req, response=resp)


def test_happy_path_maps_ident_delay_and_state(monkeypatch):
    status = FlightStatus(
        event_ts=_SCHED,
        scheduled_in_utc=_SCHED,
        estimated_in_utc=_SCHED + timedelta(minutes=90),
    )
    _inject(monkeypatch, result=status)
    r = client.get("/live/lookup", params={"flight": "6e1341", "date": "2026-06-10", "vendor": "flightaware"})
    assert r.status_code == 200
    d = r.json()
    assert d["vendor"] == "flightaware"
    assert d["requested_ident"] == "6E1341"
    assert d["resolved_ident"] == resolve_icao_ident("6E1341") != "6E1341"  # IATA -> ICAO surfaced
    assert d["delay_minutes"] == 90
    assert d["state"] == "DELAYED_T2"
    assert d["status"]["departed"] is False


def test_not_found_is_friendly_404(monkeypatch):
    _inject(monkeypatch, exc=ValueError("no flights"))
    r = client.get("/live/lookup", params={"flight": "ZZ9999", "date": "2026-06-10", "vendor": "flightaware"})
    assert r.status_code == 404
    assert "No flight found" in r.json()["detail"]


def test_future_date_400_is_friendly(monkeypatch):
    _inject(monkeypatch, exc=_http_error(400))
    r = client.get("/live/lookup", params={"flight": "6E1341", "date": "2027-01-01", "vendor": "flightaware"})
    assert r.status_code == 400
    assert "2 days" in r.json()["detail"]


def test_rate_limit_429_is_friendly(monkeypatch):
    _inject(monkeypatch, exc=_http_error(429))
    r = client.get("/live/lookup", params={"flight": "6E1341", "date": "2026-06-10", "vendor": "aerodatabox"})
    assert r.status_code == 429
    assert "rate-limited" in r.json()["detail"]


def test_unknown_vendor_is_400(monkeypatch):
    monkeypatch.setattr(cfg.settings, "demo_mode", True)
    r = client.get("/live/lookup", params={"flight": "6E1341", "date": "2026-06-10", "vendor": "bogus"})
    assert r.status_code == 400


def test_lookup_404_when_demo_off(monkeypatch):
    monkeypatch.setattr(cfg.settings, "demo_mode", False)
    r = client.get("/live/lookup", params={"flight": "6E1341", "date": "2026-06-10", "vendor": "flightaware"})
    assert r.status_code == 404
