"""one-sided 추세 추정."""

from __future__ import annotations

import pandas as pd


def one_sided_ema_cycle(series: pd.Series, span: int) -> pd.Series:
    """과거와 현재만 사용하는 지수이동 추세 대비 편차를 반환한다."""

    trend = series.ewm(span=span, adjust=False, min_periods=max(4, span // 8)).mean()
    return series - trend
