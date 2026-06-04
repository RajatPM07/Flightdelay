#!/usr/bin/env python
"""
Synthetic end-to-end test for the AeroDataBox FAN-OUT webhook pipeline.

What it proves (without spending API credits or sending real notifications):
  * One inbound AeroDataBox webhook fans out to EVERY active policy on that
    flight number + date — the headline behaviour of the shared-subscription model.
  * Flight-number normalisation: a policy stored as "6E 1341" (with a space) is
    matched by a webhook whose subject normalises to "6E1341".
  * Negative case: a policy on a DIFFERENT date is NOT touched by the webhook.
  * The full route -> verify_secret -> extract_subject -> normalise -> brain ->
    persist -> audit chain, driven through the real `AeroDataBoxProvider`.

How it works:
  - Seeds policies directly in the DB (bypassing issuance, so NO real AeroDataBox
    subscription is created). Policies are seeded with NO contact details, so the
    notifier finds no channel and makes NO real Twilio/SendGrid call — assertions
    are on flight state + the immutable audit log, which are deterministic and free.
  - POSTs synthetic AeroDataBox payloads (built from the real captured fixture) to
    the live `/webhooks/aerodatabox/{secret}` route.
  - Cleans up every row it created at the end.

Prereqs:
  - DB reachable (DATABASE_URL in .env) and the `flight_subscriptions` schema present
    (run once: python -c "import app.models; from app.db import init_db; init_db()").
  - Server running with the AeroDataBox provider selected and a matching secret:
        FLIGHT_PROVIDER=aerodatabox AERODATABOX_WEBHOOK_SECRET=test-secret \
          .venv/bin/python -m uvicorn app.main:app --port 8000
  - Pass the SAME secret here (defaults to $AERODATABOX_WEBHOOK_SECRET, else "test-secret").

Usage:
    python scripts/e2e_aerodatabox.py [--base-url http://localhost:8000] [--secret test-secret]
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Make the project root importable when run as `python scripts/e2e_aerodatabox.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

import httpx
from sqlmodel import Session, create_engine, select, delete

BOLD, GREEN, RED, YELLOW, RESET = "\033[1m", "\033[92m", "\033[91m", "\033[93m", "\033[0m"
_pass = _fail = 0


def ok(m: str) -> None:
    global _pass; _pass += 1; print(f"  {GREEN}✓{RESET}  {m}")


def fail(m: str) -> None:
    global _fail; _fail += 1; print(f"  {RED}✗{RESET}  {m}")


def info(m: str) -> None:
    print(f"  {YELLOW}·{RESET}  {m}")


def section(t: str) -> None:
    print(f"\n{BOLD}── {t} ──{RESET}")


# Arrival is scheduled 20:10Z on the flight date (matches the real fixture).
FLIGHT_DATE = "2026-06-05"
STA = datetime(2026, 6, 5, 20, 10, tzinfo=timezone.utc)


def _adb_ts(dt: datetime) -> str:
    """AeroDataBox datetime format, e.g. '2026-06-05 21:40Z'."""
    return f"{dt:%Y-%m-%d %H:%M}Z"


def _build_payload(fixture: dict, *, revised_min: int, status: str, updated: datetime) -> dict:
    """Clone the real fixture and set a revised arrival, status, and update time."""
    p = copy.deepcopy(fixture)
    revised = STA + timedelta(minutes=revised_min)
    p["status"] = status
    p["arrival"]["revisedTime"] = {"utc": _adb_ts(revised), "local": _adb_ts(revised)}
    p["arrival"]["scheduledTime"] = {"utc": _adb_ts(STA), "local": _adb_ts(STA)}
    p["lastUpdatedUtc"] = _adb_ts(updated)
    return p


def _baseline_snapshot() -> dict:
    """On-time baseline stored on the seeded FlightStateRow (event_ts before all webhooks)."""
    return {
        "event_ts": datetime(2026, 6, 5, 18, 0, tzinfo=timezone.utc).isoformat(),
        "scheduled_in_utc": STA.isoformat(),
        "estimated_in_utc": STA.isoformat(),
        "actual_in_utc": None,
        "departed": False,
        "cancelled": False,
        "diverted": False,
        "content_hash": "baseline",
    }


def run(args: argparse.Namespace) -> int:
    base = args.base_url.rstrip("/")
    secret = args.secret
    db_url = os.getenv("DATABASE_URL", "")

    from app.models import EventLog, FlightStateRow, Notification, Policy, PolicyPII

    section("Phase 1 — Pre-flight")
    if not db_url:
        fail("DATABASE_URL not set"); return 1
    try:
        r = httpx.get(f"{base}/health", timeout=5); r.raise_for_status()
        ok(f"server up — {r.json()}")
    except Exception as e:
        fail(f"server not reachable at {base}: {e}")
        info("start it: FLIGHT_PROVIDER=aerodatabox AERODATABOX_WEBHOOK_SECRET=" + secret
             + " .venv/bin/python -m uvicorn app.main:app --port 8000")
        return 1

    fixture_path = Path(__file__).resolve().parent.parent / "tests/fixtures/aerodatabox_flight.json"
    fixture = json.loads(fixture_path.read_text())
    engine = create_engine(db_url)

    # Three policies: A & B share flight+date (B stored with a space), C is a different date.
    run_tag = uuid.uuid4().hex[:8]
    pid_a, pid_b, pid_c = (f"adbe2e-{run_tag}-{x}" for x in ("a", "b", "c"))
    seeded = [pid_a, pid_b, pid_c]

    def seed(db: Session, pid: str, number: str, date: str) -> None:
        db.add(PolicyPII(policy_id=pid, name="ADB E2E", phone="", email=None))  # no contact => no real send
        db.add(Policy(policy_id=pid, pnr=f"ADB{run_tag}", flight_number=number, flight_date=date,
                      scheduled_in_utc=STA, scheduled_in_tz_offset="+00:00",
                      consent_ts=datetime.now(timezone.utc), status="ON_TIME"))
        db.add(FlightStateRow(policy_id=pid, current_state="ON_TIME", last_known_status=_baseline_snapshot(),
                              last_update_ts=datetime(2026, 6, 5, 18, 0, tzinfo=timezone.utc),
                              last_event_hash="baseline", provider="aerodatabox", alert_id=f"adb-sub-{run_tag}"))

    section("Phase 2 — Seed policies (no real subscription, no contact)")
    try:
        with Session(engine) as db:
            seed(db, pid_a, "6E1341", FLIGHT_DATE)       # exact
            seed(db, pid_b, "6E 1341", FLIGHT_DATE)      # spaced -> must still match
            seed(db, pid_c, "6E1341", "2026-06-06")      # different date -> must NOT match
            db.commit()
        ok(f"seeded A={pid_a} (6E1341), B={pid_b} (6E 1341), C={pid_c} (date 2026-06-06)")
    except Exception as e:
        fail(f"seeding failed: {e}"); return 1

    def post(payload: dict) -> httpx.Response:
        return httpx.post(f"{base}/webhooks/aerodatabox/{secret}", json=payload,
                          headers={"content-type": "application/json"}, timeout=15)

    def state(db: Session, pid: str) -> str:
        return db.get(FlightStateRow, pid).current_state

    try:
        # 3-event sequence: +90 delay -> DELAYED_T2 ; EnRoute +140 -> DEPARTED ; Arrived +138 -> LANDED
        sequence = [
            ("+90 delay", _build_payload(fixture, revised_min=90, status="Expected",
                                         updated=datetime(2026, 6, 5, 19, 0, tzinfo=timezone.utc)), "DELAYED_T2"),
            ("departed +140", _build_payload(fixture, revised_min=140, status="EnRoute",
                                             updated=datetime(2026, 6, 5, 19, 30, tzinfo=timezone.utc)), "DEPARTED"),
            ("landed +138", _build_payload(fixture, revised_min=138, status="Arrived",
                                           updated=datetime(2026, 6, 5, 22, 30, tzinfo=timezone.utc)), "LANDED"),
        ]

        section("Phase 3 — Fire webhooks at the AeroDataBox route")
        for label, payload, expected in sequence:
            info(f"webhook: {label}")
            r = post(payload)
            if r.status_code == 200:
                ok(f"accepted (HTTP 200)")
            else:
                fail(f"HTTP {r.status_code}: {r.text[:200]}")
            time.sleep(0.4)
            with Session(engine) as db:
                sa, sb, sc = state(db, pid_a), state(db, pid_b), state(db, pid_c)
            if sa == expected and sb == expected:
                ok(f"fan-out: A and B → {expected}")
            else:
                fail(f"expected both {expected}; got A={sa} B={sb}")
            if sc == "ON_TIME":
                ok("negative case: C (different date) still ON_TIME")
            else:
                fail(f"C should be ON_TIME, got {sc}")

        section("Phase 4 — Audit assertions")
        with Session(engine) as db:
            for pid, name in ((pid_a, "A"), (pid_b, "B")):
                logs = db.exec(select(EventLog).where(EventLog.policy_id == pid)).all()
                notified = [l for l in logs if l.notified]
                if len(notified) == 3:
                    ok(f"{name}: 3 notified audit rows {[l.decided_event_type for l in notified]}")
                else:
                    fail(f"{name}: expected 3 notified rows, got {len(notified)}")
            c_logs = db.exec(select(EventLog).where(EventLog.policy_id == pid_c)).all()
            if not any(l.notified for l in c_logs):
                ok("C: no notified audit rows (correctly skipped)")
            else:
                fail(f"C: unexpected notified rows: {len(c_logs)}")
    finally:
        section("Cleanup")
        try:
            with Session(engine) as db:
                for model in (Notification, EventLog, FlightStateRow, Policy, PolicyPII):
                    db.exec(delete(model).where(model.policy_id.in_(seeded)))
                db.commit()
            ok("removed all rows created by this run")
        except Exception as e:
            info(f"cleanup warning: {e}")

    section("Summary")
    total = _pass + _fail
    print(f"  {GREEN if _fail == 0 else RED}{_pass}/{total} checks passed{RESET}")
    return 0 if _fail == 0 else 1


def _parse() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="AeroDataBox synthetic fan-out E2E")
    p.add_argument("--base-url", default="http://localhost:8000")
    p.add_argument("--secret", default=os.getenv("AERODATABOX_WEBHOOK_SECRET", "test-secret"))
    return p.parse_args()


if __name__ == "__main__":
    sys.exit(run(_parse()))
