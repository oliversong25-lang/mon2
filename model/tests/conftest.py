from __future__ import annotations

import pytest

from business_cycle.config import Settings, load_settings
from business_cycle.synthetic import generate_synthetic_observations


@pytest.fixture(scope="session")
def settings() -> Settings:
    return load_settings()


@pytest.fixture(scope="session")
def synthetic_data():
    return generate_synthetic_observations("1990-01-01", "2026-08-14", seed=42)
