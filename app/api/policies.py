"""Issuance API. POST /policies -> create policy, baseline snapshot, register alert."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from app.deps import get_brain_config, get_db, get_flight_provider
from app.domain.states import BrainConfig
from app.providers.flightdata.base import FlightDataProvider
from app.services.issuance import issue_policy

router = APIRouter(prefix="/policies", tags=["issuance"])


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
