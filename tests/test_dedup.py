"""Dedup + out-of-order: a stale or duplicate event must never notify or regress state."""
from __future__ import annotations

import pytest

# Milestone 2: assert that an incoming event with event_ts <= last, or an identical content_hash,
# yields Decision(should_notify=False, event_type=NONE) and leaves state unchanged.
