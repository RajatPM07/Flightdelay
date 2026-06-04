"""Central configuration. All brain thresholds live here — never hardcode them in logic."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    log_level: str = "INFO"
    mock_providers: bool = False
    demo_mode: bool = False

    database_url: str = ""

    # FlightAware AeroAPI
    flightaware_api_key: str = ""
    flightaware_base_url: str = "https://aeroapi.flightaware.com/aeroapi"
    flightaware_webhook_secret: str = ""
    public_webhook_base_url: str = ""

    # Flight-data provider selection: "flightaware" | "aerodatabox" (mock via mock_providers)
    flight_provider: str = "flightaware"
    aerodatabox_api_key: str = ""
    aerodatabox_base_url: str = "https://aerodatabox.p.rapidapi.com"
    aerodatabox_rapidapi_host: str = "aerodatabox.p.rapidapi.com"
    aerodatabox_webhook_secret: str = ""

    # Twilio
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_whatsapp_from: str = ""
    twilio_sms_from: str = ""

    # Email
    sendgrid_api_key: str = ""
    email_from: str = "alerts@example.com"

    # Brain config (minutes / hours)
    delay_t1_min: int = 30
    delay_t2_min: int = 60
    delay_t3_min: int = 120
    recovery_buffer_min: int = 15
    backstop_hours: int = 24


settings = Settings()
