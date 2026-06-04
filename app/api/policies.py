"""Policy API: POST /policies (issuance) and GET /policies/{id} (status read model)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from app.deps import get_brain_config, get_db, get_flight_provider
from app.domain.states import BrainConfig
from app.models import EventLog, FlightStateRow, Notification, Policy, PolicyPII
from app.providers.flightdata.base import FlightDataProvider
from app.services.issuance import issue_policy

router = APIRouter(prefix="/policies", tags=["issuance"])
logger = logging.getLogger(__name__)


class CreatePolicyRequest(BaseModel):
    pnr: str
    flight_number: str
    flight_date: str            # YYYY-MM-DD (local origin date)
    name: str
    phone: str
    email: str | None = None
    consent: bool               # must be True; affirmative, not pre-ticked (DPDP)


class CreatePolicyResponse(BaseModel):
    policy_id: str
    monitoring_active: bool
    baseline_state: str


@router.post("", response_model=CreatePolicyResponse, status_code=201)
async def create_policy(
    req: CreatePolicyRequest,
    db: Session = Depends(get_db),
    provider: FlightDataProvider = Depends(get_flight_provider),
    config: BrainConfig = Depends(get_brain_config),
) -> CreatePolicyResponse:
    """
    Issue a new flight-delay monitoring policy.

    - `consent` must be True (affirmative opt-in, DPDP §6).
    - PII (name/phone/email) is split into an isolated table immediately;
      only the anonymous `policy_id` token is returned and used downstream.
    - The baseline flight status is fetched from FlightAware and stored.
    - An AeroAPI push-alert subscription is registered before this call returns.
    """
    try:
        policy_id, baseline_state = await issue_policy(
            pnr=req.pnr,
            flight_number=req.flight_number,
            flight_date=req.flight_date,
            name=req.name,
            phone=req.phone,
            email=req.email,
            consent=req.consent,
            db=db,
            provider=provider,
            config=config,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    return CreatePolicyResponse(
        policy_id=policy_id,
        monitoring_active=True,
        baseline_state=baseline_state,
    )


# ---------------------------------------------------------------------------
# Read model helpers
# ---------------------------------------------------------------------------

def _aware(dt: datetime | None) -> datetime | None:
    """Return *dt* as a tz-aware datetime (UTC assumed when naive)."""
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _mask_name(name: str) -> str:
    parts = (name or "").strip().split()
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return f"{parts[0]} {parts[-1][0]}."


def _mask_phone(phone: str) -> str:
    # Assumes Indian E.164 (+91…) — product is India-only (ICICI Lombard, Product 4233).
    # The country-code split is tuned for +91; middle masking is safe for any input.
    p = (phone or "").strip()
    if not p:
        return ""
    digits = p.lstrip("+")
    if len(digits) < 4:
        return ""
    # E.164 like +919876543299 -> "+91 98••••99": country(2)+space+next2 ... last2
    cc, rest = digits[:2], digits[2:]
    return f"+{cc} {rest[:2]}••••{rest[-2:]}"


@router.get("/{policy_id}")
async def policy_status(policy_id: str, db: Session = Depends(get_db)) -> dict:
    policy = db.get(Policy, policy_id)
    fs = db.get(FlightStateRow, policy_id)
    if policy is None or fs is None:
        raise HTTPException(status_code=404, detail="policy not found")
    pii = db.get(PolicyPII, policy_id)
    logs = db.exec(select(EventLog).where(EventLog.policy_id == policy_id)).all()
    logs = sorted(logs, key=lambda l: l.received_ts, reverse=True)
    notifs = db.exec(select(Notification).where(Notification.policy_id == policy_id)).all()
    notifs = sorted(
        notifs,
        key=lambda n: (n.sent_ts is None, _aware(n.sent_ts) or datetime.min.replace(tzinfo=timezone.utc)),
    )
    if pii is None:
        logger.warning("policy_status.pii_missing", extra={"policy_id": policy_id})
    return {
        "policy_id": policy_id,
        "flight_number": policy.flight_number,
        "flight_date": policy.flight_date,
        "current_state": fs.current_state,
        "scheduled_in_utc": policy.scheduled_in_utc.isoformat() if policy.scheduled_in_utc else None,
        "last_update_ts": fs.last_update_ts.isoformat() if fs.last_update_ts else None,
        "contact": {
            "name": _mask_name(pii.name) if pii else "",
            "phone_masked": _mask_phone(pii.phone) if pii else "",
        },
        "timeline": [
            {
                "ts": l.received_ts.isoformat() if l.received_ts else None,
                "prev_state": l.prev_state,
                "new_state": l.new_state,
                "event_type": l.decided_event_type,
                "notified": l.notified,
            }
            for l in logs
        ],
        "notifications": [
            {
                "channel": n.channel,
                "body": n.body,
                "status": n.status,
                "sent_ts": n.sent_ts.isoformat() if n.sent_ts else None,
            }
            for n in notifs
        ],
    }
