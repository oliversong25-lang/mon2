"""지표의 원빈도에서 변환·추세·인과적 표준화를 수행한다."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .standardize import (
    causal_robust_standardize,
    causal_rolling_standardize,
    causal_standardize,
)
from .trends import one_sided_ema_cycle


def periods_for_years(frequency: str, years: float) -> int:
    """같은 달력기간이 빈도마다 같은 경제적 길이를 갖도록 관측 수로 바꾼다."""

    per_year = {"weekly": 52.0, "monthly": 12.0, "quarterly": 4.0}
    if frequency not in per_year:
        raise ValueError(f"지원하지 않는 빈도: {frequency}")
    return max(1, int(round(per_year[frequency] * float(years))))


def _economic_signal(values: pd.Series, transform: str, trend_span: int) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce").astype(float)
    if transform == "log_cycle":
        return one_sided_ema_cycle(numeric.where(numeric > 0).apply(np.log), trend_span)
    if transform == "claims_4w_log":
        smooth = numeric.rolling(4, min_periods=4).mean()
        return one_sided_ema_cycle(smooth.where(smooth > 0).apply(np.log), trend_span)
    if transform == "level_change":
        return numeric.diff(4)
    if transform == "yoy":
        return numeric.pct_change(12, fill_method=None)
    if transform == "annualized_3m":
        return (numeric / numeric.shift(3)).pow(4) - 1.0
    if transform == "change_6m":
        return numeric.pct_change(6, fill_method=None)
    raise ValueError(f"지원하지 않는 변환: {transform}")


def transform_series_details(
    values: pd.Series,
    transform: str,
    direction: int,
    trend_span: int,
    min_periods: int,
    *,
    standardization_method: str = "expanding_mean_std",
    standardization_window: int | None = None,
    robust_clip: float | None = None,
) -> pd.DataFrame:
    """원신호·클립 전·클립 후 신호를 함께 보존한다."""

    original = _economic_signal(values, transform, trend_span) * float(direction)
    if standardization_method == "expanding_mean_std":
        standardized = causal_standardize(original, min_periods)
        return pd.DataFrame(
            {
                "original_signal": original,
                "preclip_signal": standardized,
                "postclip_signal": standardized,
            },
            index=values.index,
        )
    if standardization_window is None:
        raise ValueError("rolling 표준화에는 standardization_window가 필요합니다")
    if standardization_method == "rolling_mean_std":
        standardized = causal_rolling_standardize(original, standardization_window, min_periods)
        return pd.DataFrame(
            {
                "original_signal": original,
                "preclip_signal": standardized,
                "postclip_signal": standardized,
            },
            index=values.index,
        )
    if standardization_method == "rolling_median_mad":
        return causal_robust_standardize(original, standardization_window, min_periods, robust_clip)
    raise ValueError(f"지원하지 않는 표준화 방식: {standardization_method}")


def transform_series(
    values: pd.Series,
    transform: str,
    direction: int,
    trend_span: int,
    min_periods: int,
    **kwargs: Any,
) -> pd.Series:
    """기존 호출 호환용: 합성모형에 들어가는 최종 신호만 반환한다."""

    return transform_series_details(
        values, transform, direction, trend_span, min_periods, **kwargs
    )["postclip_signal"]


def transform_observations(
    frame: pd.DataFrame,
    settings: dict[str, Any],
    trend_span: int | None = None,
    min_periods: int = 26,
    *,
    trend_horizon_years: float | None = None,
    standardization_method: str = "expanding_mean_std",
    standardization_horizon_years: float = 10.0,
    standardization_min_history_years: float | None = None,
    robust_clip: float | None = None,
) -> pd.DataFrame:
    """주간 정렬 전에 각 지표의 원빈도에서 신호를 만든다."""

    parts: list[pd.DataFrame] = []
    for indicator_id, group in frame.groupby("indicator_id", sort=False):
        config = settings.get(str(indicator_id))
        if config is None:
            continue
        frequency = str(config["frequency"])
        local_trend_span = (
            periods_for_years(frequency, trend_horizon_years)
            if trend_horizon_years is not None
            else int(trend_span or 156)
        )
        standardization_window = periods_for_years(frequency, standardization_horizon_years)
        local_min_periods = (
            periods_for_years(frequency, standardization_min_history_years)
            if standardization_min_history_years is not None
            else min_periods
        )
        ordered = group.sort_values("available_week").copy()
        details = transform_series_details(
            ordered["value"],
            str(config["transform"]),
            int(config.get("direction", 1)),
            local_trend_span,
            local_min_periods,
            standardization_method=standardization_method,
            standardization_window=standardization_window,
            robust_clip=robust_clip,
        )
        for column in details:
            ordered[column] = details[column]
        # 제한 전 값을 event로 넘기고 대표 합성모형의 기여 계산 직전에 제한한다.
        # postclip은 감사와 재현을 위해 원빈도 행에 별도로 보존한다.
        ordered["signal"] = ordered["preclip_signal"]
        ordered["trend_span_observations"] = local_trend_span
        ordered["standardization_window_observations"] = standardization_window
        ordered["frequency"] = frequency
        parts.append(ordered)
    if not parts:
        raise ValueError("설정과 일치하는 지표가 없습니다")
    return pd.concat(parts, ignore_index=True).sort_values(["available_week", "indicator_id"])
