"""FastAPI entrypoint. Wires routers and the scheduler. /health is the only live endpoint
until the milestones are built."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import policies, webhooks
from app.db import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # Milestone 7: start APScheduler (STA+24h backstop + deregistration sweep) here.
    yield


app = FastAPI(title="TripSecure+ Flight Delay MVP", version="0.1.0", lifespan=lifespan)
app.include_router(policies.router)
app.include_router(webhooks.router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "tripsecure-flightdelay-mvp", "scope": "monitoring-only"}
