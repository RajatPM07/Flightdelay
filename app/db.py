"""Database engine + session. MVP uses create_all; switch to Alembic migrations later."""
from __future__ import annotations

from sqlmodel import Session, SQLModel, create_engine

from app.config import settings

engine = create_engine(
    settings.database_url or "sqlite:///./local_dev.sqlite3",
    echo=False,
    pool_pre_ping=True,
)


def init_db() -> None:
    SQLModel.metadata.create_all(engine)


def get_session() -> Session:
    return Session(engine)
