"""도메인 총량: 정보를 버리지 않으면서 한 도메인이 지배하지 못하게 한다.

후보 I는 도메인 중앙값을 썼다. 한 도메인의 지배는 막았지만 정보를 함께 버렸다.
측정해 보면 중앙값은 총량 분산의 약 4분의 1을 잃고(수준 1.106 대 평균 1.542),
등가중 평균과 부호가 다른 주가 수준 9.2%·모멘텀 11.9%였다. 그리고 25.0%의 주에서
**그 주에 새 관측이 없던 도메인**의 값이 중앙값으로 뽑혔다.

여기서는 다섯 도메인을 모두 살리되 각 도메인의 기여를 개발구간 분포에서 나온 상한으로
자른다. 여러 도메인이 함께 움직이면 총량도 함께 극단이 되지만, 한 도메인이 혼자
총량을 끌고 갈 수는 없다.

월간 계열의 "이번 주 새 소식 없음"을 "모멘텀 0"으로 바꾸지 않는다. 새 관측이 온 주에만
모멘텀 추정을 갱신하고, 그 사이에는 마지막 추정을 그대로 들고 간다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from ..current_state.domains import DOMAIN_MEMBERS, DOMAINS


@dataclass(frozen=True)
class BoundedAggregate:
    """총량과 그 총량이 어떻게 만들어졌는지."""

    aggregate: pd.Series
    uncapped: pd.Series
    bounded_frame: pd.DataFrame
    removed: pd.Series
    largest_before: pd.Series
    largest_after: pd.Series
    capped_weeks: pd.Series


def bounded_mean(frame: pd.DataFrame, cap: float) -> BoundedAggregate:
    """도메인 점수를 상한으로 자른 뒤 등가중 평균한다."""

    if cap <= 0:
        raise ValueError("상한은 양수여야 합니다")
    bounded = frame.clip(lower=-cap, upper=cap)
    magnitude = frame.abs()
    return BoundedAggregate(
        aggregate=bounded.mean(axis=1, skipna=True),
        uncapped=frame.mean(axis=1, skipna=True),
        bounded_frame=bounded,
        removed=(frame - bounded).abs().sum(axis=1),
        largest_before=magnitude.max(axis=1),
        largest_after=bounded.abs().max(axis=1),
        capped_weeks=(magnitude > cap).any(axis=1),
    )


def release_weeks(events: pd.DataFrame, index: pd.Index) -> pd.DataFrame:
    """도메인별로 그 주에 새 관측이 도착했는지."""

    aligned = events.reindex(index)
    columns: dict[str, pd.Series] = {}
    for domain, members in DOMAIN_MEMBERS.items():
        present = [member for member in members if member in aligned.columns]
        columns[domain] = (
            aligned[present].notna().any(axis=1) if present else pd.Series(False, index=index)
        )
    return pd.DataFrame(columns, index=index)


def weeks_since_release(arrived: pd.DataFrame) -> pd.DataFrame:
    """마지막 새 관측 이후 지난 주 수. 신선도를 총량과 함께 들고 간다."""

    columns: dict[str, pd.Series] = {}
    for domain in arrived.columns:
        ages: list[float] = []
        age = float("inf")
        for flag in arrived[domain]:
            age = 0.0 if bool(flag) else (age + 1.0 if np.isfinite(age) else float("inf"))
            ages.append(age)
        columns[str(domain)] = pd.Series(ages, index=arrived.index)
    return pd.DataFrame(columns, index=arrived.index)


def release_aware_momentum(
    levels: pd.DataFrame, arrived: pd.DataFrame, momentum_weeks: int
) -> pd.DataFrame:
    """새 관측이 온 주에만 모멘텀 추정을 갱신하고, 사이에는 마지막 추정을 유지한다.

    월간 계열은 3주에 1주꼴로만 새 값이 온다(생산 35.6%, 고용 36.4%, 소득 30.4%,
    소비 29.7%의 주에는 새 소식이 없다). 그 주에 8주 차분이 우연히 0이 되면
    "모멘텀이 중립"이 아니라 "이번 주에 모멘텀에 대한 새 정보가 없다"는 뜻이다.
    둘을 같은 숫자로 내보내면 없는 신호를 만들어내는 것이다.
    """

    raw = levels.diff(momentum_weeks)
    updated = raw.where(arrived.reindex(raw.index).fillna(False))
    return updated.ffill()


def domain_scores(
    held: pd.DataFrame, events: pd.DataFrame, momentum_weeks: int
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """``(수준, 모멘텀, 새 관측 여부, 경과 주)``. 도메인 안에서 먼저 평균한다."""

    columns: dict[str, pd.Series] = {}
    for domain, members in DOMAIN_MEMBERS.items():
        present = [member for member in members if member in held.columns]
        columns[domain] = (
            held[present].mean(axis=1, skipna=True)
            if present
            else pd.Series(np.nan, index=held.index)
        )
    levels = pd.DataFrame(columns, index=held.index)[list(DOMAINS)]
    arrived = release_weeks(events, levels.index)
    momentum = release_aware_momentum(levels, arrived, momentum_weeks)
    return levels, momentum, arrived, weeks_since_release(arrived)


def summary(aggregate: BoundedAggregate, recession: pd.Series) -> dict[str, Any]:
    """상한이 무엇을 얼마나 잘라냈는지. 침체 주와 평상시를 나눠 본다."""

    flags = recession.reindex(aggregate.aggregate.index).fillna(False).astype(bool)
    return {
        "weeks": int(len(aggregate.aggregate)),
        "capped_weeks": int(aggregate.capped_weeks.sum()),
        "capped_share": round(float(aggregate.capped_weeks.mean()), 6),
        "capped_weeks_in_recession": int((aggregate.capped_weeks & flags).sum()),
        "capped_share_in_recession": (
            round(float(aggregate.capped_weeks[flags].mean()), 6) if flags.any() else 0.0
        ),
        "capped_share_normal": round(float(aggregate.capped_weeks[~flags].mean()), 6),
        "median_removed_when_capped": (
            round(float(aggregate.removed[aggregate.capped_weeks].median()), 6)
            if aggregate.capped_weeks.any()
            else 0.0
        ),
        "largest_domain_before_max": round(float(aggregate.largest_before.max()), 6),
        "largest_domain_after_max": round(float(aggregate.largest_after.max()), 6),
        "aggregate_uncapped_min": round(float(aggregate.uncapped.min()), 6),
        "aggregate_bounded_min": round(float(aggregate.aggregate.min()), 6),
        "correlation_with_uncapped": round(float(aggregate.aggregate.corr(aggregate.uncapped)), 6),
    }
