"""Demo UI: the live-pipeline page and the synthetic-event simulator.

Both routes are gated behind DEMO_MODE so production deploys expose neither.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import Session

from app.config import settings
from app.deps import get_brain_config, get_db, get_flight_provider, get_msg_provider
from app.domain.states import BrainConfig
from app.services.simulator import simulate_event

router = APIRouter(tags=["demo"])

_STATIC = Path(__file__).resolve().parent.parent / "static"
_PAGE = _STATIC / "demo.html"
_LANDING = _STATIC / "landing.html"


def _require_demo() -> None:
    if not settings.demo_mode:
        raise HTTPException(status_code=404, detail="Not found")


@router.get("/")
async def demo_page() -> FileResponse:
    _require_demo()
    return FileResponse(_PAGE, media_type="text/html")


@router.get("/landing")
async def landing_page() -> FileResponse:
    _require_demo()
    return FileResponse(_LANDING, media_type="text/html")


class SimulateRequest(BaseModel):
    policy_id: str
    event: str


@router.post("/demo/simulate")
async def demo_simulate(
    req: SimulateRequest,
    db: Session = Depends(get_db),
    provider=Depends(get_flight_provider),
    config: BrainConfig = Depends(get_brain_config),
    msg_provider=Depends(get_msg_provider),
) -> dict:
    _require_demo()
    try:
        return await simulate_event(
            policy_id=req.policy_id,
            event=req.event,
            db=db,
            provider=provider,
            config=config,
            msg_provider=msg_provider,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
