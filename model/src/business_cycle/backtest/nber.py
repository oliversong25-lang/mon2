"""NBER 기준순환일의 공개 월 경계.

NBER는 12개 세부국면 정답이 아니므로 침체/비침체 평가에만 사용한다.
"""

from __future__ import annotations

import pandas as pd

NBER_RECESSIONS = [
    ("1960-04-01", "1961-02-28"),
    ("1969-12-01", "1970-11-30"),
    ("1973-11-01", "1975-03-31"),
    ("1980-01-01", "1980-07-31"),
    ("1981-07-01", "1982-11-30"),
    ("1990-07-01", "1991-03-31"),
    ("2001-03-01", "2001-11-30"),
    ("2007-12-01", "2009-06-30"),
    ("2020-02-01", "2020-04-30"),
]


def recession_flags(index: pd.DatetimeIndex) -> pd.Series:
    """주간 인덱스가 NBER 침체월에 포함되는지 표시한다."""

    flags = pd.Series(False, index=index, dtype=bool)
    for start, end in NBER_RECESSIONS:
        flags |= (index >= pd.Timestamp(start)) & (index <= pd.Timestamp(end))
    return flags
