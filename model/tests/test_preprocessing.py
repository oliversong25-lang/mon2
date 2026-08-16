from __future__ import annotations

import numpy as np
import pandas as pd

from business_cycle.data.availability import apply_availability_dates
from business_cycle.models.composite import CompositeFactorModel
from business_cycle.preprocessing.frequency import weekly_event_matrix
from business_cycle.preprocessing.standardize import causal_standardize
from business_cycle.preprocessing.transforms import transform_observations, transform_series


def test_standardization_does_not_use_future_values():
    base = pd.Series(np.linspace(1, 100, 100))
    changed = base.copy()
    changed.iloc[70:] = 1_000_000
    left = causal_standardize(base, min_periods=10)
    right = causal_standardize(changed, min_periods=10)
    pd.testing.assert_series_equal(left.iloc[:70], right.iloc[:70])


def test_monthly_observation_is_not_duplicated_in_unreleased_weeks(settings, synthetic_data):
    config = settings.indicators["indicators"]
    available, _ = apply_availability_dates(synthetic_data, config)
    transformed = transform_observations(available, config, 156, 26)
    events = weekly_event_matrix(transformed)
    monthly = "INDPRO"
    source_count = transformed.loc[
        (transformed["indicator_id"] == monthly) & transformed["signal"].notna()
    ].shape[0]
    assert events[monthly].notna().sum() == source_count
    assert events[monthly].isna().sum() > events[monthly].notna().sum()


def test_weight_renormalization_when_indicator_missing(settings):
    index = pd.date_range("2020-01-03", periods=3, freq="W-FRI")
    events = pd.DataFrame(
        {
            "PAYEMS": [1.0, 1.0, 1.0],
            "W875RX1": [1.0, 1.0, 1.0],
            "INDPRO": [np.nan, 2.0, 2.0],
            "CMRMTSPL": [1.0, 1.0, 1.0],
            "RRSFS": [1.0, 1.0, 1.0],
            "ICSA": [1.0, 1.0, 1.0],
            "CCSA": [1.0, 1.0, 1.0],
        },
        index=index,
    )
    model = CompositeFactorModel(
        settings.indicators["indicators"], settings.indicators["constraints"]
    )
    estimate = model.fit_filter(events)
    assert np.isclose(estimate.factor.iloc[0], 1.0)
    assert estimate.metadata["renormalized_weeks"] >= 1
    weights = estimate.metadata["effective_weights"][index[0]]
    assert np.isclose(sum(weights.values()), 1.0)
    assert max(weights.values()) <= settings.indicators["constraints"]["max_indicator_weight"]


def test_inverse_indicator_sign_is_applied():
    values = pd.Series(np.exp(np.linspace(10, 11, 120)))
    normal = transform_series(values, "claims_4w_log", 1, 24, 10)
    inverse = transform_series(values, "claims_4w_log", -1, 24, 10)
    valid = normal.notna() & inverse.notna()
    assert np.allclose(normal[valid], -inverse[valid])
