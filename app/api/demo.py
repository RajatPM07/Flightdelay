"""Demo UI: the live-pipeline page and the synthetic-event simulator.

Both routes are gated behind DEMO_MODE so production deploys expose neither.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.config import settings

router = APIRouter(tags=["demo"])

_PAGE = Path(__file__).resolve().parent.parent / "static" / "demo.html"


def _require_demo() -> None:
    if not settings.demo_mode:
        raise HTTPException(status_code=404, detail="Not found")


@router.get("/")
async def demo_page() -> FileResponse:
    _require_demo()
    return FileResponse(_PAGE, media_type="text/html")
