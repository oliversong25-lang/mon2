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


#: rolling 척도를 0으로 볼 임계값. 부동소수 잡음을 0으로 나누지 않기 위한 하한이다.
NEGLIGIBLE_SCALE = 1e-12


def causal_rolling_standardize_details(
    series: pd.Series, window: int, min_periods: int
) -> pd.DataFrame:
    """현재 관측을 제외한 고정 달력창 평균·표준편차로 표준화하고 창을 감사한다.

    창은 원빈도 관측 위치 ``[i-window, i-1]``이다. 현재 관측 ``i``와 그 이후는 중심·척도
    계산에 들어가지 않고, ``i-window`` 이전 관측도 들어가지 않는다. 따라서 창 밖 과거를
    바꿔도 현재 표준화값이 변하지 않고, 미래를 바꿔도 과거 값이 변하지 않는다.

    척도가 0이거나 무시할 만큼 작으면 문서화한 순서로 인과적 대체를 쓴다.
    표준편차 → 사분위범위/1.349 → 현재값을 제외한 expanding 표준편차. 어느 것도
    양수가 아니면 그 주는 결측으로 남긴다. 조용히 0으로 나누지 않는다.
    """

    numeric = pd.to_numeric(series, errors="coerce").astype(float)
    history = numeric.shift(1)
    rolling = history.rolling(window, min_periods=min_periods)
    center = rolling.mean()
    std = rolling.std(ddof=1)
    iqr_scale = (rolling.quantile(0.75) - rolling.quantile(0.25)) / 1.349
    expanding_scale = history.expanding(min_periods=min_periods).std(ddof=1)

    usable_std = std.where(std > NEGLIGIBLE_SCALE)
    usable_iqr = iqr_scale.where(iqr_scale > NEGLIGIBLE_SCALE)
    usable_expanding = expanding_scale.where(expanding_scale > NEGLIGIBLE_SCALE)
    scale = usable_std.fillna(usable_iqr).fillna(usable_expanding)
    source = pd.Series("none", index=series.index, dtype=object)
    source = source.mask(usable_expanding.notna(), "expanding_std")
    source = source.mask(usable_iqr.notna(), "rolling_iqr")
    source = source.mask(usable_std.notna(), "rolling_std")
    source = source.mask(center.isna(), "insufficient_history")

    standardized = ((numeric - center) / scale).replace([np.inf, -np.inf], np.nan)
    positions = np.arange(len(numeric))
    index_values = pd.Series(series.index)
    start_positions = np.clip(positions - window, 0, max(len(numeric) - 1, 0))
    end_positions = np.clip(positions - 1, 0, max(len(numeric) - 1, 0))
    window_start = index_values.reindex(start_positions).to_numpy()
    window_end = index_values.reindex(end_positions).to_numpy()
    observed = rolling.count()
    return pd.DataFrame(
        {
            "original_signal": numeric,
            "preclip_signal": standardized,
            "postclip_signal": standardized,
            "rolling_center": center,
            "rolling_scale": scale,
            "rolling_scale_source": source.to_numpy(),
            # 첫 행에는 과거가 없다. 창 경계를 실제 관측이 있을 때만 기록한다.
            "window_start": [
                window_start[position] if position >= 1 else None for position in positions
            ],
            "window_end": [
                window_end[position] if position >= 1 else None for position in positions
            ],
            "window_observations": observed,
        },
        index=series.index,
    )


def causal_rolling_standardize(series: pd.Series, window: int, min_periods: int) -> pd.Series:
    """현재 관측을 제외한 고정 달력창 평균과 표준편차로 표준화한다."""

    return causal_rolling_standardize_details(series, window, min_periods)["postclip_signal"]


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
