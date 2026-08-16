"""지표별 설정 기반 변환과 방향 통일."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .standardize import causal_standardize
from .trends import one_sided_ema_cycle


def transform_series(
    values: pd.Series,
    transform: str,
    direction: int,
    trend_span: int,
    min_periods: int,
) -> pd.Series:
    """설정된 기본 변환을 적용하고 양수가 경기 개선을 뜻하도록 맞춘다."""

    numeric = pd.to_numeric(values, errors="coerce").astype(float)
    if transform == "log_cycle":
        safe = numeric.where(numeric > 0).apply(np.log)
        signal = one_sided_ema_cycle(safe, trend_span)
    elif transform == "claims_4w_log":
        smooth = numeric.rolling(4, min_periods=4).mean()
        signal = one_sided_ema_cycle(smooth.where(smooth > 0).apply(np.log), trend_span)
    elif transform == "level_change":
        signal = numeric.diff(4)
    elif transform == "yoy":
        signal = numeric.pct_change(12, fill_method=None)
    elif transform == "annualized_3m":
        signal = (numeric / numeric.shift(3)).pow(4) - 1.0
    elif transform == "change_6m":
        signal = numeric.pct_change(6, fill_method=None)
    else:
        raise ValueError(f"지원하지 않는 변환: {transform}")
    return causal_standardize(signal * float(direction), min_periods)


def transform_observations(
    frame: pd.DataFrame,
    settings: dict[str, Any],
    trend_span: int,
    min_periods: int,
) -> pd.DataFrame:
    """각 지표를 관측 순서대로 변환해 `signal` 열을 추가한다."""

    parts: list[pd.DataFrame] = []
    for indicator_id, group in frame.groupby("indicator_id", sort=False):
        config = settings.get(str(indicator_id))
        if config is None:
            continue
        ordered = group.sort_values("available_week").copy()
        ordered["signal"] = transform_series(
            ordered["value"],
            str(config["transform"]),
            int(config.get("direction", 1)),
            trend_span,
            min_periods,
        )
        parts.append(ordered)
    if not parts:
        raise ValueError("설정과 일치하는 지표가 없습니다")
    return pd.concat(parts, ignore_index=True).sort_values(["available_week", "indicator_id"])
