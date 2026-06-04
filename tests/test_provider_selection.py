import pytest
import app.deps as deps
from app.config import Settings
from app.providers.flightdata.aerodatabox import AeroDataBoxProvider
from app.providers.flightdata.flightaware import FlightAwareProvider
from app.providers.mock import MockFlightDataProvider, MockMessageProvider


@pytest.fixture(autouse=True)
def _reset_singleton():
    deps._flight_provider = None
    deps._msg_provider = None
    yield
    deps._flight_provider = None
    deps._msg_provider = None


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


def test_mock_messaging_defaults_to_mock_providers():
    assert Settings(_env_file=None, mock_providers=True).mock_messaging is True
    assert Settings(_env_file=None, mock_providers=False).mock_messaging is False


def test_mock_messaging_true_returns_mock(monkeypatch):
    monkeypatch.setattr(deps.settings, "mock_providers", False)
    monkeypatch.setattr(deps.settings, "mock_messaging", True)
    assert isinstance(deps.get_msg_provider(), MockMessageProvider)


def test_mock_messaging_false_returns_real_composite(monkeypatch):
    monkeypatch.setattr(deps.settings, "mock_providers", True)
    monkeypatch.setattr(deps.settings, "mock_messaging", False)
    provider = deps.get_msg_provider()
    assert isinstance(provider, deps._CompositeMessageProvider)
