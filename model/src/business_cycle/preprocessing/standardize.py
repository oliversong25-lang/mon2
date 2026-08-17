"""현재값을 기준 분포에 섞지 않는 인과적 표준화 도구."""

from __future__ import annotations

import numpy as np
import pandas as pd


def causal_standardize(series: pd.Series, min_periods: int = 26) -> pd.Series:
    """현재 관측을 제외한 과거의 expanding 평균과 표준편차로 표준화한다."""

    numeric = pd.to_numeric(series, errors="coerce").astype(float)
    history = numeric.shift(1)
    mean = history.expanding(min_periods=min_periods).mean()
    std = history.expanding(min_periods=min_periods).std(ddof=1)
    result = (numeric - mean) / std.replace(0.0, np.nan)
    return result.replace([np.inf, -np.inf], np.nan)


def causal_rolling_standardize(series: pd.Series, window: int, min_periods: int) -> pd.Series:
    """현재 관측을 제외한 고정 달력창 평균과 표준편차로 표준화한다."""

    numeric = pd.to_numeric(series, errors="coerce").astype(float)
    history = numeric.shift(1)
    mean = history.rolling(window, min_periods=min_periods).mean()
    std = history.rolling(window, min_periods=min_periods).std(ddof=1)
    result = (numeric - mean) / std.replace(0.0, np.nan)
    return result.replace([np.inf, -np.inf], np.nan)


def _window_mad(values: np.ndarray) -> float:
    finite = values[np.isfinite(values)]
    if not len(finite):
        return np.nan
    median = float(np.median(finite))
    return float(np.median(np.abs(finite - median)))


def causal_robust_standardize(
    series: pd.Series,
    window: int,
    min_periods: int,
    clip: float | None = 6.0,
) -> pd.DataFrame:
    """과거 rolling 중앙값/MAD로 표준화하고 기여 직전 영향력을 제한한다.

    MAD가 0인 평탄 구간은 인과적 IQR, 그마저 0이면 표준편차로 대체한다.
    모든 통계는 ``shift(1)`` 뒤 계산하므로 현재값과 미래값이 기준 분포에
    들어가지 않는다.
    """

    numeric = pd.to_numeric(series, errors="coerce").astype(float)
    history = numeric.shift(1)
    rolling = history.rolling(window, min_periods=min_periods)
    center = rolling.median()
    mad = rolling.apply(_window_mad, raw=True)
    mad_scale = 1.4826 * mad
    iqr_scale = (rolling.quantile(0.75) - rolling.quantile(0.25)) / 1.349
    std_scale = rolling.std(ddof=1)
    scale = mad_scale.where(mad_scale > 0, iqr_scale)
    scale = scale.where(scale > 0, std_scale).replace(0.0, np.nan)
    preclip = ((numeric - center) / scale).replace([np.inf, -np.inf], np.nan)
    postclip = preclip.clip(-float(clip), float(clip)) if clip is not None else preclip.copy()
    return pd.DataFrame(
        {
            "original_signal": numeric,
            "preclip_signal": preclip,
            "postclip_signal": postclip,
            "robust_center": center,
            "robust_scale": scale,
        },
        index=series.index,
    )


def causal_scale_frame(frame: pd.DataFrame, min_periods: int = 26) -> pd.DataFrame:
    """각 열을 서로 섞지 않고 expanding 방식으로 표준화한다."""

    return frame.apply(lambda column: causal_standardize(column, min_periods))
