"""국면을 **알게 된 뒤** 무슨 일이 일어나는가.

국면 구간 안의 동시 수익률이 아니라, 판정 주 다음 주부터의 전방 수익률을 본다. 순환매는
판정을 보고 나서 실행하는 것이므로 동시 수익률은 쓸 수 없다.

측정은 **상대수익률**이다 — 산업에서 시장을 뺀다. 절대수익률은 시장 방향이 국면과 함께
움직여서, 산업 간 차이가 아니라 시장 베타를 재게 된다.
"""

from __future__ import annotations

from typing import Final

import numpy as np
import pandas as pd

from .french import INDUSTRIES, weekly_panel

#: 4주(약 한 달), 13주(한 분기), 26주(반년). 월 단위 자료 위의 판정이라 4주보다 짧게는
#: 재지 않는다.
HORIZONS: Final[tuple[int, ...]] = (4, 13, 26)

MARKET: Final[str] = "MKT"


def weekly_returns(weeks: list[str], cache_dir: str | None = None) -> pd.DataFrame:
    """모델 주간 격자 위의 산업·시장 주간 총수익률."""

    if cache_dir is None:
        return weekly_panel(weeks)
    return weekly_panel(weeks, cache_dir)


def _compound_forward(series: pd.Series, horizon: int) -> pd.Series:
    """t 시점에서 t+1..t+h를 복리로 묶는다. 창이 모자라면 결측."""

    growth = pd.Series(np.log1p(series.astype(float).to_numpy()), index=series.index)
    rolled = growth.rolling(horizon).sum()
    # rolling은 t-h+1..t를 담는다. 한 칸 앞으로 당겨 t+1..t+h로 만든다.
    forward = rolled.shift(-horizon)
    return pd.Series(np.expm1(forward.to_numpy()), index=series.index)


def forward_relative(weekly: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """판정 주별 전방 상대수익률. 열은 12산업, 값은 산업 - 시장."""

    market = _compound_forward(weekly[MARKET], horizon)
    out = {}
    for industry in INDUSTRIES:
        out[industry] = _compound_forward(weekly[industry], horizon) - market
    frame = pd.DataFrame(out, index=weekly.index)
    frame.index.name = "week"
    return frame


def forward_absolute(weekly: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """전방 절대수익률. 시장·동일가중 기준선을 만들 때만 쓴다."""

    columns = [*INDUSTRIES, MARKET]
    return pd.DataFrame(
        {name: _compound_forward(weekly[name], horizon) for name in columns},
        index=weekly.index,
    )


def coverage(frame: pd.DataFrame) -> dict[str, object]:
    """전방 창이 실제로 닫힌 주가 몇이나 되는지. 표본 한계를 먼저 말하기 위한 것이다."""

    usable = frame.dropna(how="all")
    return {
        "weeks_in_grid": int(len(frame)),
        "weeks_with_a_closed_forward_window": int(len(usable)),
        "first_usable_week": str(usable.index[0]) if len(usable) else None,
        "last_usable_week": str(usable.index[-1]) if len(usable) else None,
    }
