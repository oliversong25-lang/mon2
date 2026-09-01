"""경제영역 진단. 모델이 왜 그 국면을 골랐는지 영역 단위로 되짚는다.

여기서 다루는 것은 **경제영역**이지 산업이 아니다. 고용·소득·생산·소비/판매·
실업수당 청구다. 산업 폭은 다른 자료가 필요하며 `industry` 모듈이 따로 다룬다.

찬성·반대의 정의는 모델 기하에서 그대로 나온다. 대국면은 좌표의 사분면이다.

    recovery    Y<0, X>0        expansion   Y>0, X>0
    contraction Y<0, X<0        slowdown    Y>0, X<0

한 영역의 기여도 부호가 그 국면의 Y 부호와 같으면 수준이 국면을 뒷받침하고,
기여도의 8주 변화 부호가 X 부호와 같으면 모멘텀이 뒷받침한다. 둘 다 맞으면 찬성,
둘 다 어긋나면 반대, 하나만 맞으면 혼재다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from ..config import Settings
from .contract import label_domain

#: 대국면이 함의하는 (수준 부호, 모멘텀 부호).
EXPECTED_SIGNS: dict[str, tuple[int, int]] = {
    "recovery": (-1, +1),
    "expansion": (+1, +1),
    "slowdown": (+1, -1),
    "contraction": (-1, -1),
}


@dataclass(frozen=True)
class DomainReading:
    domain: str
    direction: str
    standardized_contribution: float
    contribution_share: float
    recent_change: float
    supports_official_phase: bool
    opposes_official_phase: bool
    stance: str
    data_freshness_weeks: float
    missing: bool


def domain_contributions(
    contributions: pd.DataFrame, settings: Settings, index: pd.Index
) -> pd.DataFrame:
    """지표 기여도를 경제영역으로 합친다. 계약 이름으로 옮겨 담는다."""

    indicator_settings = settings.indicators["indicators"]
    domain_of = {
        str(key): label_domain(str(value["domain"])) for key, value in indicator_settings.items()
    }
    aligned = contributions.reindex(index, method="ffill")
    known = [column for column in aligned.columns if str(column) in domain_of]
    return aligned[known].rename(columns=domain_of).T.groupby(level=0).sum().T


def _direction(value: float) -> str:
    if not np.isfinite(value) or value == 0.0:
        return "flat"
    return "positive" if value > 0 else "negative"


def domain_readings(
    contributions: pd.DataFrame,
    events: pd.DataFrame,
    settings: Settings,
    history: pd.DataFrame,
    as_of: pd.Timestamp,
    broad_phase: str,
    momentum_weeks: int,
) -> list[DomainReading]:
    """한 주의 영역별 판독. 그 주까지의 자료만 쓴다."""

    frame = domain_contributions(contributions, settings, history.index)
    current = frame.loc[as_of]
    position = int(pd.DatetimeIndex(frame.index).get_indexer(pd.DatetimeIndex([as_of]))[0])
    earlier = frame.iloc[max(0, position - momentum_weeks)]
    magnitude = float(str(current.abs().sum()))
    level_sign, momentum_sign = EXPECTED_SIGNS[broad_phase]

    indicator_settings = settings.indicators["indicators"]
    domain_of = {
        str(key): label_domain(str(value["domain"])) for key, value in indicator_settings.items()
    }
    freshness: dict[str, float] = {}
    missing: dict[str, bool] = {}
    released = events.reindex(history.index).loc[:as_of]
    for indicator, domain in domain_of.items():
        column = released[indicator] if indicator in released.columns else pd.Series(dtype=float)
        observed = column.dropna()
        age = (
            float((as_of - pd.Timestamp(str(observed.index[-1]))).days) / 7.0
            if len(observed)
            else float("inf")
        )
        freshness[domain] = min(freshness.get(domain, float("inf")), age)
        contribution = contributions.reindex([as_of], method="ffill")
        value = (
            contribution.iloc[0].get(indicator, np.nan)
            if indicator in contribution.columns
            else np.nan
        )
        absent = bool(pd.isna(value))
        missing[domain] = missing.get(domain, False) or absent

    readings: list[DomainReading] = []
    for domain in sorted(frame.columns):
        value = float(str(current[domain]))
        change = value - float(str(earlier[domain]))
        level_ok = bool(np.sign(value) == level_sign) if value != 0 else False
        momentum_ok = bool(np.sign(change) == momentum_sign) if change != 0 else False
        if level_ok and momentum_ok:
            stance = "supports"
        elif not level_ok and not momentum_ok:
            stance = "opposes"
        else:
            stance = "mixed"
        readings.append(
            DomainReading(
                domain=str(domain),
                direction=_direction(value),
                standardized_contribution=round(value, 6),
                contribution_share=round(
                    abs(value) / magnitude if magnitude > 0 else float("nan"), 6
                ),
                recent_change=round(change, 6),
                supports_official_phase=stance == "supports",
                opposes_official_phase=stance == "opposes",
                stance=stance,
                data_freshness_weeks=round(freshness.get(str(domain), float("inf")), 2),
                missing=bool(missing.get(str(domain), False)),
            )
        )
    return readings


def domain_breadth(readings: list[DomainReading]) -> dict[str, Any]:
    """경제영역 폭. 산업 폭이 아니다. 이름에 그 구분을 남긴다."""

    return {
        "measure": "economic_domain_breadth",
        "total_domains": len(readings),
        "supporting": sum(1 for reading in readings if reading.supports_official_phase),
        "opposing": sum(1 for reading in readings if reading.opposes_official_phase),
        "mixed": sum(1 for reading in readings if reading.stance == "mixed"),
        "negative_contribution_domains": sum(
            1 for reading in readings if reading.standardized_contribution < 0
        ),
    }


def domain_changes(readings: list[DomainReading]) -> dict[str, float]:
    return {reading.domain: reading.recent_change for reading in readings}


def explain(readings: list[DomainReading], broad_phase: str) -> str:
    """모델이 왜 그 국면을 골랐는지 한 문장. 투자 판단은 넣지 않는다."""

    supporting = [reading.domain for reading in readings if reading.supports_official_phase]
    opposing = [reading.domain for reading in readings if reading.opposes_official_phase]
    mixed = [reading.domain for reading in readings if reading.stance == "mixed"]
    parts: list[str] = []
    if supporting:
        parts.append(f"{', '.join(supporting)}이(가) {broad_phase} 판정을 뒷받침한다")
    if opposing:
        parts.append(f"{', '.join(opposing)}은(는) 반대 방향을 가리킨다")
    if mixed:
        parts.append(f"{', '.join(mixed)}은(는) 수준과 모멘텀이 엇갈린다")
    return ". ".join(parts) + "." if parts else "영역 판독을 만들 자료가 없다."
