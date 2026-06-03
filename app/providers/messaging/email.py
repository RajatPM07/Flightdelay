"""Email adapter (SendGrid or SMTP). Implements MessageProvider for Channel.EMAIL."""
from __future__ import annotations

from app.providers.messaging.base import Channel, MessageProvider


class EmailProvider(MessageProvider):
    def __init__(self, sendgrid_api_key: str, email_from: str):
        self.sendgrid_api_key = sendgrid_api_key
        self.email_from = email_from

    async def send(self, channel: Channel, to: str, body: str, subject: str | None = None) -> str:
        raise NotImplementedError("Milestone 6.")
