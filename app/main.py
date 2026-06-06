"""FastAPI entrypoint. Wires routers and the in-process APScheduler."""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api import demo, live, policies, webhooks
from app.config import settings
from app.db import init_db
from app.services.scheduler import scheduler

# Configure application logging to print to stdout formatted like uvicorn
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(levelname)s:     [%(name)s] %(message)s",
)



@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="TripSecure+ Flight Delay MVP", version="0.1.0", lifespan=lifespan)

# Serve ONLY static assets (logo/fonts) — NOT the HTML pages, which stay gated behind
# their demo-mode routes so production never exposes the demo UI.
_ASSETS_DIR = Path(__file__).resolve().parent / "static" / "assets"
app.mount("/static/assets", StaticFiles(directory=str(_ASSETS_DIR)), name="assets")

app.include_router(demo.router)
app.include_router(live.router)
app.include_router(policies.router)
app.include_router(webhooks.router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "tripsecure-flightdelay-mvp", "scope": "monitoring-only"}
