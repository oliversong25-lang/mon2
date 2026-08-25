"""Fama-French 정렬 포트폴리오 — **월간**.

이익/가격, 현금흐름/가격, 배당수익률 정렬은 Fama-French가 일간으로 내주지 않는다.
그래서 이 단계는 월간 격자에서 돈다. 비교가 되도록 장부가/시가도 같은 월간 격자에서
다시 계산한다 — 트랙 19의 주간 수치와 나란히 놓으려면 빈도를 맞춰야 한다.

월간이라 관측이 1994년 이후 385개뿐이다. 주간 1668개보다 검정력이 낮고, 그래서 통과가
더 어렵다. 유리한 방향이 아니므로 그대로 간다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Final

import pandas as pd

from ..phase_returns.french import CACHE_DIR as FRENCH_CACHE
from ..phase_returns.french import MISSING

#: 월간 파일에서 가치가중 절을 여는 표시. 파일마다 문구가 조금씩 다르다.
MONTHLY_MARKERS: Final[tuple[str, ...]] = (
    "Average Value Weight Returns -- Monthly",
    "Value Weight Returns -- Monthly",
)
MONTHLY_END_MARKERS: Final[tuple[str, ...]] = (
    "Average Equal Weighted Returns -- Monthly",
    "Equal Weight Returns -- Monthly",
)


@dataclass(frozen=True)
class Sort:
    """한 정렬. ``family``가 가치 가족에 드는지를 가른다."""

    key: str
    label: str
    filename: str
    family: str

    @property
    def is_value_proxy(self) -> bool:
        return self.family == "value"


#: 이 단계가 검정하는 정렬. 영업이익률은 가족이 다르다.
SORTS: Final[tuple[Sort, ...]] = (
    Sort("book_to_market", "장부가/시가 (B/M)", "Portfolios_Formed_on_BE-ME.csv", "value"),
    Sort("earnings_to_price", "이익/가격 (E/P)", "Portfolios_Formed_on_E-P.csv", "value"),
    Sort("cashflow_to_price", "현금흐름/가격 (CF/P)", "Portfolios_Formed_on_CF-P.csv", "value"),
    Sort("dividend_yield", "배당수익률 (D/P)", "Portfolios_Formed_on_D-P.csv", "value"),
    Sort(
        "operating_profitability",
        "영업이익률 (OP) — 가치 대리 변수 아님",
        "Portfolios_Formed_on_OP.csv",
        "profitability",
    ),
)

FACTORS_CSV: Final[str] = "F-F_Research_Data_Factors.csv"


def _monthly_frame(rows: list[str]) -> pd.DataFrame:
    import io

    frame = pd.read_csv(io.StringIO("\n".join(rows)))
    frame = frame.rename(columns={frame.columns[0]: "month"})
    frame["month"] = frame["month"].astype(str).str.strip()
    frame = frame[frame["month"].str.fullmatch(r"\d{6}")]
    frame["month"] = frame["month"].str[:4] + "-" + frame["month"].str[4:]
    frame = frame.set_index("month")
    frame.columns = [str(name).strip() for name in frame.columns]
    frame = frame.astype(float)
    for sentinel in MISSING:
        frame = frame.mask(frame.eq(sentinel))
    # Fama-French는 퍼센트로 준다.
    return frame / 100.0


def _section(lines: list[str]) -> list[str]:
    begin = next(
        (i for i, line in enumerate(lines) if any(m in line for m in MONTHLY_MARKERS)),
        None,
    )
    if begin is None:
        raise ValueError("월간 가치가중 절을 찾지 못했다")
    stop = next(
        (
            i
            for i, line in enumerate(lines)
            if i > begin and any(m in line for m in MONTHLY_END_MARKERS)
        ),
        len(lines),
    )
    body = lines[begin + 1 : stop]
    return [line for line in body if line[:6].strip().isdigit() or line.startswith(",")]


def load(sort: Sort, cache_dir: str = FRENCH_CACHE) -> pd.DataFrame:
    """한 정렬의 월간 가치가중 수익률."""

    with open(os.path.join(cache_dir, sort.filename), encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    return _monthly_frame(_section(lines))


def load_market(cache_dir: str = FRENCH_CACHE) -> pd.Series:
    """월간 시장 총수익률. ``Mkt-RF + RF``."""

    with open(os.path.join(cache_dir, FACTORS_CSV), encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    header = next(i for i, line in enumerate(lines) if line.startswith(",Mkt-RF"))
    rows = [lines[header]] + [line for line in lines[header + 1 :] if line[:6].strip().isdigit()]
    frame = _monthly_frame(rows)
    return frame["Mkt-RF"] + frame["RF"]


def spread(frame: pd.DataFrame, high: str, low: str) -> pd.Series:
    """고-저 스프레드. 두 열 중 하나라도 없으면 빈 계열."""

    if high not in frame.columns or low not in frame.columns:
        return pd.Series(dtype=float)
    return (frame[high] - frame[low]).dropna()


def month_window(first: str, last: str, index: pd.Index) -> list[str]:
    """``YYYY-MM`` 경계로 자른 월 목록."""

    return [str(month) for month in index if first <= str(month) <= last]
