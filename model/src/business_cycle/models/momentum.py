"""경기 수준에서 비교 가능한 모멘텀 좌표를 만든다."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..preprocessing.standardize import causal_standardize


def coordinates(
    factor: pd.Series, weeks: int = 4, min_periods: int = 26, slope: pd.Series | None = None
) -> pd.DataFrame:
    """Y(level)과 최근 변화·상태 기울기를 결합한 X(momentum)를 계산한다."""

    y = causal_standardize(factor, min_periods)
    delta = factor - factor.shift(weeks)
    x_delta = causal_standardize(delta, min_periods)
    if slope is not None:
        x_slope = causal_standardize(slope.reindex(factor.index), min_periods)
        x = pd.concat([x_delta.rename("delta"), x_slope.rename("slope")], axis=1).mean(axis=1)
    else:
        x = x_delta
    angle = np.degrees(np.arctan2(y, x)) % 360.0
    radius = np.sqrt(x.pow(2) + y.pow(2))
    return pd.DataFrame({"x": x, "y": y, "angle": angle, "radius": radius})
