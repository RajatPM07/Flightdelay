"""Email adapter via SendGrid REST API. Uses httpx — no sync SDK in async path."""
from __future__ import annotations

import logging
import uuid

import httpx

from app.providers.messaging.base import Channel, MessageProvider

logger = logging.getLogger(__name__)

_SENDGRID_API = "https://api.sendgrid.com/v3/mail/send"


class EmailProvider(MessageProvider):
    def __init__(self, sendgrid_api_key: str, email_from: str):
        self.sendgrid_api_key = sendgrid_api_key
        self.email_from = email_from

    async def send(self, channel: Channel, to: str, body: str, subject: str | None = None) -> str:
        """
        Send a plain-text email via SendGrid.
        Returns the SendGrid X-Message-Id header (or a generated UUID on 202 with no header).
        """
        payload = {
            "personalizations": [{"to": [{"email": to}]}],
            "from": {"email": self.email_from},
            "subject": subject or "Flight Update — TripSecure+",
            "content": [{"type": "text/plain", "value": body}],
        }

        logger.info("sendgrid.send", extra={"to": to})

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                _SENDGRID_API,
                headers={"Authorization": f"Bearer {self.sendgrid_api_key}"},
                json=payload,
            )
            resp.raise_for_status()

        # SendGrid returns 202 Accepted with no JSON body.
        msg_id = resp.headers.get("x-message-id", uuid.uuid4().hex)
        logger.info("sendgrid.send.ok", extra={"msg_id": msg_id})
        return msg_id
