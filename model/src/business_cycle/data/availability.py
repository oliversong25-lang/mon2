"""관측기간을 당시 사용 가능했던 주로 정렬한다."""

from __future__ import annotations

from typing import Any

import pandas as pd

OBSERVATION_COLUMNS = [
    "indicator_id",
    "observation_period",
    "value",
    "release_date",
    "vintage_date",
    "fetched_at",
    "source",
    "revision_status",
    "freshness_score",
]


def apply_availability_dates(
    frame: pd.DataFrame, indicator_settings: dict[str, Any]
) -> tuple[pd.DataFrame, list[str]]:
    """실제 발표일을 우선하고, 없으면 설정 지연으로 보수적 가용일을 만든다.

    추정 발표일은 실시간 빈티지가 아니므로 경고를 함께 반환한다.
    """

    result = frame.copy()
    result["observation_period"] = pd.to_datetime(result["observation_period"], errors="raise")
    if "release_date" not in result:
        result["release_date"] = pd.NaT
    result["release_date"] = pd.to_datetime(result["release_date"], errors="coerce")
    warnings: list[str] = []
    missing = result["release_date"].isna()
    for indicator_id, indexes in result[missing].groupby("indicator_id").groups.items():
        lag = int(indicator_settings.get(str(indicator_id), {}).get("release_lag_days", 0))
        result.loc[indexes, "release_date"] = result.loc[
            indexes, "observation_period"
        ] + pd.to_timedelta(lag, unit="D")
        warnings.append(f"{indicator_id}: 실제 발표일 없음, {lag}일 지연으로 추정")
    result["available_week"] = (
        result["release_date"].dt.to_period("W-FRI").dt.end_time.dt.normalize()
    )
    return result.sort_values(["available_week", "indicator_id"]), warnings


def validate_observations(frame: pd.DataFrame) -> pd.DataFrame:
    """공통 관측 스키마를 보완하고 잘못된 값은 명시적으로 실패시킨다."""

    required = {"indicator_id", "observation_period", "value"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"관측 데이터 필수 열 누락: {sorted(missing)}")
    result = frame.copy()
    result["value"] = pd.to_numeric(result["value"], errors="coerce")
    result = result.dropna(subset=["value"])
    defaults: dict[str, Any] = {
        "release_date": pd.NaT,
        "vintage_date": pd.NaT,
        "fetched_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "source": "local",
        "revision_status": "latest_revision",
        "freshness_score": 1.0,
    }
    for column, default in defaults.items():
        if column not in result:
            result[column] = default
    return result
