"""
SQLModel tables. PII ISOLATION IS MANDATORY (DPDP): only PolicyPII holds name/phone/email.
Every other table references the anonymised policy_id token only.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel, JSON, Column


class PolicyPII(SQLModel, table=True):
    __tablename__ = "policy_pii"
    # TODO(milestone 1): encrypt at rest (column-level / KMS). Do not store plaintext in prod.
    policy_id: str = Field(primary_key=True)
    name: str
    phone: str
    email: Optional[str] = None


class Policy(SQLModel, table=True):
    __tablename__ = "policies"
    policy_id: str = Field(primary_key=True)        # anonymised token, used everywhere downstream
    pnr: str
    flight_number: str
    flight_date: str                                 # YYYY-MM-DD (local to origin)
    scheduled_in_utc: datetime
    scheduled_in_tz_offset: str                      # e.g. "+04:00" — keep the original offset
    consent_ts: datetime
    status: str = "REGISTERED"
    created_at: datetime = Field(default_factory=datetime.utcnow)


class FlightStateRow(SQLModel, table=True):
    __tablename__ = "flight_state"
    policy_id: str = Field(primary_key=True)
    current_state: str
    last_known_status: dict = Field(default_factory=dict, sa_column=Column(JSON))
    last_update_ts: Optional[datetime] = None
    last_event_hash: Optional[str] = None
    provider: str = "flightaware"
    alert_id: Optional[str] = None


class EventLog(SQLModel, table=True):
    """Immutable, append-only. DPDP audit today; Phase-2 payout evidence tomorrow."""
    __tablename__ = "event_log"
    id: Optional[int] = Field(default=None, primary_key=True)
    policy_id: str = Field(index=True)
    received_ts: datetime = Field(default_factory=datetime.utcnow)
    source: str
    raw_payload: dict = Field(default_factory=dict, sa_column=Column(JSON))
    prev_state: Optional[str] = None
    new_state: Optional[str] = None
    decided_event_type: Optional[str] = None
    notified: bool = False
    notification_id: Optional[str] = None


class Notification(SQLModel, table=True):
    __tablename__ = "notifications"
    id: Optional[str] = Field(default=None, primary_key=True)
    policy_id: str = Field(index=True)
    channel: str
    template: str
    body: str
    provider_msg_id: Optional[str] = None
    status: str = "pending"
    sent_ts: Optional[datetime] = None


class FlightSubscription(SQLModel, table=True):
    __tablename__ = "flight_subscriptions"
    # Keyed by the normalised flight number — one shared subscription per number.
    subject_key: str = Field(primary_key=True)
    provider: str
    subscription_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
