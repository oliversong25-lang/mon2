"""격차 변환. 과열은 **생산능력에 견주어야** 보인다.

수준만으로는 과열을 못 본다. 가동률이 높다는 것은 장기 평균과 견줘야 뜻이 생기고, 실업률이
낮다는 것은 자연실업률과 견줘야 뜻이 생긴다. 자료를 새로 들이는 것이 아니라 **변환**을
더하는 것에 가깝다.

저점 통과에는 주간 신규 실업수당 청구를 쓴다. 무료, 주간, 사실상 수정되지 않는다.

## 앞을 훔쳐보지 않기

월간·분기 계열은 발표가 늦다. 그 달의 값을 그 달 주간 격자에 그대로 얹으면, 실제로는
아직 알 수 없던 값을 쓰는 것이 된다. 그래서 보수적인 지연을 두고 얹는다.

장기 평균도 마찬가지다. 전체 표본 평균을 쓰면 미래를 보고 기준선을 그리는 셈이라
**확장 평균**만 쓴다.
"""

from __future__ import annotations

import os
import urllib.request
from typing import Final

import numpy as np
import pandas as pd

FRED_CSV: Final[str] = "https://fred.stlouisfed.org/graph/fredgraph.csv?id="

#: 새로 들이는 계열. 어느 것도 API 키가 필요 없는 공개 CSV다.
GAP_SERIES: Final[tuple[str, ...]] = ("TCU", "UNRATE", "NROU")

CACHE_DIR: Final[str] = "data/cache/gaps"

#: 월간·분기 계열의 보수적 발표 지연. 월말 이후 이 주 수가 지나야 쓸 수 있다고 본다.
PUBLICATION_LAG_WEEKS: Final[int] = 6

#: 주간 청구건수의 발표 지연. 목요일에 직전 토요일 마감분이 나오므로 한 주면 충분하다.
CLAIMS_LAG_WEEKS: Final[int] = 1

#: 확장 평균을 신뢰하기 전에 필요한 최소 관측. 10년.
MINIMUM_HISTORY_MONTHS: Final[int] = 120

#: 청구건수가 연중 고점에서 이만큼 내려오면 저점을 지나는 중으로 본다. 미리 정한 값이다.
CLAIMS_OFF_PEAK: Final[float] = 0.10

#: 청구건수 고점을 재는 창.
CLAIMS_PEAK_WINDOW_WEEKS: Final[int] = 52


def download(cache_dir: str = CACHE_DIR) -> None:
    """공개 FRED CSV. 인증이 필요 없는 경로만 쓴다."""

    os.makedirs(cache_dir, exist_ok=True)
    for name in GAP_SERIES:
        payload = urllib.request.urlopen(FRED_CSV + name, timeout=60).read()
        with open(os.path.join(cache_dir, f"{name}.csv"), "wb") as handle:
            handle.write(payload)


def _read(name: str, cache_dir: str) -> pd.Series:
    frame = pd.read_csv(os.path.join(cache_dir, f"{name}.csv"))
    frame.columns = ["date", "value"]
    frame["date"] = pd.to_datetime(frame["date"])
    series = pd.Series(
        pd.to_numeric(frame["value"], errors="coerce").to_numpy(), index=frame["date"]
    )
    return series.dropna()


def _to_weekly(series: pd.Series, weeks: list[str], lag_weeks: int) -> pd.Series:
    """관측일 + 지연 이후의 주부터 그 값을 쓴다. 그 전에는 결측."""

    available = pd.Series(
        series.to_numpy(), index=series.index + pd.Timedelta(weeks=lag_weeks)
    ).sort_index()
    stamps = pd.to_datetime(pd.Series(weeks))
    values = [
        float(available[available.index <= stamp].iloc[-1])
        if bool((available.index <= stamp).any())
        else float("nan")
        for stamp in stamps
    ]
    return pd.Series(values, index=pd.Index(weeks, name="week"))


def build(weeks: list[str], cache_dir: str = CACHE_DIR, claims_path: str = "") -> pd.DataFrame:
    """주간 격자 위의 격차 변환 세 가지."""

    capacity = _read("TCU", cache_dir)
    # 장기 평균은 **확장 평균**이다. 전체 평균을 쓰면 기준선이 미래를 본다.
    long_run = capacity.expanding(MINIMUM_HISTORY_MONTHS).mean()
    capacity_gap = (capacity - long_run).dropna()

    unemployment = _read("UNRATE", cache_dir)
    natural = _read("NROU", cache_dir).reindex(unemployment.index).ffill()
    unemployment_gap = (unemployment - natural).dropna()

    out = pd.DataFrame(index=pd.Index(weeks, name="week"))
    out["capacity_gap"] = _to_weekly(capacity_gap, weeks, PUBLICATION_LAG_WEEKS)
    out["unemployment_gap"] = _to_weekly(unemployment_gap, weeks, PUBLICATION_LAG_WEEKS)

    if claims_path:
        claims = _read_claims(claims_path)
        smoothed = claims.rolling(4).mean()
        weekly = _to_weekly(smoothed.dropna(), weeks, CLAIMS_LAG_WEEKS)
        peak = weekly.rolling(CLAIMS_PEAK_WINDOW_WEEKS, min_periods=13).max()
        with np.errstate(invalid="ignore", divide="ignore"):
            out["claims_off_peak"] = (peak - weekly) / peak
    else:
        out["claims_off_peak"] = float("nan")
    return out


def _read_claims(path: str) -> pd.Series:
    frame = pd.read_csv(path)
    frame.columns = ["date", "value"]
    frame["date"] = pd.to_datetime(frame["date"])
    return pd.Series(
        pd.to_numeric(frame["value"], errors="coerce").to_numpy(), index=frame["date"]
    ).dropna()


def coverage(frame: pd.DataFrame) -> dict[str, object]:
    """격차 변환이 실제로 채워진 주. 앞부분은 확장 평균이 아직 못 서서 비어 있다."""

    return {
        column: {
            "weeks_with_a_value": int(frame[column].notna().sum()),
            "first_week": (
                str(frame[column].first_valid_index()) if frame[column].notna().any() else None
            ),
        }
        for column in frame.columns
    }
