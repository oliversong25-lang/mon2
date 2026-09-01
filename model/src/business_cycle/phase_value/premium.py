"""검정 A — 가치 프리미엄이 **애초에 있는가**.

없으면 B는 "0 주위의 변동"을 재는 것이 된다. 그것도 쓸모가 없지는 않지만(0 평균 요인을
국면으로 타이밍하는 것은 여전히 전략이다) 주장의 성격이 달라진다. 그래서 A를 먼저,
분명하게 적는다.

가치 프리미엄이 시대에 따라 달라진다는 것은 알려진 사실이다. 그러니 전체 표본 하나로
"있다"고 말하면 안 되고, **우리 국면 라벨이 존재하는 창**에서 무엇인지가 이 단계의
질문이다.
"""

from __future__ import annotations

from typing import Any, Final

import numpy as np
import pandas as pd

WEEKS_PER_YEAR: Final[float] = 52.0

#: Newey-West 지연. 주간 수익률의 자기상관이 미미해도 0으로 두지 않는다.
HAC_LAGS: Final[int] = 8

#: 십 년 단위로 나눠 본다. 가치 프리미엄이 시간에 따라 변한다는 것이 알려져 있어서다.
DECADE_MINIMUM_WEEKS: Final[int] = 100


def annualise(series: pd.Series) -> float:
    values = series.dropna().to_numpy(dtype=float)
    if values.size == 0:
        return float("nan")
    return float(np.expm1(np.log1p(values).sum() * WEEKS_PER_YEAR / values.size))


def hac_t_statistic(series: pd.Series, lags: int = HAC_LAGS) -> float | None:
    """평균이 0인지에 대한 Newey-West t. 겹치지 않는 주간 수익률에 쓴다."""

    values = series.dropna().to_numpy(dtype=float)
    count = values.size
    if count < lags + 2:
        return None
    centred = values - values.mean()
    variance = float(centred @ centred) / count
    for lag in range(1, lags + 1):
        covariance = float(centred[lag:] @ centred[:-lag]) / count
        variance += 2.0 * (1.0 - lag / (lags + 1.0)) * covariance
    if variance <= 0:
        return None
    return round(float(values.mean() / np.sqrt(variance / count)), 3)


def _profile(series: pd.Series, label: str) -> dict[str, Any]:
    values = series.dropna()
    spread = float(values.std(ddof=1)) if len(values) > 1 else 0.0
    return {
        "window": label,
        "weeks": int(len(values)),
        "annualised": round(annualise(values), 4),
        "annualised_volatility": round(spread * float(np.sqrt(WEEKS_PER_YEAR)), 4),
        "sharpe": (
            round(float(values.mean()) / spread * float(np.sqrt(WEEKS_PER_YEAR)), 3)
            if spread > 0
            else None
        ),
        "hac_t": hac_t_statistic(values),
        "weeks_positive": round(float((values > 0).mean()), 4),
    }


def by_decade(series: pd.Series) -> list[dict[str, Any]]:
    """십 년 단위. 표본이 얇은 조각은 통계를 내지 않는다."""

    years = pd.Series([int(str(week)[:4]) for week in series.index], index=series.index)
    rows: list[dict[str, Any]] = []
    for decade in range(int(years.min()) // 10 * 10, int(years.max()) + 1, 10):
        mask = (years >= decade) & (years < decade + 10)
        segment = series[mask].dropna()
        if len(segment) < DECADE_MINIMUM_WEEKS:
            rows.append({"window": f"{decade}s", "weeks": int(len(segment)), "thin": True})
            continue
        rows.append({**_profile(segment, f"{decade}s"), "thin": False})
    return rows


def leave_one_year_out(series: pd.Series) -> dict[str, Any]:
    """한 해를 빼면 결론이 뒤집히는가.

    Track 17에서 모든 결과가 한 에피소드를 빼자 무너졌다. 그 경험 이후로 이것은
    사후 점검이 아니라 기본 절차다.
    """

    years = sorted({str(week)[:4] for week in series.index})
    rows: list[dict[str, Any]] = []
    values: list[float] = []
    for year in years:
        kept = series[[str(week)[:4] != year for week in series.index]]
        value = round(annualise(kept), 4)
        values.append(value)
        rows.append({"year_removed": year, "annualised": value})
    full = annualise(series)
    # 이름을 부호로 적는다. "가장 해가 되는 해"처럼 쓰면 어느 방향인지가 흐려진다.
    lowers_most = years[values.index(min(values))]
    raises_most = years[values.index(max(values))]
    return {
        "full_sample_annualised": round(full, 4),
        "range_low": round(min(values), 4),
        "range_high": round(max(values), 4),
        "year_whose_removal_lowers_it_most": lowers_most,
        "year_whose_removal_raises_it_most": raises_most,
        "sign_flips_when_any_single_year_is_removed": bool(
            (full > 0 and min(values) < 0) or (full < 0 and max(values) > 0)
        ),
        "rows": rows,
    }


def decomposition(portfolios: pd.DataFrame, market: pd.Series) -> dict[str, Any]:
    """가치 다리와 성장 다리를 갈라 본다.

    HML이 0이어도 두 다리가 각각 시장 대비 크게 움직이고 있을 수 있다. 그 경우 문제는
    "가치가 죽었다"가 아니라 "두 다리가 같이 움직였다"다.
    """

    out: dict[str, Any] = {}
    for name, high, low in (("tercile", "Hi 30", "Lo 30"), ("decile", "Hi 10", "Lo 10")):
        if high not in portfolios.columns or low not in portfolios.columns:
            continue
        out[name] = {
            "value_leg_versus_market": round(annualise(portfolios[high] - market), 4),
            "growth_leg_versus_market": round(annualise(portfolios[low] - market), 4),
            "value_minus_growth": round(annualise(portfolios[high] - portfolios[low]), 4),
            "hac_t_on_value_minus_growth": hac_t_statistic(portfolios[high] - portfolios[low]),
        }
    return out


def run(
    hml: pd.Series, windows: dict[str, list[str]], portfolios: pd.DataFrame, market: pd.Series
) -> dict[str, Any]:
    """검정 A 전체."""

    profiles = [_profile(hml, "full Fama-French sample")]
    for label, weeks in windows.items():
        profiles.append(_profile(hml.reindex(weeks), label))

    per_window_decomposition = {
        label: decomposition(portfolios.reindex(weeks), market.reindex(weeks))
        for label, weeks in windows.items()
    }

    label_window = next(iter(windows))
    inside = hml.reindex(windows[label_window]).dropna()
    verdict_positive = bool(annualise(inside) > 0 and (hac_t_statistic(inside) or 0.0) >= 2.0)

    return {
        "profiles": profiles,
        "by_decade": by_decade(hml),
        "leave_one_year_out": {
            label: leave_one_year_out(hml.reindex(weeks).dropna())
            for label, weeks in windows.items()
        },
        "decomposition": per_window_decomposition,
        "value_premium_is_positive_in_the_label_window": verdict_positive,
        "statement": (
            "가치 프리미엄은 국면 라벨이 존재하는 창에서 통계적으로 양이다."
            if verdict_positive
            else "가치 프리미엄은 국면 라벨이 존재하는 창에서 양이라고 말할 수 없다."
        ),
    }
