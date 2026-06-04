#!/usr/bin/env python
"""
End-to-end smoke test for TripSecure+ Flight Delay MVP (Milestone 8).

Phases
------
1. Pre-flight  — env check, DB connectivity, /health
2. Issuance    — POST /policies against real AeroAPI; capture policy_id + alert_id
3. Synthetic webhooks — drive the state machine without waiting for AeroAPI push:
     a. +90 min delay   → expect DELAYED_T2 + notification
     b. Departed +140   → expect DEPARTED  + notification
     c. Landed  +138    → expect LANDED    + notification + deregister
4. Assertion   — verify DB state, audit rows, notification rows
5. Summary     — pass / fail with counts

Requirements
------------
- .env populated with real credentials (run `python scripts/check_env.py` first)
- Server running: `uvicorn app.main:app --reload`
- ngrok running:  `ngrok http 8000` (PUBLIC_WEBHOOK_BASE_URL must match)

Usage
-----
    python scripts/e2e_smoke.py \\
        --flight AI101 \\
        --date 2026-07-01 \\
        --pnr TEST123 \\
        --name "Smoke Test" \\
        --phone +911234567890 \\
        [--base-url http://localhost:8000]

The script is idempotent: each run creates a fresh policy and works independently.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# Make the project root importable so `from app.models import ...` works when this
# file is run directly as `python scripts/e2e_smoke.py` (Python otherwise only adds
# the scripts/ dir to sys.path, not the project root).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Load .env before importing app modules.
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

import httpx
from sqlmodel import Session, create_engine, select

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BOLD = "\033[1m"
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"

_pass = 0
_fail = 0


def ok(msg: str) -> None:
    global _pass
    _pass += 1
    print(f"  {GREEN}✓{RESET}  {msg}")


def fail(msg: str) -> None:
    global _fail
    _fail += 1
    print(f"  {RED}✗{RESET}  {msg}")


def info(msg: str) -> None:
    print(f"  {YELLOW}·{RESET}  {msg}")


def section(title: str) -> None:
    print(f"\n{BOLD}── {title} ──{RESET}")


def _sign(body_bytes: bytes) -> str:
    secret = os.getenv("FLIGHTAWARE_WEBHOOK_SECRET", "")
    return hmac.new(secret.encode(), body_bytes, hashlib.sha256).hexdigest()


def _post_webhook(base_url: str, payload: dict) -> httpx.Response:
    body = json.dumps(payload).encode()
    sig = _sign(body)
    return httpx.post(
        f"{base_url}/webhooks/flightaware",
        content=body,
        headers={"content-type": "application/json", "x-fa-signature": sig},
        timeout=15,
    )


def _make_flight_payload(
    alert_id: str,
    sta_iso: str,
    est_delay_min: int | None = None,
    departed: bool = False,
    landed_delay_min: int | None = None,
    ts_offset_min: int = 0,
) -> dict:
    """Build a synthetic AeroAPI alert webhook payload."""
    sta = datetime.fromisoformat(sta_iso)
    eta_str = (sta + timedelta(minutes=est_delay_min)).isoformat() if est_delay_min is not None else None
    actual_str = (sta + timedelta(minutes=landed_delay_min)).isoformat() if landed_delay_min is not None else None
    actual_off = (sta - timedelta(hours=3) + timedelta(minutes=ts_offset_min)).isoformat() if departed else None

    return {
        "alert_id": alert_id,
        "event_code": "arrival_change",
        "timestamp": (datetime.now(timezone.utc) + timedelta(minutes=ts_offset_min)).isoformat(),
        "flight": {
            "scheduled_in": sta_iso,
            "estimated_in": eta_str,
            "actual_in": actual_str,
            "actual_off": actual_off,
            "cancelled": False,
            "diverted": False,
        },
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(args: argparse.Namespace) -> int:
    base = args.base_url.rstrip("/")

    # ── Phase 1: Pre-flight ──────────────────────────────────────────────────
    section("Phase 1 — Pre-flight checks")

    db_url = os.getenv("DATABASE_URL", "")
    if not db_url:
        fail("DATABASE_URL not set — run `python scripts/check_env.py`")
        return 1

    webhook_secret = os.getenv("FLIGHTAWARE_WEBHOOK_SECRET", "")
    if not webhook_secret:
        fail("FLIGHTAWARE_WEBHOOK_SECRET not set — webhook signature verification will reject all webhooks")
        return 1

    try:
        r = httpx.get(f"{base}/health", timeout=5)
        r.raise_for_status()
        ok(f"/health → {r.json()}")
    except Exception as exc:
        fail(f"Server not reachable at {base}: {exc}")
        info("Start it with: uvicorn app.main:app --reload")
        return 1

    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            conn.execute(__import__("sqlalchemy").text("SELECT 1"))
        ok("Database connection OK")
    except Exception as exc:
        fail(f"DB connection failed: {exc}")
        return 1

    # ── Phase 2: Issuance ────────────────────────────────────────────────────
    section("Phase 2 — Issuance (real AeroAPI call)")

    payload = {
        "pnr": args.pnr,
        "flight_number": args.flight,
        "flight_date": args.date,
        "name": args.name,
        "phone": args.phone,
        "email": args.email,
        "consent": True,
    }

    try:
        r = httpx.post(f"{base}/policies", json=payload, timeout=30)
        r.raise_for_status()
        issued = r.json()
    except Exception as exc:
        fail(f"POST /policies failed: {exc}")
        if hasattr(exc, "response") and exc.response is not None:  # type: ignore[union-attr]
            info(f"Response body: {exc.response.text}")  # type: ignore[union-attr]
        return 1

    policy_id = issued["policy_id"]
    baseline_state = issued["baseline_state"]
    ok(f"Policy issued — policy_id={policy_id}")
    ok(f"Baseline state = {baseline_state}")

    # Fetch alert_id from DB for synthetic webhook injection.
    from app.models import EventLog, FlightStateRow, Notification, Policy

    with Session(engine) as db:
        fs = db.get(FlightStateRow, policy_id)
        if fs is None:
            fail("FlightStateRow not found in DB after issuance")
            return 1
        alert_id = fs.alert_id
        sta_utc = db.get(Policy, policy_id).scheduled_in_utc
        ok(f"FlightStateRow in DB — alert_id={alert_id}, state={fs.current_state}")

    sta_iso = sta_utc.replace(tzinfo=timezone.utc).isoformat()

    # ── Phase 3: Synthetic webhooks ──────────────────────────────────────────
    section("Phase 3 — Synthetic webhook injection")

    # 3a. +90 min delay → DELAYED_T2
    info("Sending +90 min delay webhook …")
    wh = _make_flight_payload(alert_id, sta_iso, est_delay_min=90, ts_offset_min=10)
    r = _post_webhook(base, wh)
    if r.status_code == 200:
        ok(f"Delay webhook accepted (HTTP {r.status_code})")
    else:
        fail(f"Delay webhook returned HTTP {r.status_code}: {r.text}")

    time.sleep(0.5)  # give the async pipeline a moment to commit

    with Session(engine) as db:
        fs = db.get(FlightStateRow, policy_id)
        if fs.current_state == "DELAYED_T2":
            ok("State → DELAYED_T2 ✓")
        else:
            fail(f"Expected DELAYED_T2, got {fs.current_state}")

    # 3b. Departed +140 min → DEPARTED
    info("Sending departed webhook …")
    wh = _make_flight_payload(alert_id, sta_iso, est_delay_min=140, departed=True, ts_offset_min=60)
    r = _post_webhook(base, wh)
    if r.status_code == 200:
        ok(f"Departed webhook accepted (HTTP {r.status_code})")
    else:
        fail(f"Departed webhook returned HTTP {r.status_code}: {r.text}")

    time.sleep(0.5)

    with Session(engine) as db:
        fs = db.get(FlightStateRow, policy_id)
        if fs.current_state == "DEPARTED":
            ok("State → DEPARTED ✓")
        else:
            fail(f"Expected DEPARTED, got {fs.current_state}")

    # 3c. Landed +138 min → LANDED
    info("Sending landed webhook …")
    wh = _make_flight_payload(alert_id, sta_iso, landed_delay_min=138, ts_offset_min=150)
    r = _post_webhook(base, wh)
    if r.status_code == 200:
        ok(f"Landed webhook accepted (HTTP {r.status_code})")
    else:
        fail(f"Landed webhook returned HTTP {r.status_code}: {r.text}")

    time.sleep(0.5)

    with Session(engine) as db:
        fs = db.get(FlightStateRow, policy_id)
        if fs.current_state == "LANDED":
            ok("State → LANDED ✓")
        else:
            fail(f"Expected LANDED, got {fs.current_state}")

    # ── Phase 4: Assertions ──────────────────────────────────────────────────
    section("Phase 4 — DB assertions")

    with Session(engine) as db:
        logs = db.exec(select(EventLog).where(EventLog.policy_id == policy_id)).all()
        notif_logs = [l for l in logs if l.notified]
        silent_logs = [l for l in logs if not l.notified]

        if len(notif_logs) == 3:
            ok(f"Audit log: exactly 3 notified entries ✓  ({[l.decided_event_type for l in notif_logs]})")
        else:
            fail(f"Expected 3 notified audit entries, got {len(notif_logs)}: {[l.decided_event_type for l in notif_logs]}")

        info(f"Total audit rows: {len(logs)} ({len(silent_logs)} silent + {len(notif_logs)} notified)")

        notifications = db.exec(
            select(Notification).where(Notification.policy_id == policy_id)
        ).all()
        sent = [n for n in notifications if n.status == "sent"]
        failed = [n for n in notifications if n.status == "failed"]

        if len(sent) == 3:
            ok(f"Notification rows: 3 sent ✓  (channels: {[n.channel for n in sent]})")
        elif len(sent) + len(failed) == 3:
            fail(
                f"3 notifications attempted but only {len(sent)} sent, {len(failed)} failed. "
                "Check Twilio/SendGrid credentials."
            )
        else:
            fail(f"Expected 3 notification rows, got {len(notifications)}")

    # ── Phase 5: Summary ─────────────────────────────────────────────────────
    section("Summary")
    total = _pass + _fail
    print(f"  {GREEN if _fail == 0 else RED}{_pass}/{total} checks passed{RESET}")
    if _fail:
        print(f"  {RED}{_fail} failed — see above{RESET}")

    return 0 if _fail == 0 else 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="TripSecure+ Flight Delay — E2E smoke test")
    p.add_argument("--flight", required=True, help="IATA flight number, e.g. AI101")
    p.add_argument("--date", required=True, help="Flight date YYYY-MM-DD (local origin)")
    p.add_argument("--pnr", default="SMOKE01", help="PNR (any string for test)")
    p.add_argument("--name", default="Smoke Test User", help="Passenger name")
    p.add_argument("--phone", required=True, help="E.164 phone number for notifications")
    p.add_argument("--email", default=None, help="Email address (optional)")
    p.add_argument("--base-url", default="http://localhost:8000", help="Server base URL")
    return p.parse_args()


if __name__ == "__main__":
    sys.exit(run(_parse()))
