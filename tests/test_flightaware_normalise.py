"""
Unit tests for FlightAwareProvider.normalise() and verify_signature().

All tests are offline — no network, no database.
"""
from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone

import pytest

from app.providers.flightdata.flightaware import FlightAwareProvider

STA_STR = "2026-07-01T14:30:00Z"
STA_UTC = datetime(2026, 7, 1, 14, 30, tzinfo=timezone.utc)

PROVIDER = FlightAwareProvider(
    api_key="dummy",
    base_url="https://aeroapi.flightaware.com/aeroapi",
    webhook_secret="test-secret",
    public_webhook_base_url="https://example.com",
)


def _flight(
    scheduled_in: str = STA_STR,
    estimated_in: str | None = None,
    actual_in: str | None = None,
    actual_off: str | None = None,
    cancelled: bool = False,
    diverted: bool = False,
) -> dict:
    return {
        "scheduled_in": scheduled_in,
        "estimated_in": estimated_in,
        "actual_in": actual_in,
        "actual_off": actual_off,
        "cancelled": cancelled,
        "diverted": diverted,
    }


# ------------------------------------------------------------------
# Direct flight-object payloads (from GET /flights)
# ------------------------------------------------------------------

def test_normalise_on_time():
    status = PROVIDER.normalise(_flight(estimated_in=STA_STR))
    assert status.scheduled_in_utc == STA_UTC
    assert status.estimated_in_utc == STA_UTC
    assert status.actual_in_utc is None
    assert not status.departed
    assert not status.cancelled
    assert not status.diverted


def test_normalise_delayed_70():
    eta = "2026-07-01T15:40:00Z"  # STA + 70 min
    status = PROVIDER.normalise(_flight(estimated_in=eta))
    delay = int((status.estimated_in_utc - status.scheduled_in_utc).total_seconds() / 60)
    assert delay == 70


def test_normalise_departed():
    status = PROVIDER.normalise(
        _flight(estimated_in="2026-07-01T16:30:00Z", actual_off="2026-07-01T14:28:00Z")
    )
    assert status.departed is True
    assert status.actual_in_utc is None


def test_normalise_landed():
    actual_in = "2026-07-01T16:48:00Z"  # gate arrival (chocks-on)
    status = PROVIDER.normalise(_flight(actual_in=actual_in))
    assert status.actual_in_utc == datetime(2026, 7, 1, 16, 48, tzinfo=timezone.utc)
    # gate arrival — not runway (actual_on is absent from our payload, correctly ignored)


def test_normalise_cancelled():
    status = PROVIDER.normalise(_flight(cancelled=True))
    assert status.cancelled is True
    assert not status.diverted


def test_normalise_diverted():
    status = PROVIDER.normalise(_flight(diverted=True))
    assert status.diverted is True
    assert not status.cancelled


def test_normalise_missing_scheduled_in_raises():
    with pytest.raises((ValueError, KeyError, AssertionError)):
        PROVIDER.normalise({"estimated_in": STA_STR})


# ------------------------------------------------------------------
# Alert webhook envelope ({"flight": {...}, "timestamp": "..."})
# ------------------------------------------------------------------

def test_normalise_webhook_envelope():
    envelope = {
        "event_code": "arrival",
        "alert_id": 99,
        "timestamp": "2026-07-01T16:00:00Z",
        "flight": _flight(estimated_in="2026-07-01T16:00:00Z"),
    }
    status = PROVIDER.normalise(envelope)
    assert status.scheduled_in_utc == STA_UTC
    # timestamp from envelope is used as event_ts
    assert status.event_ts == datetime(2026, 7, 1, 16, 0, tzinfo=timezone.utc)


def test_normalise_webhook_envelope_no_timestamp():
    before = datetime.now(timezone.utc)
    envelope = {
        "event_code": "departure",
        "flight": _flight(actual_off="2026-07-01T14:28:00Z"),
    }
    status = PROVIDER.normalise(envelope)
    after = datetime.now(timezone.utc)
    # Falls back to now() — should be within the test window.
    assert before <= status.event_ts <= after


# ------------------------------------------------------------------
# Dedup: identical payloads produce identical content_hash
# ------------------------------------------------------------------

def test_content_hash_stable():
    s1 = PROVIDER.normalise(_flight(estimated_in="2026-07-01T15:40:00Z"))
    s2 = PROVIDER.normalise(_flight(estimated_in="2026-07-01T15:40:00Z"))
    assert s1.content_hash == s2.content_hash


def test_content_hash_differs_on_change():
    s1 = PROVIDER.normalise(_flight(estimated_in="2026-07-01T15:40:00Z"))
    s2 = PROVIDER.normalise(_flight(estimated_in="2026-07-01T16:00:00Z"))
    assert s1.content_hash != s2.content_hash


# ------------------------------------------------------------------
# Webhook signature verification
# ------------------------------------------------------------------

def _make_sig(body: bytes, secret: str = "test-secret") -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_verify_signature_valid():
    body = b'{"event_code":"arrival"}'
    sig = _make_sig(body)
    assert PROVIDER.verify_signature(body, sig) is True


def test_verify_signature_invalid():
    body = b'{"event_code":"arrival"}'
    assert PROVIDER.verify_signature(body, "deadbeef") is False


def test_verify_signature_tampered_body():
    body = b'{"event_code":"arrival"}'
    sig = _make_sig(body)
    assert PROVIDER.verify_signature(b'{"event_code":"departure"}', sig) is False


def test_verify_signature_no_secret():
    open_provider = FlightAwareProvider(
        api_key="x", base_url="x", webhook_secret="", public_webhook_base_url="x"
    )
    # Open mode: no secret configured → always passes (dev only).
    assert open_provider.verify_signature(b"anything", "garbage") is True
