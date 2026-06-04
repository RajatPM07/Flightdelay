"""Twilio adapter for WhatsApp + SMS. Uses httpx (Twilio REST API) — no sync SDK in async path."""
from __future__ import annotations

import logging

import httpx

from app.providers.messaging.base import Channel, MessageProvider

logger = logging.getLogger(__name__)

_TWILIO_API = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"


class TwilioProvider(MessageProvider):
    def __init__(self, account_sid: str, auth_token: str, whatsapp_from: str, sms_from: str):
        self.account_sid = account_sid
        self.auth_token = auth_token
        self.whatsapp_from = whatsapp_from
        self.sms_from = sms_from

    async def send(self, channel: Channel, to: str, body: str, subject: str | None = None) -> str:
        """
        Send via Twilio Messages API.
        WhatsApp: From/To prefixed with "whatsapp:".
        SMS: plain E.164 numbers.
        Returns the Twilio message SID.
        """
        if channel == Channel.WHATSAPP:
            # Idempotent prefixing — the configured number may or may not already
            # carry the "whatsapp:" scheme (the .env convention varies).
            from_addr = f"whatsapp:{self.whatsapp_from.removeprefix('whatsapp:')}"
            to_addr = f"whatsapp:{to.removeprefix('whatsapp:')}"
        else:
            from_addr = self.sms_from
            to_addr = to

        url = _TWILIO_API.format(sid=self.account_sid)
        logger.info("twilio.send", extra={"channel": channel.value, "to": to_addr[:6] + "****"})

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                url,
                auth=(self.account_sid, self.auth_token),
                data={"From": from_addr, "To": to_addr, "Body": body},
            )
            resp.raise_for_status()

        sid = resp.json()["sid"]
        logger.info("twilio.send.ok", extra={"sid": sid})
        return sid
