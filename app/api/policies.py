"""Issuance API. POST /policies -> create policy, baseline snapshot, register alert."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/policies", tags=["issuance"])


class CreatePolicyRequest(BaseModel):
    pnr: str
    flight_number: str
    flight_date: str          # YYYY-MM-DD
    name: str
    phone: str
    email: str | None = None
    consent: bool             # must be True; standalone, not pre-ticked (DPDP)


class CreatePolicyResponse(BaseModel):
    policy_id: str
    monitoring_active: bool
    baseline_state: str


@router.post("", response_model=CreatePolicyResponse)
async def create_policy(req: CreatePolicyRequest) -> CreatePolicyResponse:
    # Milestone 4: validate consent; split PII into policy_pii; take baseline snapshot via
    # FlightDataProvider; register the alert; persist Policy + FlightStateRow; return token.
    raise NotImplementedError("Milestone 4.")
