"""인과적 강건 척도. 한 번의 충격이 10년간 단위를 정하지 못하게 한다.

기존 좌표 척도는 10년 rolling 표준편차였다. 2020년 모멘텀 급등이 창에 들어가면서
X 척도가 5.5배로 부풀었고, 원신호는 오히려 커졌는데도 X가 눌렸다. 그 창은 2028년까지
남는다. 현재상태 분류기가 그 단위를 물려받으면 안 된다.

여기 있는 척도는 전부 다음을 지킨다.

* 계산에 과거 관측만 쓴다. 현재 관측은 ``shift(1)``로 제외한다.
* 한 극단 관측이 척도를 좌우하지 못한다(중앙값·사분위 기반).
* 그러면서 진짜 극단은 극단으로 남는다 — 척도가 커지지 않으므로 표준화값이 크게 나온다.
"""

from __future__ import annotations

from typing import Final

import numpy as np
import pandas as pd

#: 정규분포에서 MAD를 표준편차로 되돌리는 상수.
MAD_TO_SIGMA: Final[float] = 1.4826

#: 정규분포에서 IQR을 표준편차로 되돌리는 상수.
IQR_TO_SIGMA: Final[float] = 1.349

#: 척도가 0에 붙어 표준화값이 폭주하지 않게 하는 하한. 창 안 관측 수가 모자랄 때만 쓴다.
MINIMUM_SCALE: Final[float] = 1e-9

METHODS: Final[tuple[str, ...]] = (
    "rolling_std",
    "rolling_mad",
    "rolling_iqr",
    "bounded_influence",
)


def _history(series: pd.Series) -> pd.Series:
    """현재 관측을 기준분포에서 제외한다. 자기 자신으로 자신을 재는 것을 막는다."""

    return series.shift(1)


def rolling_std(series: pd.Series, window: int, min_periods: int) -> pd.Series:
    """기존 방식. 비교 기준으로만 둔다 — 한 번의 충격이 창 내내 단위를 지배한다."""

    return _history(series).rolling(window, min_periods=min_periods).std(ddof=1)


def rolling_mad(series: pd.Series, window: int, min_periods: int) -> pd.Series:
    """중앙값 절대편차. 창 안 관측의 절반이 바뀌어야 척도가 움직인다."""

    history = _history(series)
    median = history.rolling(window, min_periods=min_periods).median()
    deviation = (history - median).abs()
    return deviation.rolling(window, min_periods=min_periods).median() * MAD_TO_SIGMA


def rolling_iqr(series: pd.Series, window: int, min_periods: int) -> pd.Series:
    """사분위 범위. MAD보다 꼬리를 조금 더 반영하되 극단 하나에는 둔감하다."""

    history = _history(series)
    rolling = history.rolling(window, min_periods=min_periods)
    return (rolling.quantile(0.75) - rolling.quantile(0.25)) / IQR_TO_SIGMA


def bounded_influence(
    series: pd.Series, window: int, min_periods: int, clip: float = 3.0
) -> pd.Series:
    """윈저화 후 표준편차. 극단 관측의 **영향**만 자르고 관측 자체는 남긴다.

    자르는 기준(중앙값 ± clip×MAD)도 과거 자료로만 만든다.
    """

    history = _history(series)
    median = history.rolling(window, min_periods=min_periods).median()
    mad = (history - median).abs().rolling(window, min_periods=min_periods).median() * MAD_TO_SIGMA
    upper = median + clip * mad
    lower = median - clip * mad
    winsorized = history.clip(lower=lower, upper=upper)
    return winsorized.rolling(window, min_periods=min_periods).std(ddof=1)


def causal_scale(
    series: pd.Series,
    method: str,
    window: int,
    min_periods: int,
    clip: float = 3.0,
) -> pd.Series:
    """이름으로 척도를 고른다. 0 이하 값은 결측으로 둔다 — 지어내지 않는다."""

    if method == "rolling_std":
        scale = rolling_std(series, window, min_periods)
    elif method == "rolling_mad":
        scale = rolling_mad(series, window, min_periods)
    elif method == "rolling_iqr":
        scale = rolling_iqr(series, window, min_periods)
    elif method == "bounded_influence":
        scale = bounded_influence(series, window, min_periods, clip)
    else:
        raise ValueError(f"지원하지 않는 척도 방식: {method}")
    return scale.where(scale > MINIMUM_SCALE)


def standardize(
    series: pd.Series,
    method: str,
    window: int,
    min_periods: int,
    clip: float = 3.0,
) -> tuple[pd.Series, pd.Series]:
    """표준화값과 그때 쓴 척도를 함께 돌려준다. 척도를 감사할 수 있어야 한다."""

    scale = causal_scale(series, method, window, min_periods, clip)
    scaled = (series / scale).replace([np.inf, -np.inf], np.nan)
    return scaled, scale
