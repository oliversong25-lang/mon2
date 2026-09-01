"""도메인 상태를 먼저 만들고, 그 다음에 총량 상태를 만든다.

계열 수가 곧 가중치가 되면 안 된다. 소비/실질판매는 두 계열(RRSFS, CMRMTSPL), 노동시장
스트레스도 두 계열(ICSA, CCSA)인데, 이들을 개별 지표로 합치면 한 계열짜리 생산·고용·
소득보다 두 배의 발언권을 갖는다. 그래서 **도메인 안에서 먼저 평균**을 내고, 그 다음에
도메인끼리 합친다.

총량은 중앙값으로 모은다. 후보 H에서 실질소득 한 도메인이 Y의 100% 이상을 만들었던 것이
바로 가중합의 성질이다. 중앙값은 한 도메인이 아무리 극단이어도 총량을 끌고 갈 수 없다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

import numpy as np
import pandas as pd

#: 다섯 경제 도메인과 그 구성 계열. 계열 수는 도메인 안에서만 의미를 갖는다.
DOMAIN_MEMBERS: Final[dict[str, tuple[str, ...]]] = {
    "production": ("INDPRO",),
    "employment": ("PAYEMS",),
    "real_income": ("W875RX1",),
    "consumption": ("RRSFS", "CMRMTSPL"),
    "labor_stress": ("ICSA", "CCSA"),
}

DOMAINS: Final[tuple[str, ...]] = tuple(DOMAIN_MEMBERS)

#: 노동시장 스트레스는 동행 활동지표가 아니라 가교다. 단독으로 침체를 선언할 수 없다.
COINCIDENT_DOMAINS: Final[tuple[str, ...]] = (
    "production",
    "employment",
    "real_income",
    "consumption",
)


@dataclass(frozen=True)
class DomainState:
    """한 도메인의 현재 상태. 전부 그 시점까지의 자료로만 만든다."""

    domain: str
    level_raw: float
    level_scaled: float
    momentum_raw: float
    momentum_scaled: float
    direction: str
    freshness_weeks: float
    contribution: float
    contribution_share: float
    members_available: int
    members_total: int


def _direction(level: float, momentum: float, neutral_level: float, neutral_momentum: float) -> str:
    """수준과 모멘텀의 부호를 중립대 밖에서만 읽는다."""

    if not np.isfinite(level) or not np.isfinite(momentum):
        return "unknown"
    above = level > neutral_level
    below = level < -neutral_level
    rising = momentum > neutral_momentum
    falling = momentum < -neutral_momentum
    if below and rising:
        return "improving_from_weak"
    if below and falling:
        return "deteriorating_from_weak"
    if above and falling:
        return "decelerating_from_strong"
    if above and rising:
        return "strengthening"
    return "flat"


def domain_level_frame(signals: pd.DataFrame) -> pd.DataFrame:
    """지표 표준화 신호를 도메인 평균으로 모은다. 결측 계열은 빼고 평균한다."""

    columns: dict[str, pd.Series] = {}
    for domain, members in DOMAIN_MEMBERS.items():
        present = [member for member in members if member in signals.columns]
        if not present:
            columns[domain] = pd.Series(np.nan, index=signals.index)
            continue
        columns[domain] = signals[present].mean(axis=1, skipna=True)
    return pd.DataFrame(columns, index=signals.index)


def domain_member_counts(signals: pd.DataFrame) -> pd.DataFrame:
    counts: dict[str, pd.Series] = {}
    for domain, members in DOMAIN_MEMBERS.items():
        present = [member for member in members if member in signals.columns]
        counts[domain] = (
            signals[present].notna().sum(axis=1) if present else pd.Series(0, index=signals.index)
        )
    return pd.DataFrame(counts, index=signals.index)


def domain_momentum_frame(levels: pd.DataFrame, momentum_weeks: int) -> pd.DataFrame:
    """도메인 수준의 momentum_weeks 변화. 2차 척도를 물려받지 않는다."""

    return levels.diff(momentum_weeks)


def aggregate_level(levels: pd.DataFrame) -> pd.Series:
    """도메인 균형 총량. 중앙값이라 한 도메인이 결과를 끌고 갈 수 없다."""

    return levels.median(axis=1, skipna=True)


def aggregate_momentum(momentum: pd.DataFrame) -> pd.Series:
    return momentum.median(axis=1, skipna=True)


def concentration(frame: pd.DataFrame) -> pd.Series:
    """가장 큰 항목이 전체 크기에서 차지하는 몫."""

    magnitude = frame.abs()
    total = magnitude.sum(axis=1)
    return (magnitude.max(axis=1) / total).where(total > 0)


def count_states(frame: pd.DataFrame, neutral: float) -> tuple[pd.Series, pd.Series, pd.Series]:
    """중립대를 벗어난 도메인 수를 음수·양수·중립으로 나눈다."""

    negative = (frame < -neutral).sum(axis=1)
    positive = (frame > neutral).sum(axis=1)
    neutral_count = frame.notna().sum(axis=1) - negative - positive
    return negative, positive, neutral_count


def domain_states(
    levels_scaled: pd.DataFrame,
    levels_raw: pd.DataFrame,
    momentum_scaled: pd.DataFrame,
    momentum_raw: pd.DataFrame,
    freshness: dict[str, float],
    counts: pd.DataFrame,
    week: pd.Timestamp,
    neutral_level: float,
    neutral_momentum: float,
) -> list[DomainState]:
    """한 주의 도메인 상태 목록."""

    magnitude = levels_scaled.loc[[week]].abs().iloc[0]
    total_value = float(str(magnitude.sum()))
    total = total_value if np.isfinite(total_value) else 0.0
    states: list[DomainState] = []
    for domain in DOMAINS:
        level = float(str(levels_scaled.loc[week, domain]))
        momentum = float(str(momentum_scaled.loc[week, domain]))
        states.append(
            DomainState(
                domain=domain,
                level_raw=float(str(levels_raw.loc[week, domain])),
                level_scaled=level,
                momentum_raw=float(str(momentum_raw.loc[week, domain])),
                momentum_scaled=momentum,
                direction=_direction(level, momentum, neutral_level, neutral_momentum),
                freshness_weeks=float(freshness.get(domain, float("inf"))),
                contribution=level,
                contribution_share=(
                    float(abs(level) / total) if total > 0 and np.isfinite(level) else float("nan")
                ),
                members_available=int(str(counts.loc[week, domain])),
                members_total=len(DOMAIN_MEMBERS[domain]),
            )
        )
    return states


def as_records(states: list[DomainState]) -> list[dict[str, Any]]:
    return [
        {
            "domain": state.domain,
            "level_raw": round(state.level_raw, 6),
            "level_scaled": round(state.level_scaled, 6),
            "momentum_raw": round(state.momentum_raw, 6),
            "momentum_scaled": round(state.momentum_scaled, 6),
            "direction": state.direction,
            "freshness_weeks": round(state.freshness_weeks, 2),
            "contribution": round(state.contribution, 6),
            "contribution_share": (
                round(state.contribution_share, 6)
                if np.isfinite(state.contribution_share)
                else None
            ),
            "members_available": state.members_available,
            "members_total": state.members_total,
        }
        for state in states
    ]
