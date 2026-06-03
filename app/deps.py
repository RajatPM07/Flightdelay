"""FastAPI dependency providers. Import these with Depends() in route handlers."""
from __future__ import annotations

from typing import Generator

from sqlmodel import Session

from app.config import settings
from app.db import engine
from app.domain.states import BrainConfig
from app.providers.flightdata.base import FlightDataProvider
from app.providers.flightdata.flightaware import FlightAwareProvider


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


# Singleton — constructed once per process; relies on settings being populated at startup.
_flight_provider: FlightDataProvider | None = None


def get_flight_provider() -> FlightDataProvider:
    global _flight_provider
    if _flight_provider is None:
        _flight_provider = FlightAwareProvider(
            api_key=settings.flightaware_api_key,
            base_url=settings.flightaware_base_url,
            webhook_secret=settings.flightaware_webhook_secret,
            public_webhook_base_url=settings.public_webhook_base_url,
        )
    return _flight_provider
