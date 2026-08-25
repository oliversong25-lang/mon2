"""Fama-French 12산업 포트폴리오와 시장 요인. **내부 검증 전용**.

Ken French Data Library 자료는 Fama·French의 저작물이며 CRSP에서 파생된다. 여기서는
국면 분류의 유용성을 재는 내부 검증에만 쓰고, 제품에 실어 내보내지 않는다. 산업 분류를
SIC 기반 FF로 잡은 것도 같은 이유다 — GICS 분류 체계 자체가 S&P·MSCI 소유라 직접 지수를
만들어도 회피가 되지 않는다.

주간 격자는 모델을 따른다. 모델의 주는 금요일로 끝나므로 (F-7, F] 구간의 일간 수익률을
복리로 묶어 그 주의 수익률로 삼는다.
"""

from __future__ import annotations

import io
import os
import urllib.request
import zipfile
from typing import Final

import pandas as pd

FRENCH_BASE: Final[str] = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"

INDUSTRY_ZIP: Final[str] = "12_Industry_Portfolios_daily_CSV.zip"
FACTOR_ZIP: Final[str] = "F-F_Research_Data_Factors_daily_CSV.zip"

INDUSTRY_CSV: Final[str] = "12_Industry_Portfolios_Daily.csv"
FACTOR_CSV: Final[str] = "F-F_Research_Data_Factors_daily.csv"

CACHE_DIR: Final[str] = "data/cache/famafrench"

#: 12산업. FF 표기 그대로 둔다. 번역하면 원자료와 대조가 어려워진다.
INDUSTRIES: Final[tuple[str, ...]] = (
    "NoDur",
    "Durbl",
    "Manuf",
    "Enrgy",
    "Chems",
    "BusEq",
    "Telcm",
    "Utils",
    "Shops",
    "Hlth",
    "Money",
    "Other",
)

#: FF의 결측 표기. 그대로 두면 -99.99%가 되어 버린다.
MISSING: Final[tuple[float, ...]] = (-99.99, -999.0)

#: 가치가중 수익률 절만 쓴다. 동일가중은 소형주 쏠림이 커 산업 신호와 섞인다.
VALUE_WEIGHTED_MARKER: Final[str] = "Average Value Weighted Returns -- Daily"
EQUAL_WEIGHTED_MARKER: Final[str] = "Average Equal Weighted Returns -- Daily"


def download(cache_dir: str = CACHE_DIR) -> None:
    """FF 원자료를 내려받아 캐시에 둔다. 이미 있으면 건드리지 않는다."""

    os.makedirs(cache_dir, exist_ok=True)
    for archive in (INDUSTRY_ZIP, FACTOR_ZIP):
        payload = urllib.request.urlopen(FRENCH_BASE + archive, timeout=60).read()
        with zipfile.ZipFile(io.BytesIO(payload)) as bundle:
            for inner in bundle.namelist():
                with open(os.path.join(cache_dir, inner), "wb") as handle:
                    handle.write(bundle.read(inner))


def _section(lines: list[str], start_marker: str, end_marker: str | None) -> list[str]:
    """머리말과 다른 절을 걷어내고 자료 줄만 남긴다."""

    begin = next(i for i, line in enumerate(lines) if start_marker in line)
    stop = len(lines)
    if end_marker is not None:
        stop = next(i for i, line in enumerate(lines) if end_marker in line)
    body = lines[begin + 1 : stop]
    return [line for line in body if line[:8].strip().isdigit() or line.startswith(",")]


def _to_frame(rows: list[str]) -> pd.DataFrame:
    frame = pd.read_csv(io.StringIO("\n".join(rows)))
    frame = frame.rename(columns={frame.columns[0]: "date"})
    frame["date"] = pd.to_datetime(frame["date"].astype(str), format="%Y%m%d")
    frame = frame.set_index("date").astype(float)
    for sentinel in MISSING:
        frame = frame.mask(frame.eq(sentinel))
    # FF는 퍼센트로 준다. 소수로 바꿔 둬야 복리 계산이 맞는다.
    return frame / 100.0


def load_daily(cache_dir: str = CACHE_DIR) -> tuple[pd.DataFrame, pd.DataFrame]:
    """일간 산업 수익률과 요인. (industries, factors)."""

    with open(os.path.join(cache_dir, INDUSTRY_CSV), encoding="utf-8") as handle:
        industry_lines = handle.read().splitlines()
    with open(os.path.join(cache_dir, FACTOR_CSV), encoding="utf-8") as handle:
        factor_lines = handle.read().splitlines()

    industries = _to_frame(_section(industry_lines, VALUE_WEIGHTED_MARKER, EQUAL_WEIGHTED_MARKER))
    # 요인 파일에는 절 표시가 없다. 열 이름 줄(`,Mkt-RF,...`)부터 잘라야 첫 관측일을
    # 머리글로 삼는 사고가 나지 않는다.
    header = next(i for i, line in enumerate(factor_lines) if line.startswith(",Mkt-RF"))
    factors = _to_frame(
        [factor_lines[header]]
        + [line for line in factor_lines[header + 1 :] if line[:8].strip().isdigit()]
    )
    return industries[list(INDUSTRIES)], factors


def to_weekly(daily: pd.DataFrame, weeks: list[str]) -> pd.DataFrame:
    """모델 주간 격자에 맞춰 복리로 묶는다.

    ``weeks``는 모델이 쓰는 금요일 목록이다. 주 F의 수익률은 (F-7, F] 구간이며, 거래일이
    하나도 없는 주는 결측으로 남긴다 — 0으로 채우면 없는 주를 무수익 주로 만들어 버린다.
    """

    stamps = pd.to_datetime(pd.Series(weeks))
    edges = [stamp - pd.Timedelta(days=7) for stamp in stamps]
    rows: list[pd.Series] = []
    for lower, upper in zip(edges, stamps, strict=True):
        window = daily[(daily.index > lower) & (daily.index <= upper)]
        if window.empty:
            rows.append(pd.Series({column: float("nan") for column in daily.columns}))
        else:
            rows.append((1.0 + window).prod() - 1.0)
    weekly = pd.DataFrame(rows)
    weekly.index = pd.Index(weeks, name="week")
    return weekly


def weekly_panel(weeks: list[str], cache_dir: str = CACHE_DIR) -> pd.DataFrame:
    """산업 12개 + 시장(``MKT``)의 주간 총수익률.

    시장은 ``Mkt-RF + RF``다. 상대수익률을 산업에서 시장을 빼서 구할 것이므로 무위험
    수익률은 양쪽에서 상쇄되지만, 초과수익 형태로 섞어 쓰지 않도록 총수익률로 통일한다.
    """

    industries, factors = load_daily(cache_dir)
    market = (factors["Mkt-RF"] + factors["RF"]).to_frame("MKT")
    daily = industries.join(market, how="inner")
    return to_weekly(daily, weeks)
