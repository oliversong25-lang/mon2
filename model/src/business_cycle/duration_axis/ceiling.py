"""완전예지 천장 — 두 축을 같은 절차로.

트랙 23의 구성을 그대로 쓴다. 국면을 이미 알고 국면별로 어느 통이 가장 좋았는지까지
이미 아는 전략의 수익이며, 실현 가능한 어떤 전략도 그 위로 갈 수 없다.

바뀌는 것은 **연율화 인자 하나**다. 주간 52 대신 월간 12를 쓴다. 나머지 — 확장 창 가중,
전체표본 가중, 한 기간 밀기, 신탁 상한 — 은 트랙 23의 함수를 그대로 불러 쓴다. 다시 짜면
전후 비교가 축의 차이인지 구현의 차이인지 갈리지 않는다.
"""

from __future__ import annotations

from typing import Any, Final

import numpy as np
import pandas as pd

from ..phase_returns import rotation as R
from ..rotation_rerun.ceiling import _weekly_oracle

MONTHS_PER_YEAR: Final[float] = 12.0


def _annualise(series: np.ndarray, periods: float) -> float:
    clean = series[np.isfinite(series)]
    if clean.size == 0:
        return float("nan")
    return float(np.expm1(np.log1p(clean).sum() * periods / clean.size))


def _drawdown(series: np.ndarray) -> float:
    clean = np.nan_to_num(series)
    curve = np.cumprod(1.0 + clean)
    peak = np.maximum.accumulate(curve)
    return float((curve / peak - 1.0).min())


def _profile(series: np.ndarray, label: str, periods: float) -> dict[str, Any]:
    clean = series[np.isfinite(series)]
    spread = float(clean.std(ddof=1)) if clean.size > 1 else 0.0
    return {
        "strategy": label,
        "periods": int(clean.size),
        "annualised_relative_return": round(_annualise(series, periods), 4),
        "annualised_volatility": round(spread * float(np.sqrt(periods)), 4),
        "information_ratio": (
            round(float(clean.mean()) / spread * float(np.sqrt(periods)), 3) if spread > 0 else None
        ),
        "periods_positive": round(float((clean > 0).mean()), 4),
        "worst_drawdown_versus_market": round(_drawdown(series), 4),
    }


def measure(
    phase: pd.Series,
    axis: pd.DataFrame,
    market: pd.Series,
    top_k: int = R.TOP_K,
    minimum: int = 12,
    periods: float = MONTHS_PER_YEAR,
) -> dict[str, Any]:
    """한 축의 두 천장과 실현 가능한 확장 창 순환매.

    ``minimum``은 한 국면을 이만큼 본 뒤에야 그 국면으로 거래한다는 뜻이다. 트랙 23의
    주간 52와 같은 **1년**이며, 월간 격자라 12가 된다.
    """

    relative = axis.sub(market, axis=0)
    usable = relative.dropna(how="any")
    aligned = phase.reindex(usable.index).fillna("").astype(str).to_numpy()
    values = usable.to_numpy(dtype=float)

    realised = R._realise(R._expanding_weights(aligned, values, top_k, minimum), values)
    ranking = R._realise(R._full_sample_weights(aligned, values, top_k), values)
    oracle = (_weekly_oracle(values, top_k) * values).sum(axis=1)
    equal = values.mean(axis=1)

    ranking_profile = _profile(ranking, "phase ranking ceiling (not achievable)", periods)
    oracle_profile = _profile(oracle, "period oracle ceiling (no phases at all)", periods)
    share = (
        round(
            float(ranking_profile["annualised_relative_return"])
            / float(oracle_profile["annualised_relative_return"]),
            4,
        )
        if oracle_profile["annualised_relative_return"]
        else None
    )
    return {
        "periods": int(len(values)),
        "buckets": int(values.shape[1]),
        "top_k": top_k,
        "minimum_phase_history": minimum,
        "ranking_ceiling": ranking_profile,
        "oracle_ceiling": oracle_profile,
        "phase_share_of_the_oracle": share,
        "achievable_rotation": _profile(realised, "phase rotation (expanding window)", periods),
        "equal_weight": _profile(equal, "equal weight across buckets", periods),
    }


def compare(duration: dict[str, Any], industry: dict[str, Any]) -> dict[str, Any]:
    """두 축을 나란히. 축이 틀렸는지에 대한 직접적인 답이다."""

    def _annual(entry: dict[str, Any]) -> float:
        return float(entry["ranking_ceiling"]["annualised_relative_return"])

    ratio = round(_annual(duration) / _annual(industry), 3) if _annual(industry) else None
    share_duration = duration["phase_share_of_the_oracle"]
    share_industry = industry["phase_share_of_the_oracle"]
    return {
        "duration": duration,
        "industry": industry,
        "ceiling_ratio": ratio,
        "share_organised_duration": share_duration,
        "share_organised_industry": share_industry,
        "share_moved": (
            round(float(share_duration) - float(share_industry), 4)
            if share_duration is not None and share_industry is not None
            else None
        ),
        "reading": (
            f"같은 월간 격자에서 듀레이션 축 천장은 연 {_annual(duration):.2%}, 업종 축은 "
            f"연 {_annual(industry):.2%}다(비 {ratio}). 국면이 조직하는 몫은 듀레이션 축에서 "
            f"{float(share_duration):.1%}, 업종 축에서 {float(share_industry):.1%}다."
            if share_duration is not None and share_industry is not None
            else "두 축 중 하나에서 몫을 계산할 수 없다."
        ),
    }
