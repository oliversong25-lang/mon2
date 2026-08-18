"""단계 A-3 좌표층의 창 단위·인과성·성숙도와 상태 우선순위."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from business_cycle.config import load_baseline
from business_cycle.models.momentum import coordinate_details, coordinates, weeks_for_years
from business_cycle.pipeline import run_pipeline
from business_cycle.validation.phase5 import total_maturity_years


def _weekly(values: list[float], start: str = "1990-01-05") -> pd.Series:
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq="W-FRI"))


def _random_factor(weeks: int = 1200, seed: int = 5, start: str = "1990-01-05") -> pd.Series:
    generator = np.random.default_rng(seed)
    return _weekly(list(generator.normal(size=weeks).cumsum() / 20.0), start=start)


# ── 창 단위 ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("years", "weeks"), [(3, 157), (5, 261), (7, 365), (10, 522)])
def test_coordinate_windows_use_calendar_duration(years: int, weeks: int) -> None:
    assert weeks_for_years(years) == weeks


@pytest.mark.parametrize("years", [3.0, 5.0, 7.0, 10.0])
def test_measured_window_duration_matches_the_intended_years(years: float) -> None:
    factor = _random_factor()
    details = coordinate_details(
        factor, 8, 26, method="rolling_mean_std", window_years=years, minimum_history_years=2
    )
    measured = details["window_duration_years"].dropna()
    mature = measured[measured.index >= factor.index[weeks_for_years(years)]]
    assert abs(float(mature.median()) - years) <= 0.05
    counts = details["window_observation_count"].dropna()
    assert int(counts.max()) == weeks_for_years(years)


def test_expanding_window_is_not_reported_as_a_fixed_window() -> None:
    """expanding에는 창이 없다. 창이 있는 것처럼 기록하면 거짓 감사 기록이 남는다."""

    factor = _random_factor()
    details = coordinate_details(factor, 8, 26, method="expanding_mean_std")
    duration = details["window_duration_years"].dropna()
    # 시작점이 자료의 시작이므로 길이가 계속 늘어난다.
    assert duration.iloc[-1] > duration.iloc[len(duration) // 2] + 1.0


# ── 인과성 ───────────────────────────────────────────────────────────────────


def test_current_observation_is_excluded_from_coordinate_center_and_scale() -> None:
    factor = _random_factor()
    changed = factor.copy()
    changed.iloc[-1] = 500.0
    left = coordinate_details(factor, 8, 26, method="rolling_mean_std", window_years=5)
    right = coordinate_details(changed, 8, 26, method="rolling_mean_std", window_years=5)
    assert left["coordinate_center"].iloc[-1] == right["coordinate_center"].iloc[-1]
    assert left["coordinate_scale"].iloc[-1] == right["coordinate_scale"].iloc[-1]


def test_future_changes_do_not_move_past_coordinates() -> None:
    factor = _random_factor()
    changed = factor.copy()
    changed.iloc[900:] = 999.0
    left = coordinate_details(factor, 8, 26, method="rolling_mean_std", window_years=5)
    right = coordinate_details(changed, 8, 26, method="rolling_mean_std", window_years=5)
    pd.testing.assert_series_equal(left["y"].iloc[:900], right["y"].iloc[:900])


def test_changes_outside_the_coordinate_window_do_not_move_the_current_value() -> None:
    factor = _random_factor()
    changed = factor.copy()
    changed.iloc[:400] = changed.iloc[:400] + 100.0
    left = coordinate_details(factor, 8, 26, method="rolling_mean_std", window_years=5)
    right = coordinate_details(changed, 8, 26, method="rolling_mean_std", window_years=5)
    assert left["y"].iloc[-1] == pytest.approx(right["y"].iloc[-1])


def test_mature_runs_from_different_starts_agree_exactly() -> None:
    """5년 창이면 1985·1990 시작이 2000년 무렵부터 같은 좌표를 내야 한다."""

    long_factor = _random_factor(weeks=1900, start="1985-01-04")
    short_factor = long_factor.loc["1990-01-01":]
    left = coordinate_details(
        long_factor, 8, 26, method="rolling_mean_std", window_years=5, minimum_history_years=2
    )
    right = coordinate_details(
        short_factor, 8, 26, method="rolling_mean_std", window_years=5, minimum_history_years=2
    )
    common = right.index[right.index >= pd.Timestamp("1996-01-01")]
    for column in ("coordinate_center", "coordinate_scale", "y"):
        pd.testing.assert_series_equal(left.loc[common, column], right.loc[common, column])


# ── 척도 붕괴 진단 ───────────────────────────────────────────────────────────


def test_flat_history_never_produces_infinite_coordinates() -> None:
    factor = _weekly([1.0] * 400)
    details = coordinate_details(factor, 8, 26, method="rolling_mean_std", window_years=5)
    assert not np.isinf(details["y"].to_numpy(dtype=float)).any()
    assert set(details["scale_fallback_used"].unique()) <= {
        "std",
        "iqr",
        "expanding_std",
        "insufficient_history",
        "none",
    }


def test_small_historical_scale_is_visible_in_the_audit() -> None:
    """조용한 구간 뒤에 충격이 오면 척도가 작아 Y가 부풀 수 있다. 감사로 보여야 한다."""

    values = [0.0] * 300 + list(np.linspace(0.0, -5.0, 200))
    details = coordinate_details(
        _weekly(values), 8, 26, method="rolling_mean_std", window_years=5, minimum_history_years=2
    )
    scale = details["coordinate_scale"].dropna()
    assert float(scale.min()) < float(scale.median())
    assert details["y"].abs().max() > 3.0


def test_unstandardized_coordinates_are_marked_as_such() -> None:
    factor = _random_factor()
    details = coordinate_details(factor, 8, 26, method="none")
    assert (details["scale_fallback_used"] == "not_standardized").all()
    pd.testing.assert_series_equal(details["y"], factor, check_names=False)


def test_unknown_coordinate_method_is_rejected() -> None:
    with pytest.raises(ValueError):
        coordinates(_random_factor(weeks=200), 8, 26, method="median")


# ── 성숙도와 상태 ────────────────────────────────────────────────────────────


def test_five_year_coordinate_layer_keeps_total_maturity_at_ten_years() -> None:
    ten = load_baseline("coordinate_a_10y")
    five = load_baseline("coordinate_b_5y")
    assert total_maturity_years(ten) == pytest.approx(15.0)
    assert total_maturity_years(five) == pytest.approx(10.0)


def test_coordinate_maturity_settings_are_loaded() -> None:
    five = load_baseline("coordinate_b_5y").model
    assert five["coordinate_standardization_method"] == "rolling_mean_std"
    assert five["coordinate_standardization_horizon_years"] == 5
    assert five["coordinate_standardization_min_history_years"] == 2
    assert five["coordinate_full_history_years"] == 5


def test_no_second_standardization_is_configurable() -> None:
    assert load_baseline("coordinate_e_none").model["coordinate_standardization_method"] == "none"


def test_withheld_takes_precedence_over_preliminary(settings, synthetic_data) -> None:
    """자료가 모자라 판정을 내지 않기로 한 경우를 잠정 판정으로 낮추면 안 된다."""

    sparse = synthetic_data[synthetic_data["indicator_id"].isin(["PAYEMS", "ICSA"])].copy()
    run = run_pipeline(sparse, settings, "2026-08-14")
    assert run.result.status == "withheld"
    assert run.result.metadata["status_reason"]
    assert any("판정 보류" in warning for warning in run.result.warnings)


def test_official_status_requires_both_maturity_clocks(settings, synthetic_data) -> None:
    run = run_pipeline(synthetic_data, settings, "2026-08-14")
    assert run.result.status == "official"
    assert run.result.metadata["status_reason"] == ""
    assert run.result.metadata["coordinate_history_years"] >= 10.0


def test_short_history_is_preliminary_not_official(settings, synthetic_data) -> None:
    early = synthetic_data[
        pd.to_datetime(synthetic_data["observation_period"]) <= pd.Timestamp("1997-06-30")
    ].copy()
    run = run_pipeline(early, settings, "1997-06-30")
    assert run.result.status in {"preliminary", "withheld"}
    assert run.result.metadata["status_reason"]


# ── 진단 출력 ────────────────────────────────────────────────────────────────


def test_pipeline_exposes_the_coordinate_audit(settings, synthetic_data) -> None:
    run = run_pipeline(synthetic_data, settings, "2026-08-14")
    for column in (
        "coordinate_center",
        "coordinate_scale",
        "unscaled_x",
        "unscaled_y",
        "x",
        "y",
        "window_start",
        "window_end",
        "window_duration_years",
        "window_observation_count",
        "scale_fallback_used",
    ):
        assert column in run.coordinate_audit
    assert run.coordinate_audit["y"].notna().any()


def test_immature_run_reports_no_confirmed_case_lag() -> None:
    """검증 구간에 없는 침체는 확인 시차를 지어내지 않고 비워 둔다."""

    from business_cycle.validation.robustness import _case_lags

    class _Stub:
        metrics = {
            "turning_points": [
                {
                    "official_start_week": "2007-12-07",
                    "entry_lead_lag_weeks": 3.0,
                    "exit_lead_lag_weeks": 1.0,
                }
            ]
        }

    assert _case_lags(_Stub(), "gfc") == (3.0, 1.0)  # type: ignore[arg-type]
    assert _case_lags(_Stub(), "2001") == (None, None)  # type: ignore[arg-type]
