#!/usr/bin/env python
"""
Pre-flight env check for TripSecure+ Flight Delay MVP.

Run before starting the server or the E2E smoke test.
Exits 0 if all required vars are set; exits 1 with a clear diff of what's missing.

Usage:
    python scripts/check_env.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Load .env if present so this works without the server running.
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass  # python-dotenv may not be installed in the check env


REQUIRED = {
    "DATABASE_URL": "Supabase/Postgres connection string (postgresql+psycopg://...)",
    "FLIGHTAWARE_API_KEY": "AeroAPI key from flightaware.com",
    "FLIGHTAWARE_WEBHOOK_SECRET": "HMAC secret shared with AeroAPI for webhook signing",
    "PUBLIC_WEBHOOK_BASE_URL": "Publicly reachable base URL (e.g. https://xxxx.ngrok.io)",
}

# At least one messaging channel must be configured.
MESSAGING_OPTIONS = {
    "TWILIO": ["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_WHATSAPP_FROM"],
    "SENDGRID": ["SENDGRID_API_KEY", "EMAIL_FROM"],
}

PLACEHOLDER_PREFIXES = (
    "your-ngrok",
    "postgresql+psycopg://USER:PASSWORD",
    "https://your-",
)


def check() -> int:
    errors: list[str] = []
    warnings: list[str] = []

    mock_mode = os.getenv("MOCK_PROVIDERS", "").strip().lower() in ("true", "1")

    print("\n── TripSecure+ Flight Delay — pre-flight env check ──\n")

    if mock_mode:
        print("✓  Mock Provider Mode is ACTIVE (MOCK_PROVIDERS=True).")
        print("   External API keys (AeroAPI, Twilio, SendGrid) are bypassed.")
        print("   DATABASE_URL will default to local SQLite if unset.\n")
        return 0

    # Required vars.
    for var, description in REQUIRED.items():
        val = os.getenv(var, "").strip()
        if not val:
            errors.append(f"  MISSING  {var}  —  {description}")
        elif any(val.startswith(p) for p in PLACEHOLDER_PREFIXES):
            warnings.append(f"  PLACEHOLDER  {var}  =  {val!r}")

    # Messaging: at least one complete channel.
    messaging_ok = False
    for channel, vars_ in MESSAGING_OPTIONS.items():
        if all(os.getenv(v, "").strip() for v in vars_):
            messaging_ok = True
            break
    if not messaging_ok:
        errors.append(
            "  MISSING  messaging channel — set either "
            f"({', '.join(MESSAGING_OPTIONS['TWILIO'])}) "
            f"or ({', '.join(MESSAGING_OPTIONS['SENDGRID'])})"
        )

    # ngrok reminder.
    webhook_url = os.getenv("PUBLIC_WEBHOOK_BASE_URL", "")
    if webhook_url and "ngrok" in webhook_url.lower():
        warnings.append(
            f"  REMINDER  ngrok URL detected ({webhook_url!r}). "
            "Make sure `ngrok http 8000` is running and this URL is current."
        )

    if warnings:
        print("Warnings:")
        for w in warnings:
            print(w)
        print()

    if errors:
        print("Errors (required before running):")
        for e in errors:
            print(e)
        print("\n✗  Fix the above before proceeding.\n")
        return 1

    print("✓  All required env vars are set.\n")
    return 0


if __name__ == "__main__":
    sys.exit(check())
