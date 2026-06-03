"""Notifier: maps a brain Decision/EventType to a template + channel and sends via MessageProvider.
Idempotent: never send the same notification twice for the same (policy_id, event)."""
from __future__ import annotations

# Milestone 6. Templates per EventType (delay tiers, back-on-schedule, departed, landed,
# cancelled, diverted). No payment language anywhere.
