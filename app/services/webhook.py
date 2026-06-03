"""Webhook service: normalise -> load state -> brain.decide() -> persist -> (maybe) notify.
Idempotent on retried deliveries."""
from __future__ import annotations

# Milestone 5.
