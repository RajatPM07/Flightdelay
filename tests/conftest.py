"""Shared test fixtures. The brain tests run with NO database and NO network."""
from __future__ import annotations

import pytest

from app.domain.brain import BrainConfig


@pytest.fixture
def config() -> BrainConfig:
    # Mirrors .env defaults; tests must not depend on real settings.
    return BrainConfig(delay_t1_min=30, delay_t2_min=60, delay_t3_min=120, recovery_buffer_min=15)
