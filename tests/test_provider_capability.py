from app.providers.flightdata.base import FlightDataProvider
from app.providers.flightdata.flightaware import FlightAwareProvider
from app.providers.mock import MockFlightDataProvider


def test_base_default_scope_is_per_policy():
    assert FlightDataProvider.subscription_scope == "per_policy"


def test_flightaware_is_per_policy():
    p = FlightAwareProvider(api_key="k", base_url="https://x", webhook_secret="s", public_webhook_base_url="https://p")
    assert p.subscription_scope == "per_policy"


def test_mock_is_per_policy():
    p = MockFlightDataProvider(webhook_secret="s")
    assert p.subscription_scope == "per_policy"


def test_extract_subject_default_returns_none():
    p = MockFlightDataProvider(webhook_secret="s")
    assert p.extract_subject({"anything": 1}) is None
