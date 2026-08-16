from __future__ import annotations

import numpy as np
import pandas as pd

from business_cycle.models.momentum import coordinates
from business_cycle.pipeline import run_pipeline
from business_cycle.reporting.writers import validate_result


def test_pipeline_smoke_and_json_schema(settings, synthetic_data):
    run = run_pipeline(synthetic_data, settings, "2026-08-14")
    payload = run.result.to_dict()
    validate_result(payload)
    assert payload["current_phase"]["code"]
    assert len(payload["phase_probabilities"]) == 12
    assert np.isclose(sum(item["probability"] for item in payload["phase_probabilities"]), 1.0)
    assert payload["forecast_13w"]["status"] == "not_calibrated"
    assert payload["metadata"]["uses_backward_smoothing"] is False
    expected = coordinates(
        run.composite,
        int(settings.model["momentum_weeks"]),
        int(settings.model["standardization_min_periods"]),
    ).dropna()
    pd.testing.assert_series_equal(run.history["x"], expected.loc[run.history.index, "x"])
    assert payload["metadata"]["representative_model"] == "CompositeFactorModel"


def test_same_seed_is_reproducible(settings):
    from business_cycle.synthetic import generate_synthetic_observations

    left = generate_synthetic_observations("1990-01-01", "2020-12-31", 7)
    right = generate_synthetic_observations("1990-01-01", "2020-12-31", 7)
    run_left = run_pipeline(left, settings, "2020-12-31")
    run_right = run_pipeline(right, settings, "2020-12-31")
    assert run_left.result.to_dict() == run_right.result.to_dict()


def test_missing_indicators_can_withhold_instead_of_inventing_result(settings, synthetic_data):
    sparse = synthetic_data[synthetic_data["indicator_id"].isin(["PAYEMS", "ICSA"])].copy()
    run = run_pipeline(sparse, settings, "2026-08-14")
    assert run.result.status == "withheld"
    assert any("판정 보류" in warning for warning in run.result.warnings)
