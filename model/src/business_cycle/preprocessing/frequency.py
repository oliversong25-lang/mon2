"""혼합빈도 관측을 주간 상태축의 사건(event)으로 변환한다."""

from __future__ import annotations

from typing import Any

import pandas as pd


def weekly_event_matrix(frame: pd.DataFrame) -> pd.DataFrame:
    """발표가 있는 주에만 값을 둔 희소 행렬을 만든다.

    월간 값을 다음 발표까지 forward-fill하지 않는다. 상태공간모형은 NaN인 주에
    관측 업데이트를 생략하고 예측 단계만 수행한다.
    """

    usable = frame.dropna(subset=["signal"]).copy()
    if usable.empty:
        raise ValueError("최소 학습기간 이후 사용할 변환 신호가 없습니다")
    grouped = usable.groupby(["available_week", "indicator_id"], as_index=False)["signal"].last()
    matrix: pd.DataFrame = grouped.pivot(
        index="available_week", columns="indicator_id", values="signal"
    )
    full_index = pd.date_range(matrix.index.min(), matrix.index.max(), freq="W-FRI")
    result: pd.DataFrame = matrix.reindex(full_index).sort_index()
    # 성숙도는 표준화가 끝난 첫 값이 아니라 원자료가 처음 공개된 때부터 잰다.
    result.attrs["first_available"] = {
        str(key): pd.Timestamp(value)
        for key, value in frame.groupby("indicator_id")["available_week"].min().items()
    }
    audit_columns = [
        column
        for column in (
            "indicator_id",
            "available_week",
            "observation_period",
            "value",
            "original_signal",
            "preclip_signal",
            "postclip_signal",
            "frequency",
            "trend_span_observations",
            # rolling 표준화의 창을 감사할 수 있어야 "10년 창"이 주장이 아니라 기록이 된다.
            "standardization_window_observations",
            "rolling_center",
            "rolling_scale",
            "rolling_scale_source",
            "window_start",
            "window_end",
            "window_observations",
        )
        if column in frame.columns
    ]
    result.attrs["signal_audit"] = frame[audit_columns].copy()
    return result


def combine_subfactor(
    events: pd.DataFrame,
    indicator_settings: dict[str, Any],
    config: dict[str, Any] | None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """중복된 지표들을 표준화 이후 동일가중으로 하나의 부요인으로 합친다.

    ICSA와 CCSA처럼 같은 현상을 두 번 세는 지표가 요인을 지배하는 것을 막기 위한
    장치다. PCA 대신 동일가중을 쓰는 이유는 기여도를 그대로 읽을 수 있어야 하기
    때문이다. 합성은 주간 사건행렬 위에서 하므로 발표 시점이 다른 두 계열도
    각자 발표된 주의 값만 평균에 들어간다.
    """

    if not config or not bool(config.get("enabled", False)):
        return events, indicator_settings
    members = [str(member) for member in config["members"]]
    missing = [member for member in members if member not in events.columns]
    if missing:
        raise ValueError(f"부요인 구성 지표가 사건행렬에 없습니다: {missing}")
    combined_id = str(config.get("id", "CLAIMS"))
    if combined_id in events.columns:
        raise ValueError(f"부요인 이름이 기존 지표와 겹칩니다: {combined_id}")
    combined = events[members].mean(axis=1, skipna=True)
    result = events.drop(columns=members).copy()
    result[combined_id] = combined
    first_available = dict(events.attrs.get("first_available", {}))
    member_starts = [first_available.pop(member) for member in members if member in first_available]
    if member_starts:
        first_available[combined_id] = min(member_starts)
    result.attrs = dict(events.attrs)
    result.attrs["first_available"] = first_available
    result.attrs["subfactor_members"] = {combined_id: members}

    settings = {key: value for key, value in indicator_settings.items() if key not in members}
    member_settings = [indicator_settings[member] for member in members]
    settings[combined_id] = {
        "domain": str(config.get("domain", member_settings[0]["domain"])),
        "frequency": str(member_settings[0]["frequency"]),
        # 명목 가중치는 구성 지표의 합이다. 합치는 것 자체로 비중을 바꾸지 않는다.
        "weight": float(config.get("weight", sum(float(m["weight"]) for m in member_settings))),
        "direction": 1,
        "transform": str(member_settings[0]["transform"]),
        "release_lag_days": max(int(m.get("release_lag_days", 0)) for m in member_settings),
        "max_age_weeks": max(int(m.get("max_age_weeks", 8)) for m in member_settings),
    }
    return result, settings


def held_signal_matrix(events: pd.DataFrame, max_age_weeks: dict[str, int]) -> pd.DataFrame:
    """투명한 합성지수용으로만 신호를 제한된 기간 보유한다."""

    held = pd.DataFrame(index=events.index, columns=events.columns, dtype=float)
    for column in events.columns:
        held[column] = events[column].ffill(limit=max(0, int(max_age_weeks.get(str(column), 0))))
    return held
