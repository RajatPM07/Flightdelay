"""MessageProvider interface. Swap Twilio -> Gupshup / Meta WhatsApp Cloud API later with a new
implementation and zero brain changes."""
from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum


class Channel(str, Enum):
    WHATSAPP = "whatsapp"
    SMS = "sms"
    EMAIL = "email"


class MessageProvider(ABC):
    @abstractmethod
    async def send(self, channel: Channel, to: str, body: str, subject: str | None = None) -> str:
        """Send a message. Returns the provider message id. Must be idempotent at the caller."""
