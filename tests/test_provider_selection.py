import pytest
import app.deps as deps
from app.providers.flightdata.aerodatabox import AeroDataBoxProvider
from app.providers.flightdata.flightaware import FlightAwareProvider
from app.providers.mock import MockFlightDataProvider


@pytest.fixture(autouse=True)
def _reset_singleton():
    deps._flight_provider = None
    yield
    deps._flight_provider = None


def test_selects_aerodatabox(monkeypatch):
    monkeypatch.setattr(deps.settings, "mock_providers", False)
    monkeypatch.setattr(deps.settings, "flight_provider", "aerodatabox")
    monkeypatch.setattr(deps.settings, "aerodatabox_api_key", "k")
    assert isinstance(deps.get_flight_provider(), AeroDataBoxProvider)


def test_defaults_to_flightaware(monkeypatch):
    monkeypatch.setattr(deps.settings, "mock_providers", False)
    monkeypatch.setattr(deps.settings, "flight_provider", "flightaware")
    assert isinstance(deps.get_flight_provider(), FlightAwareProvider)


def test_mock_takes_precedence(monkeypatch):
    monkeypatch.setattr(deps.settings, "mock_providers", True)
    monkeypatch.setattr(deps.settings, "flight_provider", "aerodatabox")
    assert isinstance(deps.get_flight_provider(), MockFlightDataProvider)
