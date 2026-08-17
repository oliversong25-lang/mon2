"""경기 수준에서 비교 가능한 모멘텀 좌표를 만든다."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..preprocessing.standardize import causal_rolling_standardize, causal_standardize

EXPANDING = "expanding_mean_std"
ROLLING = "rolling_mean_std"


def _standardize(series: pd.Series, method: str, window: int, min_periods: int) -> pd.Series:
    """지표 표준화와 같은 규칙을 좌표 표준화에도 적용한다.

    좌표는 주간 축이므로 창 단위도 주다. 지표 쪽은 원빈도 관측 수를 쓴다.
    """

    if method == EXPANDING:
        return causal_standardize(series, min_periods)
    if method == ROLLING:
        return causal_rolling_standardize(series, window, min_periods)
    raise ValueError(f"지원하지 않는 좌표 표준화 방식: {method}")


def coordinates(
    factor: pd.Series,
    weeks: int = 4,
    min_periods: int = 26,
    slope: pd.Series | None = None,
    *,
    method: str = EXPANDING,
    window: int = 520,
    minimum_history_weeks: int | None = None,
) -> pd.DataFrame:
    """Y(level)과 최근 변화·상태 기울기를 결합한 X(momentum)를 계산한다.

    ``minimum_history_weeks``는 좌표 표준화가 몇 주치 합성요인 이력을 확보한 뒤에야
    값을 내보낼지 정한다. 이 값이 작으면 짧고 조용한 표본에서 계산된 작은 척도가
    같은 요인 값을 몇 배로 부풀린다. 지표 표준화에 최소 이력 규칙을 둔 것과 같은
    이유이며, 좌표 층에는 그 규칙이 없었다.
    """

    required = max(int(min_periods), int(minimum_history_weeks or 0))
    y = _standardize(factor, method, window, required)
    delta = factor - factor.shift(weeks)
    x_delta = _standardize(delta, method, window, required)
    if slope is not None:
        x_slope = _standardize(slope.reindex(factor.index), method, window, required)
        x = pd.concat([x_delta.rename("delta"), x_slope.rename("slope")], axis=1).mean(axis=1)
    else:
        x = x_delta
    angle = np.degrees(np.arctan2(y, x)) % 360.0
    radius = np.sqrt(x.pow(2) + y.pow(2))
    return pd.DataFrame({"x": x, "y": y, "angle": angle, "radius": radius})
