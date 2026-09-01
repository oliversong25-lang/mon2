"""국면 기반 순환매를 **시장 보유**와 **12산업 동일가중**에 견준다.

완벽한 국면 시계라도 순환매가 이 둘을 못 이기면 쓸 자리가 없다. 그래서 정확도보다 이쪽이
먼저다.

전방 수익률 표가 아니라 **주간** 상대수익률로 굴린다. 26주 전방 표로 매주 거래하면 같은
수익을 26번 세게 된다.

가중치는 **확장 창**으로만 정한다. 전체 표본으로 국면별 우량 산업을 고르면 그것은 성과가
아니라 정답을 미리 본 것이다. 그 상한도 함께 적되, 상한이라고 이름을 붙여 둔다.
"""

from __future__ import annotations

from typing import Any, Final

import numpy as np
import pandas as pd

from .forward import MARKET
from .french import INDUSTRIES
from .labels import PHASES

#: 한 국면을 이 주 수만큼 본 뒤에야 그 국면으로 거래한다. 그 전에는 시장을 든다.
MINIMUM_PHASE_HISTORY: Final[int] = 52

#: 몇 개 산업을 드는가. 12개 중 3개면 집중되면서도 한 종목 사고에 무너지지 않는다.
TOP_K: Final[int] = 3

WEEKS_PER_YEAR: Final[float] = 52.0


def weekly_relative(weekly: pd.DataFrame) -> pd.DataFrame:
    """주간 상대수익률. 산업 - 시장."""

    return weekly[list(INDUSTRIES)].sub(weekly[MARKET], axis=0)


def _expanding_weights(
    phase: np.ndarray, relative: np.ndarray, top_k: int, minimum: int
) -> np.ndarray:
    """t 시점까지 관측된 것만으로 t의 비중을 정한다.

    t주의 비중은 t+1주 수익을 받는다. 그래서 추정에 쓰는 마지막 관측은 t주의 수익까지다.
    """

    weeks, count = relative.shape
    weights = np.zeros((weeks, count))
    totals = {name: np.zeros(count) for name in PHASES}
    seen = {name: 0 for name in PHASES}

    for position in range(weeks):
        name = phase[position]
        if name in totals and seen[name] >= minimum:
            ranked = np.argsort(-(totals[name] / seen[name]))
            weights[position, ranked[:top_k]] = 1.0 / top_k
        row = relative[position]
        if name in totals and bool(np.isfinite(row).all()):
            totals[name] += row
            seen[name] += 1
    return weights


def _full_sample_weights(phase: np.ndarray, relative: np.ndarray, top_k: int) -> np.ndarray:
    """전체 표본으로 고른 비중. 실현 가능한 성과가 아니라 **상한**이다."""

    weeks, count = relative.shape
    weights = np.zeros((weeks, count))
    table: dict[str, np.ndarray] = {}
    for name in PHASES:
        mask = phase == name
        if int(mask.sum()) == 0:
            continue
        table[name] = np.asarray(np.nanmean(relative[mask], axis=0))
    for position in range(weeks):
        name = phase[position]
        if name in table:
            ranked = np.argsort(-table[name])
            weights[position, ranked[:top_k]] = 1.0 / top_k
    return weights


def _annualise(series: np.ndarray) -> float:
    clean = series[np.isfinite(series)]
    if clean.size == 0:
        return float("nan")
    return float(np.expm1(np.log1p(clean).sum() * WEEKS_PER_YEAR / clean.size))


def _drawdown(series: np.ndarray) -> float:
    clean = np.nan_to_num(series)
    curve = np.cumprod(1.0 + clean)
    peak = np.maximum.accumulate(curve)
    return float((curve / peak - 1.0).min())


def _profile(series: np.ndarray, label: str) -> dict[str, Any]:
    clean = series[np.isfinite(series)]
    spread = float(clean.std(ddof=1)) if clean.size > 1 else 0.0
    return {
        "strategy": label,
        "weeks": int(clean.size),
        "annualised_relative_return": round(_annualise(series), 4),
        "annualised_volatility": round(spread * float(np.sqrt(WEEKS_PER_YEAR)), 4),
        "information_ratio": (
            round(float(clean.mean()) / spread * float(np.sqrt(WEEKS_PER_YEAR)), 3)
            if spread > 0
            else None
        ),
        "weeks_positive": round(float((clean > 0).mean()), 4),
        "worst_drawdown_versus_market": round(_drawdown(series), 4),
    }


def _realise(weights: np.ndarray, values: np.ndarray) -> np.ndarray:
    """t주 비중이 t+1주 수익을 받는다. 밀지 않으면 같은 주 수익을 보고 고른 셈이 된다."""

    series = np.full(len(values), np.nan)
    series[1:] = (weights[:-1] * values[1:]).sum(axis=1)
    return series


def run(
    phase: pd.Series,
    weekly: pd.DataFrame,
    top_k: int = TOP_K,
    minimum: int = MINIMUM_PHASE_HISTORY,
) -> dict[str, Any]:
    """확장 창 순환매와 두 기준선."""

    relative = weekly_relative(weekly)
    usable = relative.dropna(how="any")
    aligned = phase.reindex(usable.index).fillna("").astype(str).to_numpy()
    values = usable.to_numpy(dtype=float)

    weights = _expanding_weights(aligned, values, top_k, minimum)
    ceiling = _full_sample_weights(aligned, values, top_k)

    realised = _realise(weights, values)
    realised_ceiling = _realise(ceiling, values)
    equal_weight = values.mean(axis=1)
    market = weekly[MARKET].reindex(usable.index).to_numpy(dtype=float)

    traded = int((weights.sum(axis=1) > 0).sum())
    turnover = float(np.abs(np.diff(weights, axis=0)).sum() / 2 / max(len(weights) - 1, 1))

    return {
        "top_k": top_k,
        "minimum_phase_history_weeks": minimum,
        "weeks": int(len(values)),
        "weeks_traded": traded,
        "weeks_held_market_for_lack_of_history": int(len(weights) - traded),
        "average_weekly_turnover": round(turnover, 4),
        "market_annualised_return": round(_annualise(market), 4),
        "rotation": _profile(realised, "phase rotation (expanding window)"),
        "equal_weight": _profile(equal_weight, "equal weight 12 industries"),
        "rotation_full_sample_ceiling": _profile(
            realised_ceiling, "phase rotation (full-sample ranking, not achievable)"
        ),
        "rotation_minus_equal_weight": round(_annualise(realised) - _annualise(equal_weight), 4),
    }


def shift_null(
    phase: pd.Series,
    weekly: pd.DataFrame,
    minimum_shift: int,
    top_k: int = TOP_K,
    minimum: int = MINIMUM_PHASE_HISTORY,
    stride: int = 4,
) -> dict[str, Any]:
    """국면 라벨을 순환 이동시켜 순환매 성과의 귀무분포를 만든다.

    확장 창 재추정이 이동마다 필요해 ``stride``로 솎는다. 이동 간격이 촘촘해 봐야 서로
    거의 같은 정렬이라 정보가 늘지 않는다.
    """

    relative = weekly_relative(weekly)
    usable = relative.dropna(how="any")
    aligned = phase.reindex(usable.index).fillna("").astype(str).to_numpy()
    values = usable.to_numpy(dtype=float)

    target = _annualise(_realise(_expanding_weights(aligned, values, top_k, minimum), values))

    weeks = len(aligned)
    offsets = [
        offset
        for offset in range(0, weeks, stride)
        if minimum_shift <= offset <= weeks - minimum_shift
    ]
    draws = [
        _annualise(
            _realise(_expanding_weights(np.roll(aligned, offset), values, top_k, minimum), values)
        )
        for offset in offsets
    ]

    array = np.array(draws)
    return {
        "observed_annualised_relative_return": round(target, 4),
        "shifts_used": len(offsets),
        "null_median": round(float(np.median(array)), 4),
        "null_p90": round(float(np.quantile(array, 0.90)), 4),
        "p_value": round(float((int((array >= target).sum()) + 1) / (array.size + 1)), 4),
    }
