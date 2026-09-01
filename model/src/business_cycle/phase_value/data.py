"""가치 요인, 장부가/시가 포트폴리오, 기간 스프레드를 주간 격자에 올린다.

HML은 장부가/시가 정렬이다. Damodaran이 말하는 내재가치 대비 저평가와는 다르며, 그
차이가 이 단계 결론의 비대칭을 만든다 — 음의 결과는 주장을 약화시키지만 반증하지
않고, 양의 결과는 강한 지지가 된다.

기간 스프레드(10년-3개월)는 **가장 중요한 대조군**이다. 가치 프리미엄이 금리·신용
여건과 함께 움직인다는 것은 알려져 있어서, 금리를 통제하면 사라지는 국면 효과는
국면 옷을 입은 금리 효과다.
"""

from __future__ import annotations

import os
import urllib.request
from typing import Final

import pandas as pd

from ..phase_returns.french import CACHE_DIR as FRENCH_CACHE
from ..phase_returns.french import FRENCH_BASE, _section, _to_frame

#: 장부가/시가로 정렬한 포트폴리오. 가치 다리와 성장 다리를 갈라 보려고 쓴다.
BEME_ZIP: Final[str] = "Portfolios_Formed_on_BE-ME_daily_CSV.zip"
BEME_CSV: Final[str] = "Portfolios_Formed_on_BE-ME_Daily.csv"

VALUE_WEIGHTED_MARKER: Final[str] = "Average Value Weighted Returns -- Daily"
EQUAL_WEIGHTED_MARKER: Final[str] = "Average Equal Weighted Returns -- Daily"

#: 기간 스프레드. 미 재무부 일별 수익률 곡선에서 파생된 계열이며 키가 필요 없다.
TERM_SPREAD_ID: Final[str] = "T10Y3M"
FRED_CSV: Final[str] = "https://fred.stlouisfed.org/graph/fredgraph.csv?id="
RATE_CACHE: Final[str] = "data/cache/rates"

#: 기간 스프레드의 **변화**를 재는 창. 26주 지평선의 절반으로 미리 정한다.
SPREAD_CHANGE_WEEKS: Final[int] = 13

#: 이 단계가 쓰는 지평선. 가치 주장에 4주·13주는 짧다 — 잘못된 가격이 한 달에 닫히지
#: 않는다.
HORIZONS: Final[tuple[int, ...]] = (26, 52, 104)


def download(french_cache: str = FRENCH_CACHE, rate_cache: str = RATE_CACHE) -> None:
    """장부가/시가 포트폴리오와 기간 스프레드를 캐시에 둔다."""

    import io
    import zipfile

    os.makedirs(french_cache, exist_ok=True)
    payload = urllib.request.urlopen(FRENCH_BASE + BEME_ZIP, timeout=90).read()
    with zipfile.ZipFile(io.BytesIO(payload)) as bundle:
        for inner in bundle.namelist():
            with open(os.path.join(french_cache, inner), "wb") as handle:
                handle.write(bundle.read(inner))

    os.makedirs(rate_cache, exist_ok=True)
    spread = urllib.request.urlopen(FRED_CSV + TERM_SPREAD_ID, timeout=60).read()
    with open(os.path.join(rate_cache, f"{TERM_SPREAD_ID}.csv"), "wb") as handle:
        handle.write(spread)


def load_book_to_market(cache_dir: str = FRENCH_CACHE) -> pd.DataFrame:
    """장부가/시가 정렬 포트폴리오의 일간 가치가중 수익률."""

    with open(os.path.join(cache_dir, BEME_CSV), encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    frame = _to_frame(_section(lines, VALUE_WEIGHTED_MARKER, EQUAL_WEIGHTED_MARKER))
    frame.columns = [str(name).strip() for name in frame.columns]
    return frame


def load_term_spread(cache_dir: str = RATE_CACHE) -> pd.Series:
    """10년-3개월 기간 스프레드. 퍼센트포인트 단위 그대로 둔다."""

    frame = pd.read_csv(os.path.join(cache_dir, f"{TERM_SPREAD_ID}.csv"))
    frame.columns = ["date", "value"]
    frame["date"] = pd.to_datetime(frame["date"])
    series = pd.Series(
        pd.to_numeric(frame["value"], errors="coerce").to_numpy(), index=frame["date"]
    )
    return series.dropna()


def weekly_spread(weeks: list[str], cache_dir: str = RATE_CACHE) -> pd.DataFrame:
    """주 F 시점에 **관측된** 기간 스프레드와 그 변화.

    수익률 곡선은 매일 마감되므로 발표 지연이 없다. 그 주 마지막 거래일 값을 쓴다.
    """

    series = load_term_spread(cache_dir)
    stamps = pd.to_datetime(pd.Series(weeks))
    values = [
        float(series[series.index <= stamp].iloc[-1])
        if bool((series.index <= stamp).any())
        else float("nan")
        for stamp in stamps
    ]
    frame = pd.DataFrame({"term_spread": values}, index=pd.Index(weeks, name="week"))
    frame["term_spread_change"] = frame["term_spread"] - frame["term_spread"].shift(
        SPREAD_CHANGE_WEEKS
    )
    return frame
