from __future__ import annotations

import numpy as np
import pandas as pd

from business_cycle.data.availability import apply_availability_dates
from business_cycle.models.composite import CompositeFactorModel
from business_cycle.preprocessing.frequency import weekly_event_matrix
from business_cycle.preprocessing.standardize import causal_robust_standardize
from business_cycle.preprocessing.transforms import (
    periods_for_years,
    transform_observations,
)


def test_three_year_weekly_horizon_is_156_observations() -> None:
    assert periods_for_years("weekly", 3) == 156


def test_three_year_monthly_horizon_is_36_observations() -> None:
    assert periods_for_years("monthly", 3) == 36


def test_three_year_quarterly_horizon_is_12_observations() -> None:
    assert periods_for_years("quarterly", 3) == 12


def test_unknown_frequency_is_rejected() -> None:
    try:
        periods_for_years("daily", 3)
    except ValueError as exc:
        assert "daily" in str(exc)
    else:
        raise AssertionError("unknown frequency must not silently pick a unit")


def test_robust_center_excludes_current_observation() -> None:
    series = pd.Series([1.0, 2.0, 3.0, 100.0])
    result = causal_robust_standardize(series, 3, 3, 6)
    assert result.loc[3, "robust_center"] == 2.0


def test_robust_standardization_does_not_use_future_values() -> None:
    base = pd.Series(np.linspace(1.0, 100.0, 100))
    changed = base.copy()
    changed.iloc[70:] = 1_000_000.0
    left = causal_robust_standardize(base, 30, 10, 6)
    right = causal_robust_standardize(changed, 30, 10, 6)
    pd.testing.assert_series_equal(
        left.loc[:69, "postclip_signal"], right.loc[:69, "postclip_signal"]
    )


def test_mad_zero_falls_back_to_iqr() -> None:
    series = pd.Series([0.0] * 8 + [1.0, 2.0, 10.0])
    result = causal_robust_standardize(series, 10, 5, 6)
    assert np.isfinite(result.loc[10, "robust_scale"])
    assert result.loc[10, "robust_scale"] > 0


def test_mad_and_iqr_zero_fall_back_to_standard_deviation() -> None:
    series = pd.Series([0.0] * 9 + [1.0, 10.0])
    result = causal_robust_standardize(series, 10, 5, 6)
    assert np.isfinite(result.loc[10, "robust_scale"])
    assert result.loc[10, "robust_scale"] > 0


def test_positive_extreme_is_clipped() -> None:
    series = pd.Series([float(value % 5) for value in range(50)] + [1_000.0])
    result = causal_robust_standardize(series, 40, 20, 6)
    assert result.iloc[-1]["preclip_signal"] > 6
    assert result.iloc[-1]["postclip_signal"] == 6


def test_negative_extreme_is_clipped() -> None:
    series = pd.Series([float(value % 5) for value in range(50)] + [-1_000.0])
    result = causal_robust_standardize(series, 40, 20, 4)
    assert result.iloc[-1]["postclip_signal"] == -4


def test_clip_sensitivity_respects_requested_bound() -> None:
    series = pd.Series([float(value % 7) for value in range(60)] + [10_000.0])
    for bound in (4.0, 6.0, 8.0):
        result = causal_robust_standardize(series, 50, 20, bound)
        assert result.iloc[-1]["postclip_signal"] == bound


def test_original_signal_is_not_overwritten_by_clipping() -> None:
    series = pd.Series([float(value % 5) for value in range(50)] + [1_000.0])
    result = causal_robust_standardize(series, 40, 20, 6)
    assert result.iloc[-1]["original_signal"] == 1_000.0


def _maturity_model(settings) -> CompositeFactorModel:
    return CompositeFactorModel(
        settings.indicators["indicators"],
        settings.indicators["constraints"],
        {"enabled": True, "exclude_years": 5, "full_weight_years": 10},
    )


def _events_with_start(settings, date: str) -> pd.DataFrame:
    columns = list(settings.indicators["indicators"])
    index = pd.DatetimeIndex([pd.Timestamp(date)])
    events = pd.DataFrame(1.0, index=index, columns=columns)
    events.attrs["first_available"] = {column: pd.Timestamp("2000-01-07") for column in columns}
    return events


def test_maturity_excludes_history_under_five_years(settings) -> None:
    events = _events_with_start(settings, "2004-12-31")
    maturity = _maturity_model(settings)._maturity_weights(events, events.index[0])
    assert maturity.eq(0.0).all()


def test_maturity_ramps_between_five_and_ten_years(settings) -> None:
    events = _events_with_start(settings, "2007-07-06")
    maturity = _maturity_model(settings)._maturity_weights(events, events.index[0])
    assert maturity.between(0.49, 0.51).all()


def test_maturity_reaches_full_weight_after_ten_years(settings) -> None:
    events = _events_with_start(settings, "2010-01-08")
    maturity = _maturity_model(settings)._maturity_weights(events, events.index[0])
    assert maturity.eq(1.0).all()


def test_maturity_uses_each_indicators_own_start(settings) -> None:
    events = _events_with_start(settings, "2010-01-08")
    events.attrs["first_available"]["ICSA"] = pd.Timestamp("2007-01-05")
    maturity = _maturity_model(settings)._maturity_weights(events, events.index[0])
    assert maturity["PAYEMS"] == 1.0
    assert maturity["ICSA"] == 0.0


def test_caps_hold_after_maturity_weighting(settings) -> None:
    events = _events_with_start(settings, "2010-01-08")
    estimate = _maturity_model(settings).fit_filter(events)
    weights = estimate.metadata["effective_weights"][events.index[0]]
    assert max(weights.values()) <= 0.200000001
    domains: dict[str, float] = {}
    for indicator, weight in weights.items():
        domain = settings.indicators["indicators"][indicator]["domain"]
        domains[domain] = domains.get(domain, 0.0) + weight
    assert max(domains.values()) <= 0.300000001


def test_frequency_audit_is_preserved_before_weekly_alignment(settings, synthetic_data) -> None:
    available, _ = apply_availability_dates(synthetic_data, settings.indicators["indicators"])
    transformed = transform_observations(
        available,
        settings.indicators["indicators"],
        trend_horizon_years=3,
        standardization_method="rolling_median_mad",
        standardization_horizon_years=10,
        standardization_min_history_years=5,
        robust_clip=6,
    )
    monthly = transformed[transformed["indicator_id"].eq("INDPRO")]
    weekly = transformed[transformed["indicator_id"].eq("ICSA")]
    assert monthly["trend_span_observations"].eq(36).all()
    assert weekly["trend_span_observations"].eq(156).all()


def test_weekly_matrix_keeps_first_raw_availability(settings, synthetic_data) -> None:
    available, _ = apply_availability_dates(synthetic_data, settings.indicators["indicators"])
    transformed = transform_observations(
        available,
        settings.indicators["indicators"],
        trend_horizon_years=3,
        standardization_method="rolling_median_mad",
        standardization_min_history_years=5,
        robust_clip=6,
    )
    events = weekly_event_matrix(transformed)
    expected = transformed.groupby("indicator_id")["available_week"].min()["INDPRO"]
    assert events.attrs["first_available"]["INDPRO"] == expected


def test_robust_transformation_is_reproducible(settings, synthetic_data) -> None:
    available, _ = apply_availability_dates(synthetic_data, settings.indicators["indicators"])
    kwargs = {
        "trend_horizon_years": 3,
        "standardization_method": "rolling_median_mad",
        "standardization_min_history_years": 5,
        "robust_clip": 6,
    }
    left = transform_observations(available, settings.indicators["indicators"], **kwargs)
    right = transform_observations(available, settings.indicators["indicators"], **kwargs)
    pd.testing.assert_series_equal(left["signal"], right["signal"])


def test_multidomain_shock_survives_bounded_influence(settings) -> None:
    events = _events_with_start(settings, "2010-01-08")
    events.loc[:, :] = -100.0
    estimate = _maturity_model(settings).fit_filter(events)
    contributions = estimate.contributions.iloc[0]
    active_domains = {
        settings.indicators["indicators"][indicator]["domain"]
        for indicator in contributions[contributions < 0].index
    }
    assert len(active_domains) >= 4


def test_composite_records_contribution_stage_clipping(settings) -> None:
    events = _events_with_start(settings, "2010-01-08")
    events.loc[events.index[0], "INDPRO"] = -23.0
    estimate = CompositeFactorModel(
        settings.indicators["indicators"],
        settings.indicators["constraints"],
        robust_clip=6.0,
    ).fit_filter(events)
    assert estimate.metadata["clipped_observation_count"] == 1
    assert estimate.metadata["clipping_events"][0]["indicator_id"] == "INDPRO"
    assert estimate.metadata["clipping_events"][0]["postclip"] == -6.0
    assert estimate.metadata["total_limited_amount"] == 17.0
