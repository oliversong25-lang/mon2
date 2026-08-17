"""혼합빈도 관측을 주간 상태축의 사건(event)으로 변환한다."""

from __future__ import annotations

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
            "original_signal",
            "preclip_signal",
            "postclip_signal",
            "frequency",
            "trend_span_observations",
        )
        if column in frame.columns
    ]
    result.attrs["signal_audit"] = frame[audit_columns].copy()
    return result


def held_signal_matrix(events: pd.DataFrame, max_age_weeks: dict[str, int]) -> pd.DataFrame:
    """투명한 합성지수용으로만 신호를 제한된 기간 보유한다."""

    held = pd.DataFrame(index=events.index, columns=events.columns, dtype=float)
    for column in events.columns:
        held[column] = events[column].ffill(limit=max(0, int(max_age_weeks.get(str(column), 0))))
    return held
