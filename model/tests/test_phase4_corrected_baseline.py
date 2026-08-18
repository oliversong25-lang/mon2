"""단계 A-2 corrected baseline의 필수 정확성과 시작시점 불변성."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from business_cycle.config import load_baseline
from business_cycle.data.availability import apply_availability_dates
from business_cycle.models.momentum import coordinates
from business_cycle.models.transition import filter_probabilities, transition_matrix
from business_cycle.preprocessing.standardize import causal_rolling_standardize_details
from business_cycle.preprocessing.transforms import periods_for_years, transform_observations


def _monthly(values: list[float], start: str = "1990-01-31") -> pd.Series:
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq="ME"))


# ── 창 단위 ──────────────────────────────────────────────────────────────────


def test_ten_year_window_is_120_monthly_observations() -> None:
    assert periods_for_years("monthly", 10) == 120


def test_ten_year_window_is_520_weekly_observations() -> None:
    assert periods_for_years("weekly", 10) == 520


def test_rolling_window_audit_records_actual_span_and_count() -> None:
    series = _monthly([float(index) for index in range(200)])
    details = causal_rolling_standardize_details(series, 120, 60)
    mature = details.dropna(subset=["window_start", "window_end"]).iloc[-1]
    # 창은 현재 관측 직전까지이며 정확히 120개 관측을 담는다.
    assert mature["window_observations"] == 120
    span_days = (pd.Timestamp(mature["window_end"]) - pd.Timestamp(mature["window_start"])).days
    assert 10 * 365 - 40 <= span_days <= 10 * 365 + 40


# ── 인과성과 창 밖 불변성 ────────────────────────────────────────────────────


def test_current_observation_is_excluded_from_center_and_scale() -> None:
    base = _monthly([float(index % 7) for index in range(200)])
    changed = base.copy()
    changed.iloc[-1] = 10_000.0
    left = causal_rolling_standardize_details(base, 120, 60)
    right = causal_rolling_standardize_details(changed, 120, 60)
    assert left["rolling_center"].iloc[-1] == right["rolling_center"].iloc[-1]
    assert left["rolling_scale"].iloc[-1] == right["rolling_scale"].iloc[-1]


def test_changes_outside_the_ten_year_window_do_not_move_the_current_value() -> None:
    base = _monthly([float(index % 11) for index in range(300)])
    changed = base.copy()
    # 마지막 관측 기준 창은 직전 120개다. 그보다 앞을 아무리 바꿔도 현재값은 그대로여야 한다.
    changed.iloc[:150] = changed.iloc[:150] + 5_000.0
    left = causal_rolling_standardize_details(base, 120, 60)
    right = causal_rolling_standardize_details(changed, 120, 60)
    assert left["postclip_signal"].iloc[-1] == pytest.approx(right["postclip_signal"].iloc[-1])
    assert left["rolling_center"].iloc[-1] == pytest.approx(right["rolling_center"].iloc[-1])


def test_future_changes_do_not_move_past_standardized_values() -> None:
    base = _monthly([float(index % 5) for index in range(200)])
    changed = base.copy()
    changed.iloc[150:] = 9_999.0
    left = causal_rolling_standardize_details(base, 120, 60)["postclip_signal"].iloc[:150]
    right = causal_rolling_standardize_details(changed, 120, 60)["postclip_signal"].iloc[:150]
    pd.testing.assert_series_equal(left, right)


def test_fully_mature_runs_from_different_starts_agree_exactly() -> None:
    """1985 시작과 1990 시작이 완전 성숙 뒤 같은 값을 내는지 본다.

    rolling 창이 공통 구간 안에 완전히 들어간 뒤에는 앞의 자료가 더 있든 없든
    중심·척도·표준화값이 **같아야** 한다. 같지 않으면 창이 rolling이 아니다.
    """

    rng = np.random.default_rng(7)
    long_series = _monthly(list(rng.normal(size=360)), start="1985-01-31")
    short_series = long_series.loc["1990-01-01":]
    long_details = causal_rolling_standardize_details(long_series, 120, 60)
    short_details = causal_rolling_standardize_details(short_series, 120, 60)
    common = short_details.index[short_details.index >= pd.Timestamp("2000-01-31")]
    for column in ("rolling_center", "rolling_scale", "postclip_signal"):
        pd.testing.assert_series_equal(
            long_details.loc[common, column], short_details.loc[common, column]
        )


def test_expanding_standardization_keeps_the_start_date_forever() -> None:
    """대조군: expanding은 같은 조건에서 값이 달라진다. rolling 검사가 자명하지 않음을 보인다."""

    rng = np.random.default_rng(7)
    long_series = _monthly(list(rng.normal(size=360)), start="1985-01-31")
    short_series = long_series.loc["1990-01-01":]
    long_expanding = long_series.shift(1).expanding(min_periods=60).mean()
    short_expanding = short_series.shift(1).expanding(min_periods=60).mean()
    common = short_series.index[short_series.index >= pd.Timestamp("2000-01-31")]
    assert not np.allclose(
        long_expanding.loc[common].to_numpy(), short_expanding.loc[common].to_numpy()
    )


# ── 척도 대체 ────────────────────────────────────────────────────────────────


def test_flat_history_falls_back_from_std_to_iqr_or_expanding() -> None:
    values = [1.0] * 80 + [2.0, 1.0] * 30
    series = _monthly(values)
    details = causal_rolling_standardize_details(series, 60, 30)
    sources = details["rolling_scale_source"]
    # 평탄 구간에서는 표준편차가 0이므로 문서화한 대체를 쓰거나 결측으로 남긴다.
    assert set(sources.unique()) <= {
        "insufficient_history",
        "rolling_std",
        "rolling_iqr",
        "expanding_std",
        "none",
    }
    assert (details.loc[sources.eq("none"), "postclip_signal"].isna()).all()


def test_zero_scale_never_produces_infinite_values() -> None:
    series = _monthly([1.0] * 200)
    details = causal_rolling_standardize_details(series, 60, 30)
    assert not np.isinf(details["postclip_signal"].to_numpy(dtype=float)).any()


# ── 상태필터 초기값 ──────────────────────────────────────────────────────────


def _emissions(weeks: int, seed: int = 3) -> np.ndarray:
    rng = np.random.default_rng(seed)
    raw = rng.random((weeks, 12)) + 0.05
    return raw / raw.sum(axis=1, keepdims=True)


def test_state_initialization_influence_decays() -> None:
    matrix = transition_matrix(
        12, {"stay": 0.66, "next": 0.22, "previous": 0.09, "jump_mass": 0.03}
    )
    emissions = _emissions(120)
    uniform = filter_probabilities(emissions, matrix)
    concentrated = np.zeros(12)
    concentrated[6] = 1.0
    biased = filter_probabilities(emissions, matrix, initial=concentrated)
    distance = np.abs(uniform - biased).sum(axis=1) / 2.0
    assert distance[0] > 0.5
    # 전이행렬의 2번째 고유값이 0.92라 반감기가 약 8.5주다. 실측 총변동거리는 60주에
    # 1e-3, 105주에 1e-6 아래로 내려간다. `minimum_training_weeks = 104`가 그래서
    # burn-in으로 충분하다. 이 수치가 무너지면 초기값이 판정에 남는다는 뜻이다.
    assert distance[52] < 1e-2
    assert distance[104] < 1e-5


def test_initial_distribution_must_be_valid() -> None:
    matrix = transition_matrix(
        12, {"stay": 0.66, "next": 0.22, "previous": 0.09, "jump_mass": 0.03}
    )
    emissions = _emissions(10)
    with pytest.raises(ValueError):
        filter_probabilities(emissions, matrix, initial=np.zeros(12))
    with pytest.raises(ValueError):
        filter_probabilities(emissions, matrix, initial=np.ones(5))


# ── 좌표 표준화 ──────────────────────────────────────────────────────────────


def test_coordinate_standardization_method_is_honoured() -> None:
    rng = np.random.default_rng(11)
    factor = pd.Series(
        rng.normal(size=800).cumsum() / 20.0,
        index=pd.date_range("2000-01-07", periods=800, freq="W-FRI"),
    )
    expanding = coordinates(factor, 8, 26, method="expanding_mean_std")
    rolling = coordinates(factor, 8, 26, method="rolling_mean_std", window_years=10)
    assert not np.allclose(
        expanding["y"].dropna().to_numpy()[-50:], rolling["y"].dropna().to_numpy()[-50:]
    )


def test_unknown_coordinate_method_is_rejected() -> None:
    factor = pd.Series(range(100), index=pd.date_range("2000-01-07", periods=100, freq="W-FRI"))
    with pytest.raises(ValueError):
        coordinates(factor, 8, 26, method="median")


# ── 설정 분리 ────────────────────────────────────────────────────────────────


def test_legacy_and_corrected_configurations_are_explicitly_separated() -> None:
    legacy = load_baseline("legacy_benchmark").model
    corrected = load_baseline("corrected_baseline").model
    assert legacy["trend_span_weeks"] == 156
    assert "trend_horizon_years" not in legacy
    assert legacy["standardization_method"] == "expanding_mean_std"
    assert legacy["maturity"]["enabled"] is False
    assert corrected["trend_horizon_years"] == 3
    assert "trend_span_weeks" not in corrected
    assert corrected["standardization_method"] == "rolling_mean_std"
    assert corrected["standardization_horizon_years"] == 10
    assert corrected["maturity"]["enabled"] is True


def test_corrected_baseline_disables_robust_processing() -> None:
    corrected = load_baseline("corrected_baseline").model
    assert corrected.get("robust_clip") is None
    assert corrected["standardization_method"] != "rolling_median_mad"


def test_leaking_configuration_is_rejected() -> None:
    from business_cycle.config import _baseline_model

    with pytest.raises(ValueError):
        _baseline_model(
            "leaky",
            {
                "trend": {"horizon_years": 3},
                "standardization": {
                    "method": "rolling",
                    "window_years": 10,
                    "include_current_observation": True,
                },
            },
            {},
        )


def test_trend_must_declare_exactly_one_horizon() -> None:
    from business_cycle.config import _baseline_model

    with pytest.raises(ValueError):
        _baseline_model("both", {"trend": {"horizon_years": 3, "span_weeks": 156}}, {})
    with pytest.raises(ValueError):
        _baseline_model("neither", {"trend": {}}, {})


# ── 원빈도 처리 ──────────────────────────────────────────────────────────────


def test_corrected_baseline_uses_native_frequency_windows(settings, synthetic_data) -> None:
    available, _ = apply_availability_dates(synthetic_data, settings.indicators["indicators"])
    corrected = load_baseline("corrected_baseline", settings).model
    transformed = transform_observations(
        available,
        settings.indicators["indicators"],
        trend_horizon_years=corrected["trend_horizon_years"],
        standardization_method=corrected["standardization_method"],
        standardization_horizon_years=corrected["standardization_horizon_years"],
        standardization_min_history_years=corrected["standardization_min_history_years"],
    )
    monthly = transformed[transformed["indicator_id"].eq("INDPRO")]
    weekly = transformed[transformed["indicator_id"].eq("ICSA")]
    assert monthly["trend_span_observations"].eq(36).all()
    assert monthly["standardization_window_observations"].eq(120).all()
    assert weekly["trend_span_observations"].eq(156).all()
    assert weekly["standardization_window_observations"].eq(520).all()
