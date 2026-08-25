"""국면별·산업별 전방 상대수익률의 **분포**. 평균만으로는 구분되는지 알 수 없다.

평균이 다르지만 분포가 완전히 겹치는 두 국면은 실무에서 구분되지 않는다. 그래서 폭과
겹침을 함께 적는다.

``overlap_coefficient``   두 분포가 공유하는 넓이. 1이면 완전히 겹친다.
``probability_superior``  한 국면에서 뽑은 값이 다른 국면 값보다 클 확률. 0.5면 무정보.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .french import INDUSTRIES
from .labels import PHASES

#: 겹침 계수를 잴 때 쓰는 공통 격자 칸 수. 표본이 얇은 국면이 있어 과하게 잘게 나누지 않는다.
OVERLAP_BINS: int = 24

#: 이보다 관측이 적은 칸은 통계를 내지 않는다. 18주짜리 침체에서 26주 전방창을 닫고 나면
#: 남는 것이 얼마 없다 — 그 사실을 감추지 않기 위한 문턱이다.
MINIMUM_OBSERVATIONS: int = 8


def overlap_coefficient(first: np.ndarray, second: np.ndarray, bins: int = OVERLAP_BINS) -> float:
    """두 표본 분포의 겹치는 넓이."""

    if first.size == 0 or second.size == 0:
        return float("nan")
    lo = float(min(first.min(), second.min()))
    hi = float(max(first.max(), second.max()))
    if hi <= lo:
        return 1.0
    edges = np.linspace(lo, hi, bins + 1)
    a, _ = np.histogram(first, bins=edges)
    b, _ = np.histogram(second, bins=edges)
    return float(np.minimum(a / a.sum(), b / b.sum()).sum())


def probability_superior(first: np.ndarray, second: np.ndarray) -> float:
    """P(X > Y) + P(X = Y)/2. 순위 기반이라 꼬리에 휘둘리지 않는다."""

    if first.size == 0 or second.size == 0:
        return float("nan")
    both = np.concatenate([first, second])
    ranks = pd.Series(both).rank().to_numpy()
    total = ranks[: first.size].sum()
    n, m = first.size, second.size
    return float((total - n * (n + 1) / 2) / (n * m))


def _summary(values: np.ndarray) -> dict[str, Any]:
    if values.size < MINIMUM_OBSERVATIONS:
        return {"observations": int(values.size), "sufficient": False}
    return {
        "observations": int(values.size),
        "sufficient": True,
        "mean": round(float(values.mean()), 6),
        "median": round(float(np.median(values)), 6),
        "sd": round(float(values.std(ddof=1)), 6),
        "p10": round(float(np.quantile(values, 0.10)), 6),
        "p90": round(float(np.quantile(values, 0.90)), 6),
        "share_positive": round(float((values > 0).mean()), 4),
    }


def cells(phase: pd.Series, relative: pd.DataFrame) -> dict[str, dict[str, dict[str, Any]]]:
    """국면 x 산업 칸마다 분포 요약. 4 x 12 = 48칸."""

    out: dict[str, dict[str, dict[str, Any]]] = {}
    for name in PHASES:
        mask = phase.reindex(relative.index).eq(name)
        out[name] = {}
        for industry in INDUSTRIES:
            values = relative.loc[mask, industry].dropna().to_numpy()
            out[name][industry] = _summary(values)
    return out


def separability(phase: pd.Series, relative: pd.DataFrame) -> list[dict[str, Any]]:
    """산업마다 가장 좋은 국면과 가장 나쁜 국면을 찾고, 그 둘이 실제로 갈리는지 본다."""

    rows: list[dict[str, Any]] = []
    aligned = phase.reindex(relative.index)
    for industry in INDUSTRIES:
        samples = {
            name: relative.loc[aligned.eq(name), industry].dropna().to_numpy() for name in PHASES
        }
        usable = {
            name: values for name, values in samples.items() if values.size >= MINIMUM_OBSERVATIONS
        }
        if len(usable) < 2:
            rows.append({"industry": industry, "comparable_phases": len(usable)})
            continue
        best = max(usable, key=lambda name: float(usable[name].mean()))
        worst = min(usable, key=lambda name: float(usable[name].mean()))
        rows.append(
            {
                "industry": industry,
                "comparable_phases": len(usable),
                "best_phase": best,
                "worst_phase": worst,
                "best_mean": round(float(usable[best].mean()), 6),
                "worst_mean": round(float(usable[worst].mean()), 6),
                "spread": round(float(usable[best].mean() - usable[worst].mean()), 6),
                "overlap_coefficient": round(overlap_coefficient(usable[best], usable[worst]), 4),
                "probability_superior": round(probability_superior(usable[best], usable[worst]), 4),
            }
        )
    return rows
