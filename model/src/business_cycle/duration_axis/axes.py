"""두 축을 **같은 월간 격자**에 올린다.

정렬 포트폴리오가 월간뿐이므로 업종도 월간으로 내린다. 빈도가 다르면 천장 비교가 축의
차이가 아니라 자의 차이를 재게 된다.

## 듀레이션 순서

무배당군이 가장 긴 듀레이션이다 — 배당을 내지 않는 기업은 현금이 먼 미래에 있다. 거기서
분위를 따라 올라가면 배당수익률이 높아지고 듀레이션이 짧아진다. 통 이름을 그 순서대로
붙여 두어 결과를 읽을 때 방향이 헷갈리지 않게 한다.

E/P·CF/P·B/M도 같은 모양이다. 낮은 쪽이 비싸고 길다.

## 국면 라벨을 월로 내리는 법

그 달의 **마지막 주** 라벨을 쓴다. 그 시점에 알 수 있었던 것이고, 달 안의 라벨을
평균 내면 존재하지 않는 상태가 만들어진다.
"""

from __future__ import annotations

import io
import os
from typing import Any, Final

import pandas as pd

from ..phase_returns.french import MISSING
from ..phase_returns.french import load_daily as load_industries
from ..value_proxies.sorts import MONTHLY_END_MARKERS, MONTHLY_MARKERS

#: 정렬 파일 이름. 트랙 20이 내려받아 둔 것을 그대로 읽는다.
FILES: Final[dict[str, str]] = {
    "dividend_yield": "Portfolios_Formed_on_D-P.csv",
    "earnings_to_price": "Portfolios_Formed_on_E-P.csv",
    "cashflow_to_price": "Portfolios_Formed_on_CF-P.csv",
    "book_to_market": "Portfolios_Formed_on_BE-ME.csv",
}

LABEL: Final[dict[str, str]] = {
    "dividend_yield": "배당수익률 (D/P)",
    "earnings_to_price": "이익/가격 (E/P)",
    "cashflow_to_price": "현금흐름/가격 (CF/P)",
    "book_to_market": "장부가/시가 (B/M)",
}

#: 무배당·무이익 통의 열 이름. 파일마다 표기가 다르다.
ZERO_COLUMNS: Final[tuple[str, ...]] = ("<=0", "<= 0")

#: 10분위 열. 긴 듀레이션(싼 지표가 낮은 쪽)에서 짧은 쪽으로.
DECILES: Final[tuple[str, ...]] = (
    "Lo 10",
    "2-Dec",
    "3-Dec",
    "4-Dec",
    "5-Dec",
    "6-Dec",
    "7-Dec",
    "8-Dec",
    "9-Dec",
    "Hi 10",
)

#: 5분위 열.
QUINTILES: Final[tuple[str, ...]] = ("Lo 20", "Qnt 2", "Qnt 3", "Qnt 4", "Hi 20")


def _monthly_section(path: str) -> pd.DataFrame:
    """가치가중 월간 절만 잘라 읽는다. 동일가중 절이 뒤에 이어 붙어 있다."""

    with open(path, encoding="latin-1") as handle:
        lines = handle.read().split("\n")

    start: int | None = None
    for position, line in enumerate(lines):
        if any(marker in line for marker in MONTHLY_MARKERS):
            start = position + 1
            break
    if start is None:
        raise ValueError(f"{path}에서 가치가중 월간 절을 찾지 못했다")

    end = len(lines)
    for position in range(start, len(lines)):
        if any(marker in lines[position] for marker in MONTHLY_END_MARKERS):
            end = position
            break

    frame = pd.read_csv(io.StringIO("\n".join(lines[start:end])))
    frame = frame.rename(columns={frame.columns[0]: "month"})
    frame["month"] = frame["month"].astype(str).str.strip()
    frame = frame[frame["month"].str.fullmatch(r"\d{6}")]
    frame["month"] = frame["month"].str[:4] + "-" + frame["month"].str[4:]
    frame = frame.set_index("month")
    frame.columns = [str(name).strip() for name in frame.columns]
    frame = frame.astype(float)
    for sentinel in MISSING:
        frame = frame.mask(frame.eq(sentinel))
    return frame / 100.0


def duration_axis(
    proxy: str, cache_dir: str, buckets: str = "deciles"
) -> tuple[pd.DataFrame, list[str]]:
    """한 대리변수의 듀레이션 축. 긴 쪽에서 짧은 쪽으로 정렬된 통들."""

    frame = _monthly_section(os.path.join(cache_dir, FILES[proxy]))
    zero = next((name for name in ZERO_COLUMNS if name in frame.columns), None)
    tail = DECILES if buckets == "deciles" else QUINTILES
    present = [name for name in tail if name in frame.columns]
    ordered = ([zero] if zero else []) + present
    renamed = {}
    for position, name in enumerate(ordered):
        renamed[name] = "D00_zero" if name == zero else f"D{position:02d}_{name.replace(' ', '')}"
    axis = frame[ordered].rename(columns=renamed)
    return axis, list(axis.columns)


def industry_axis(cache_dir: str, weeks: list[str]) -> tuple[pd.DataFrame, list[str]]:
    """FF12를 같은 월간 격자로 내린다. 일간을 달 안에서 복리로 묶는다."""

    industries, _ = load_industries(cache_dir)
    stamps = pd.to_datetime(industries.index)
    grouped = industries.copy()
    grouped.index = pd.Index(
        [f"{stamp.year:04d}-{stamp.month:02d}" for stamp in stamps], name="month"
    )
    monthly = (1.0 + grouped).groupby(level=0).prod() - 1.0
    del weeks  # 서명을 맞추기 위한 자리. 월간 격자는 자료 자체가 정한다.
    return monthly, list(monthly.columns)


def market_monthly(cache_dir: str) -> pd.Series:
    """월간 시장 수익. 정렬 파일과 같은 절차로 만든 것을 쓴다."""

    from ..value_proxies.sorts import FACTORS_CSV

    frame = _monthly_section_factors(os.path.join(cache_dir, FACTORS_CSV))
    return (frame["Mkt-RF"] + frame["RF"]).rename("MKT")


def _monthly_section_factors(path: str) -> pd.DataFrame:
    """요인 파일은 절 표시가 없다. 월 형식 줄만 걸러 읽는다."""

    with open(path, encoding="latin-1") as handle:
        rows = [line for line in handle.read().split("\n") if line.strip()]
    header = next(
        position for position, line in enumerate(rows) if "Mkt-RF" in line and "SMB" in line
    )
    frame = pd.read_csv(io.StringIO("\n".join(rows[header:])))
    frame = frame.rename(columns={frame.columns[0]: "month"})
    frame["month"] = frame["month"].astype(str).str.strip()
    frame = frame[frame["month"].str.fullmatch(r"\d{6}")]
    frame["month"] = frame["month"].str[:4] + "-" + frame["month"].str[4:]
    frame = frame.set_index("month")
    frame.columns = [str(name).strip() for name in frame.columns]
    frame = frame.astype(float)
    for sentinel in MISSING:
        frame = frame.mask(frame.eq(sentinel))
    return frame / 100.0


def monthly_phase(phase: pd.Series) -> pd.Series:
    """주간 라벨을 월로 내린다. 그 달 **마지막 주**의 라벨이다."""

    frame = pd.DataFrame({"phase": phase.astype(str)})
    frame["month"] = [str(week)[:7] for week in phase.index]
    last = frame.groupby("month")["phase"].last()
    return pd.Series(last.to_numpy(), index=pd.Index(last.index, name="month"), name="phase")


def align(
    axis: pd.DataFrame, market: pd.Series, phase: pd.Series
) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    """세 조각을 같은 달 목록으로 맞춘다. 하나라도 없는 달은 버린다."""

    months = [
        month for month in axis.index if month in set(market.index) and month in set(phase.index)
    ]
    trimmed = axis.loc[months].dropna(how="any")
    months = list(trimmed.index)
    return trimmed, market.loc[months], phase.loc[months]


def describe(axis: pd.DataFrame, columns: list[str]) -> dict[str, Any]:
    """축이 무엇인지 산출물에 적어 둔다. 통 순서를 나중에 다시 확인할 수 있어야 한다."""

    return {
        "buckets": len(columns),
        "order_long_to_short": columns,
        "first_month": str(axis.index[0]),
        "last_month": str(axis.index[-1]),
        "note": (
            "왼쪽이 긴 듀레이션이다 — 무배당(또는 무이익) 통이 맨 앞이고, 분위를 따라 "
            "지표가 높아질수록 듀레이션이 짧아진다."
        ),
    }
