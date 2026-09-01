"""검정 A를 정렬마다 다시 돌린다. 통계는 트랙 19와 같다.

세 창을 모두 싣되 **판정은 판정 창에서만** 내린다. 그 규칙은 자료를 내려받기 전에
``prespec``에 적혔고, 여기서는 그것을 읽어 쓸 뿐 다시 정하지 않는다.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from ..phase_value.premium import hac_t_statistic
from . import prespec
from .sorts import SORTS, Sort, load, load_market, spread


def annualise(series: pd.Series) -> float:
    values = series.dropna().to_numpy(dtype=float)
    if values.size == 0:
        return float("nan")
    return float(np.expm1(np.log1p(values).sum() * prespec.MONTHS_PER_YEAR / values.size))


def profile(series: pd.Series, window: str) -> dict[str, Any]:
    values = series.dropna()
    if len(values) < prespec.MONTHLY_HAC_LAGS + 2:
        return {"window": window, "months": int(len(values)), "thin": True}
    deviation = float(values.std(ddof=1))
    return {
        "window": window,
        "months": int(len(values)),
        "thin": False,
        "annualised": round(annualise(values), 4),
        "annualised_volatility": round(deviation * float(np.sqrt(prespec.MONTHS_PER_YEAR)), 4),
        "sharpe": (
            round(
                float(values.mean()) / deviation * float(np.sqrt(prespec.MONTHS_PER_YEAR)),
                3,
            )
            if deviation > 0
            else None
        ),
        "hac_t": hac_t_statistic(values, lags=prespec.MONTHLY_HAC_LAGS),
        "months_positive": round(float((values > 0).mean()), 4),
    }


def _legs(frame: pd.DataFrame, market: pd.Series, months: list[str]) -> dict[str, Any]:
    """가치 다리와 성장 다리를 시장 대비로 갈라 본다."""

    out: dict[str, Any] = {}
    for name, (high, low) in (
        ("tercile", prespec.PRIMARY_SORT),
        ("decile", prespec.SECONDARY_SORT),
    ):
        if high not in frame.columns or low not in frame.columns:
            continue
        trimmed = frame.reindex(months)
        reference = market.reindex(months)
        out[name] = {
            "high_leg_versus_market": round(annualise(trimmed[high] - reference), 4),
            "low_leg_versus_market": round(annualise(trimmed[low] - reference), 4),
            "high_minus_low": round(annualise(trimmed[high] - trimmed[low]), 4),
        }
    return out


def run_one(sort: Sort, windows: dict[str, list[str]], cache_dir: str) -> dict[str, Any]:
    """한 정렬의 검정 A 전체."""

    frame = load(sort, cache_dir)
    market = load_market(cache_dir)
    primary = spread(frame, *prespec.PRIMARY_SORT)
    secondary = spread(frame, *prespec.SECONDARY_SORT)

    profiles = [profile(primary, "full Fama-French sample")]
    for label, months in windows.items():
        profiles.append(profile(primary.reindex(months), label))

    decision = next(entry for entry in profiles if entry["window"] == prespec.DECISION_WINDOW)
    annualised = decision.get("annualised")
    hac = decision.get("hac_t")

    return {
        "sort": sort.key,
        "label": sort.label,
        "family": sort.family,
        "is_value_proxy": sort.is_value_proxy,
        "first_month": str(primary.index[0]) if len(primary) else None,
        "profiles": profiles,
        "secondary_profiles": [
            profile(secondary.reindex(months), label) for label, months in windows.items()
        ],
        "legs": {label: _legs(frame, market, months) for label, months in windows.items()},
        "decision_window_annualised": annualised,
        "decision_window_hac_t": hac,
        "passes_nominally": prespec.passes_nominally(annualised, hac),
        "passes_after_multiplicity": prespec.passes_after_multiplicity(annualised, hac),
    }


def run(windows: dict[str, list[str]], cache_dir: str) -> dict[str, Any]:
    """모든 정렬. 가치 가족과 수익성 요인을 갈라 담는다."""

    rows = [run_one(sort, windows, cache_dir) for sort in SORTS]
    value_rows = [row for row in rows if row["is_value_proxy"]]
    passing = [row for row in value_rows if row["passes_after_multiplicity"]]
    nominal = [row for row in value_rows if row["passes_nominally"]]

    return {
        "rule": prespec.rule(),
        "sorts": rows,
        "value_proxies_tested_in_this_track": [
            row["sort"] for row in value_rows if row["sort"] != "book_to_market"
        ],
        "book_to_market_rerun_for_comparability": True,
        "value_proxies_passing_nominally": [row["sort"] for row in nominal],
        "value_proxies_passing_after_multiplicity": [row["sort"] for row in passing],
        "test_b_opens": bool(passing),
        "statement": (
            "어떤 가치 대리 변수도 판정 창에서 프리미엄을 보이지 않는다. 검정 B를 열지 않는다."
            if not passing
            else "다중비교를 통과한 가치 대리 변수가 있다. 그 위에서 검정 B를 다시 돌린다."
        ),
    }
