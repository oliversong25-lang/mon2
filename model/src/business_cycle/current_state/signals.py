"""기존 전처리에서 주간 지표 신호를 얻는다. 변환은 새로 만들지 않는다.

§3 정규화 감사에서 척도조정판이 문제를 풀지 못하고 성능도 떨어뜨린다는 것이 확인됐다.
그래서 일곱 계열의 변환·추세·1차 표준화는 후보 H와 **동일하게** 쓰고, 여기서는 그
결과를 도메인으로 모으는 일만 한다.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from ..config import Settings
from ..data.availability import apply_availability_dates, validate_observations
from ..preprocessing.frequency import held_signal_matrix, weekly_event_matrix
from ..preprocessing.transforms import transform_observations


def indicator_signals(
    observations: pd.DataFrame, settings: Settings, as_of: pd.Timestamp
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """``(사건행렬, 보유행렬, 메타)``.

    사건행렬은 발표가 있던 주에만 값이 있고, 보유행렬은 각 지표의 ``max_age_weeks``
    안에서만 직전 값을 들고 간다. 오래된 값을 무한정 끌지 않는다.
    """

    core = settings.indicators["indicators"]
    validated = validate_observations(observations)
    available, warnings = apply_availability_dates(validated, core)
    available = available[available["release_date"] <= as_of].copy()
    if available.empty:
        raise ValueError(f"{as_of.date()}까지 공개된 핵심지표가 없습니다")
    model = settings.model
    transformed = transform_observations(
        available,
        core,
        int(model["trend_span_weeks"]) if "trend_horizon_years" not in model else None,
        int(model["standardization_min_periods"]),
        trend_horizon_years=model.get("trend_horizon_years"),
        standardization_method=str(model.get("standardization_method", "expanding_mean_std")),
        standardization_horizon_years=float(model.get("standardization_horizon_years", 10.0)),
        standardization_min_history_years=model.get("standardization_min_history_years"),
        robust_clip=model.get("robust_clip"),
    )
    events = weekly_event_matrix(transformed)
    held = held_signal_matrix(
        events, {str(key): int(value["max_age_weeks"]) for key, value in core.items()}
    )
    meta = {
        "warnings": warnings,
        "first_available": events.attrs.get("first_available", {}),
        "release_weeks": {
            str(name): pd.DatetimeIndex(group.dropna().index) for name, group in events.items()
        },
    }
    return events, held, meta


def freshness_weeks(
    events: pd.DataFrame, settings: Settings, week: pd.Timestamp
) -> dict[str, float]:
    """도메인별 최신 관측 경과 주 수. 도메인 안에서 가장 신선한 계열을 쓴다."""

    from .domains import DOMAIN_MEMBERS

    window = events.loc[:week]
    result: dict[str, float] = {}
    for domain, members in DOMAIN_MEMBERS.items():
        ages: list[float] = []
        for member in members:
            if member not in window.columns:
                continue
            observed = window[member].dropna()
            if observed.empty:
                continue
            ages.append(float((week - pd.Timestamp(str(observed.index[-1]))).days) / 7.0)
        result[domain] = min(ages) if ages else float("inf")
    return result
