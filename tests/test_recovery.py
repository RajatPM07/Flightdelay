"""Hysteresis: a delayed flight only fires BACK_ON_SCHEDULE below (T1 - recovery_buffer),
and a same-tier wobble never re-notifies."""
from __future__ import annotations

import pytest

# Milestone 2.
