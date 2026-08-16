"""순방향(expanding/rolling) 표준화."""

from __future__ import annotations

import numpy as np
import pandas as pd


def causal_standardize(series: pd.Series, min_periods: int = 26) -> pd.Series:
    """현재 시점 이전 값으로만 평균과 표준편차를 계산한다.

    현재 관측이 자기 자신의 기준분포를 바꾸지 않게 통계량을 한 칸 shift한다.
    """

    numeric = pd.to_numeric(series, errors="coerce").astype(float)
    mean = numeric.expanding(min_periods=min_periods).mean().shift(1)
    std = numeric.expanding(min_periods=min_periods).std(ddof=1).shift(1)
    result = (numeric - mean) / std.replace(0.0, np.nan)
    return result.replace([np.inf, -np.inf], np.nan)


def causal_scale_frame(frame: pd.DataFrame, min_periods: int = 26) -> pd.DataFrame:
    """각 열에 독립적으로 순방향 표준화를 적용한다."""

    return frame.apply(lambda column: causal_standardize(column, min_periods))
