"""FastAPI dependency providers. Import these with Depends() in route handlers."""
from __future__ import annotations

from typing import Generator

from sqlmodel import Session

from app.config import settings
from app.db import engine
from app.domain.states import BrainConfig
from app.providers.flightdata.base import FlightDataProvider
from app.providers.flightdata.flightaware import FlightAwareProvider
from app.providers.messaging.base import MessageProvider
from app.providers.messaging.email import EmailProvider
from app.providers.messaging.twilio import TwilioProvider
from app.providers.mock import MockFlightDataProvider, MockMessageProvider


def get_db() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session


def get_brain_config() -> BrainConfig:
    return BrainConfig(
        delay_t1_min=settings.delay_t1_min,
        delay_t2_min=settings.delay_t2_min,
        delay_t3_min=settings.delay_t3_min,
        recovery_buffer_min=settings.recovery_buffer_min,
    )


def get_lookup_provider(vendor: str) -> FlightDataProvider:
    """Build a REAL flight-data provider for the read-only status lookup (page 2).

    Deliberately ignores ``MOCK_PROVIDERS`` — the simulate demo (page 1) stays mocked
    while the lookup page hits live vendor feeds. Not cached: cheap to construct and the
    vendor is chosen per-request.
    """
    if vendor == "flightaware":
        return FlightAwareProvider(
            api_key=settings.flightaware_api_key,
            base_url=settings.flightaware_base_url,
            webhook_secret=settings.flightaware_webhook_secret,
            public_webhook_base_url=settings.public_webhook_base_url,
        )
    if vendor == "aerodatabox":
        from app.providers.flightdata.aerodatabox import AeroDataBoxProvider

        return AeroDataBoxProvider(
            api_key=settings.aerodatabox_api_key,
            base_url=settings.aerodatabox_base_url,
            rapidapi_host=settings.aerodatabox_rapidapi_host,
            webhook_secret=settings.aerodatabox_webhook_secret,
            public_webhook_base_url=settings.public_webhook_base_url,
        )
    raise ValueError(f"Unknown vendor '{vendor}' (expected 'flightaware' or 'aerodatabox').")


# Singletons — constructed once per process; rely on settings being populated at startup.
_flight_provider: FlightDataProvider | None = None
_msg_provider: MessageProvider | None = None


def get_flight_provider() -> FlightDataProvider:
    global _flight_provider
    if _flight_provider is None:
        if settings.mock_providers:
            _flight_provider = MockFlightDataProvider(
                webhook_secret=settings.flightaware_webhook_secret
            )
        elif settings.flight_provider == "aerodatabox":
            from app.providers.flightdata.aerodatabox import AeroDataBoxProvider
            _flight_provider = AeroDataBoxProvider(
                api_key=settings.aerodatabox_api_key,
                base_url=settings.aerodatabox_base_url,
                rapidapi_host=settings.aerodatabox_rapidapi_host,
                webhook_secret=settings.aerodatabox_webhook_secret,
                public_webhook_base_url=settings.public_webhook_base_url,
            )
        else:
            _flight_provider = FlightAwareProvider(
                api_key=settings.flightaware_api_key,
                base_url=settings.flightaware_base_url,
                webhook_secret=settings.flightaware_webhook_secret,
                public_webhook_base_url=settings.public_webhook_base_url,
            )
    return _flight_provider


class _CompositeMessageProvider(MessageProvider):
    """Routes WHATSAPP/SMS to Twilio and EMAIL to SendGrid."""

    def __init__(self, twilio: TwilioProvider, email: EmailProvider) -> None:
        self._twilio = twilio
        self._email = email

    async def send(
        self, channel: "Channel", to: str, body: str, subject: str | None = None  # noqa: F821
    ) -> str:
        from app.providers.messaging.base import Channel
        if channel in (Channel.WHATSAPP, Channel.SMS):
            return await self._twilio.send(channel, to, body, subject)
        return await self._email.send(channel, to, body, subject)


def get_msg_provider() -> MessageProvider:
    global _msg_provider
    if _msg_provider is None:
        if settings.mock_messaging:
            _msg_provider = MockMessageProvider()
        else:
            _msg_provider = _CompositeMessageProvider(
                twilio=TwilioProvider(
                    account_sid=settings.twilio_account_sid,
                    auth_token=settings.twilio_auth_token,
                    whatsapp_from=settings.twilio_whatsapp_from,
                    sms_from=settings.twilio_sms_from,
                ),
                email=EmailProvider(
                    sendgrid_api_key=settings.sendgrid_api_key,
                    email_from=settings.email_from,
                ),
            )
    return _msg_provider
