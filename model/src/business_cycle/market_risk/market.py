"""국면별 시장 수익과 위험. 동시점과 전방을 갈라서 낸다.

**동시점**은 국면을 서술한다 — "그 국면이던 주들은 어땠는가".
**전방**은 결정이 쓸 수 있었던 것이다 — "그 국면이라고 부른 날부터 앞으로 어땠는가".

둘을 섞으면 안 된다. 동시점 수치가 좋으면 국면이 시장을 잘 요약하는 것이고, 전방
수치가 좋아야 노출을 바꿀 근거가 된다.

## 무엇을 재는가

평균과 중앙값, 변동성과 **하방변동성**, 국면 안의 최대 낙폭과 국면 안에서 **시작한**
낙폭, 큰 음의 주의 빈도, 그리고 분포 겹침. 평균만 보면 꼬리가 어디 있는지 알 수 없고,
노출 결정은 꼬리에 대한 결정이다.

## 국면 안 낙폭과 국면에서 시작한 낙폭

앞의 것은 국면 구간을 잘라서 그 안의 최대 낙폭을 잰다 — 구간이 짧으면 작게 나온다.
뒤의 것은 국면 안의 어느 주에서 고점을 찍고 그 뒤로 얼마나 내려갔는지를 **구간 밖까지**
따라간다. 침체기가 짧게 잡히는 모델에서는 뒤의 것이 실제 위험에 가깝다.
"""

from __future__ import annotations

from typing import Any, Final

import numpy as np
import pandas as pd

from ..phase_returns.distribution import overlap_coefficient, probability_superior
from ..phase_returns.labels import PHASES
from . import prespec

WEEKS_PER_YEAR: Final[float] = 52.0

#: 국면에서 시작한 낙폭을 이 주 수까지 따라간다. 1년이면 어떤 침체도 바닥을 지난다.
DRAWDOWN_FOLLOW_WEEKS: Final[int] = 52


def forward_sum(weekly: pd.Series, horizon: int) -> pd.Series:
    """t주에서 본 t+1..t+h의 누적 수익. `phase_returns.forward`와 같은 정렬이다."""

    compounded = pd.Series(np.log1p(weekly.to_numpy(dtype=float)), index=weekly.index)
    rolled = compounded.rolling(horizon).sum().shift(-horizon)
    return pd.Series(np.expm1(rolled.to_numpy()), index=weekly.index, name=weekly.name)


def _annualised_volatility(values: np.ndarray, horizon: int) -> float | None:
    """전방 창의 변동성을 연율로. 창이 겹치므로 **표본 크기를 부풀리지 않도록** 주의한다.

    겹친 관측의 표준편차 자체는 편향되지 않지만 그 표준오차는 겹침만큼 커진다. 그래서
    이 값 옆에는 항상 에피소드 수를 놓는다.
    """

    clean = values[np.isfinite(values)]
    if clean.size < 2:
        return None
    return round(float(clean.std(ddof=1)) * float(np.sqrt(WEEKS_PER_YEAR / horizon)), 4)


def _downside_volatility(values: np.ndarray, horizon: int) -> float | None:
    """0 미만 관측만으로 잰 변동성. 시장 초과수익이 이미 무위험 초과다."""

    clean = values[np.isfinite(values)]
    negative = clean[clean < prespec.DOWNSIDE_THRESHOLD]
    if negative.size < 2:
        return None
    # 0을 기준으로 한 반편차. 평균 주위가 아니라 **손실 크기**를 재는 것이 목적이다.
    return round(float(np.sqrt(np.mean(negative**2))) * float(np.sqrt(WEEKS_PER_YEAR / horizon)), 4)


def _max_drawdown(weekly: np.ndarray) -> float:
    clean = np.nan_to_num(weekly)
    curve = np.cumprod(1.0 + clean)
    peak = np.maximum.accumulate(curve)
    return round(float((curve / peak - 1.0).min()), 4)


def _blocks(phase: pd.Series, name: str) -> list[tuple[int, int]]:
    values = [str(item) for item in phase.tolist()]
    spans: list[tuple[int, int]] = []
    start: int | None = None
    for position, value in enumerate(values):
        if value == name and start is None:
            start = position
        elif value != name and start is not None:
            spans.append((start, position - 1))
            start = None
    if start is not None:
        spans.append((start, len(values) - 1))
    return spans


def drawdown_beginning_within(
    phase: pd.Series, weekly: pd.Series, name: str, follow: int = DRAWDOWN_FOLLOW_WEEKS
) -> dict[str, Any]:
    """국면 안의 어느 주에서 고점을 찍고 그 뒤로 얼마나 내려갔는가.

    구간 밖까지 따라간다. 침체기가 짧게 잡히는 모델에서는 구간을 잘라 재면 위험이
    실제보다 작아 보인다.
    """

    values = weekly.to_numpy(dtype=float)
    worst = 0.0
    worst_start: str | None = None
    for start, end in _blocks(phase, name):
        for position in range(start, end + 1):
            window = values[position : min(position + follow + 1, len(values))]
            if window.size < 2:
                continue
            curve = np.cumprod(1.0 + np.nan_to_num(window))
            trough = float(curve.min() - 1.0)
            if trough < worst:
                worst = trough
                worst_start = str(phase.index[position])
    return {
        "worst_drawdown_starting_in_phase": round(worst, 4),
        "started_on": worst_start,
        "followed_weeks": follow,
    }


def contemporaneous(phase: pd.Series, weekly: pd.Series) -> list[dict[str, Any]]:
    """국면 서술 — 그 국면이던 주들은 어땠는가."""

    rows: list[dict[str, Any]] = []
    for name in PHASES:
        mask = phase.eq(name)
        values = weekly[mask].to_numpy(dtype=float)
        clean = values[np.isfinite(values)]
        blocks = _blocks(phase, name)
        rows.append(
            {
                "phase": name,
                "weeks": int(mask.sum()),
                "episodes": len(blocks),
                "mean_weekly": round(float(clean.mean()), 6) if clean.size else None,
                "median_weekly": round(float(np.median(clean)), 6) if clean.size else None,
                "annualised_volatility": _annualised_volatility(clean, 1),
                "annualised_downside_volatility": _downside_volatility(clean, 1),
                "large_negative_week_frequency": (
                    round(float((clean <= prespec.LARGE_NEGATIVE_WEEK).mean()), 4)
                    if clean.size
                    else None
                ),
                "max_drawdown_within_phase": (
                    min(
                        (
                            _max_drawdown(weekly.to_numpy(dtype=float)[start : end + 1])
                            for start, end in blocks
                        ),
                        default=None,
                    )
                    if blocks
                    else None
                ),
                **drawdown_beginning_within(phase, weekly, name),
            }
        )
    return rows


def forward(phase: pd.Series, weekly: pd.Series, horizon: int) -> list[dict[str, Any]]:
    """결정이 쓸 수 있었던 것 — 국면이라 부른 날부터 앞으로 h주."""

    ahead = forward_sum(weekly, horizon)
    rows: list[dict[str, Any]] = []
    for name in PHASES:
        values = ahead[phase.eq(name)].to_numpy(dtype=float)
        clean = values[np.isfinite(values)]
        rows.append(
            {
                "phase": name,
                "horizon_weeks": horizon,
                "weeks": int(phase.eq(name).sum()),
                "episodes": len(_blocks(phase, name)),
                "observations": int(clean.size),
                "mean_forward": round(float(clean.mean()), 6) if clean.size else None,
                "median_forward": round(float(np.median(clean)), 6) if clean.size else None,
                "annualised_volatility": _annualised_volatility(clean, horizon),
                "annualised_downside_volatility": _downside_volatility(clean, horizon),
                "share_negative": (round(float((clean < 0.0).mean()), 4) if clean.size else None),
                "worst_forward": round(float(clean.min()), 4) if clean.size else None,
                "fifth_percentile": (
                    round(float(np.quantile(clean, 0.05)), 4) if clean.size else None
                ),
            }
        )
    return rows


def downside_ratio(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """가장 위험한 국면 ÷ 가장 안전한 국면. 판정 통계량이다."""

    usable = [row for row in rows if row["annualised_downside_volatility"] is not None]
    if len(usable) < 2:
        return {"ratio": None, "riskiest": None, "safest": None, "comparable_phases": len(usable)}
    riskiest = max(usable, key=lambda row: float(row["annualised_downside_volatility"]))
    safest = min(usable, key=lambda row: float(row["annualised_downside_volatility"]))
    return {
        "ratio": round(
            float(riskiest["annualised_downside_volatility"])
            / float(safest["annualised_downside_volatility"]),
            3,
        ),
        "riskiest": riskiest["phase"],
        "riskiest_downside": riskiest["annualised_downside_volatility"],
        "riskiest_episodes": riskiest["episodes"],
        "safest": safest["phase"],
        "safest_downside": safest["annualised_downside_volatility"],
        "safest_episodes": safest["episodes"],
        "comparable_phases": len(usable),
    }


def overlap(
    phase: pd.Series, weekly: pd.Series, horizon: int, first: str, second: str
) -> dict[str, Any]:
    """두 국면의 전방 수익 분포가 얼마나 포개지는가. 평균만으로는 알 수 없다."""

    ahead = forward_sum(weekly, horizon)
    a = ahead[phase.eq(first)].to_numpy(dtype=float)
    b = ahead[phase.eq(second)].to_numpy(dtype=float)
    a = a[np.isfinite(a)]
    b = b[np.isfinite(b)]
    if a.size == 0 or b.size == 0:
        return {"first": first, "second": second, "overlap_coefficient": None}
    return {
        "first": first,
        "second": second,
        "horizon_weeks": horizon,
        "overlap_coefficient": round(overlap_coefficient(a, b), 4),
        "probability_second_exceeds_first": round(probability_superior(b, a), 4),
        "observations": [int(a.size), int(b.size)],
    }
