"""Twilio adapter for WhatsApp + SMS. Implements MessageProvider."""
from __future__ import annotations

from app.providers.messaging.base import Channel, MessageProvider


class TwilioProvider(MessageProvider):
    def __init__(self, account_sid: str, auth_token: str, whatsapp_from: str, sms_from: str):
        self.account_sid = account_sid
        self.auth_token = auth_token
        self.whatsapp_from = whatsapp_from
        self.sms_from = sms_from

    async def send(self, channel: Channel, to: str, body: str, subject: str | None = None) -> str:
        raise NotImplementedError("Milestone 6.")
