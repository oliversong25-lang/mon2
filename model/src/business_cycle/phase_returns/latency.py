"""인식 지연의 비용을 **주가 아니라 수익률로** 적는다.

수정치 라벨과 실시간 라벨을 같은 주에 나란히 놓고, 같은 분석을 두 번 돌린다. 그 차이가
지연의 값이다. 앞으로 어떤 선행 신호를 붙이든 줄여야 하는 것이 이 수치다.

차이를 그냥 적기만 하면 안 된다. 두 추정치가 **각각** 우연과 구분되지 않으면 그 차이는
"지연 비용"이 아니라 잡음에서 잡음을 뺀 값이다. 그래서 각 추정치의 귀무 대비 p를 먼저
싣고, 그 다음에야 차이를 말한다.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from .labels import PHASES, WITHHELD


def disagreement(revised: pd.Series, real_time: pd.Series) -> dict[str, Any]:
    """두 라벨이 어긋난 주. 어긋남의 방향도 함께 센다."""

    frame = pd.DataFrame({"revised": revised, "real_time": real_time}).dropna()
    differs = frame["revised"] != frame["real_time"]
    counted = (
        frame[differs]
        .groupby(["revised", "real_time"])
        .size()
        .sort_values(ascending=False)
        .reset_index(name="weeks")
    )
    return {
        "weeks_compared": int(len(frame)),
        "weeks_that_disagree": int(differs.sum()),
        "share_that_disagree": round(float(differs.mean()), 4),
        "most_common_disagreements": [
            {
                "revised": str(row["revised"]),
                "real_time": str(row["real_time"]),
                "weeks": int(row["weeks"]),
            }
            for _, row in counted.head(8).iterrows()
        ],
        "weeks_real_time_withheld": int((frame["real_time"] == WITHHELD).sum()),
    }


def recognition_delay(revised: pd.Series, real_time: pd.Series) -> dict[str, Any]:
    """수정치가 국면을 바꾼 주부터, 실시간이 같은 국면을 부를 때까지 몇 주인가.

    이것은 주 단위로 잴 수 있다. 아래 수익률 차이와 달리 잡음에 묻히지 않는다.
    """

    frame = pd.DataFrame({"revised": revised, "real_time": real_time}).dropna()
    weeks = [str(week) for week in frame.index]
    revised_values = frame["revised"].tolist()
    real_time_values = frame["real_time"].tolist()

    rows: list[dict[str, Any]] = []
    for position in range(1, len(weeks)):
        if revised_values[position] == revised_values[position - 1]:
            continue
        target = revised_values[position]
        if target not in PHASES:
            continue
        found: int | None = None
        for ahead in range(position, len(weeks)):
            if real_time_values[ahead] == target:
                found = ahead - position
                break
        rows.append(
            {
                "week": weeks[position],
                "revised_phase": target,
                "weeks_until_real_time_agreed": found,
            }
        )

    matched = [
        row["weeks_until_real_time_agreed"]
        for row in rows
        if row["weeks_until_real_time_agreed"] is not None
    ]
    by_phase: dict[str, Any] = {}
    for name in PHASES:
        values = [
            row["weeks_until_real_time_agreed"]
            for row in rows
            if row["revised_phase"] == name and row["weeks_until_real_time_agreed"] is not None
        ]
        by_phase[name] = {
            "changes": sum(1 for row in rows if row["revised_phase"] == name),
            "median_delay_weeks": (float(pd.Series(values).median()) if values else None),
            "max_delay_weeks": max(values) if values else None,
        }

    return {
        "revised_phase_changes": len(rows),
        "never_matched": sum(1 for row in rows if row["weeks_until_real_time_agreed"] is None),
        "median_delay_weeks": float(pd.Series(matched).median()) if matched else None,
        "mean_delay_weeks": round(float(pd.Series(matched).mean()), 2) if matched else None,
        "by_phase": by_phase,
    }


def cost(
    revised_rotation: dict[str, Any],
    real_time_rotation: dict[str, Any],
    revised_null: dict[str, Any],
    real_time_null: dict[str, Any],
    revised_dispersion: dict[int, float],
    real_time_dispersion: dict[int, float],
) -> dict[str, Any]:
    """지연 비용. 두 추정치가 각각 우연을 이기는지 먼저 보고, 그 다음에 차이를 적는다."""

    revised_return = float(revised_rotation["rotation"]["annualised_relative_return"])
    real_time_return = float(real_time_rotation["rotation"]["annualised_relative_return"])
    gap = revised_return - real_time_return

    # 차이의 크기만 보면 안 된다. 두 추정치가 **각각** 우연과 구분되는지가 먼저다.
    # 둘 다 우연 범위 안이면 그 차이는 지연 비용이 아니라 잡음 둘을 뺀 값이다.
    revised_p = float(revised_null["p_value"])
    real_time_p = float(real_time_null["p_value"])
    measurable = revised_p <= 0.05 and real_time_p <= 0.05 and gap > 0

    return {
        "revised_rotation_annualised": round(revised_return, 4),
        "revised_rotation_p_versus_chance": revised_p,
        "real_time_rotation_annualised": round(real_time_return, 4),
        "real_time_rotation_p_versus_chance": real_time_p,
        "latency_cost_in_annualised_relative_return": round(gap, 4),
        "sign_is_backwards": bool(gap < 0),
        "either_estimate_beats_chance": bool(revised_p <= 0.05 or real_time_p <= 0.05),
        "latency_cost_is_measurable": bool(measurable),
        "dispersion_by_horizon": {
            str(horizon): {
                "revised": revised_dispersion[horizon],
                "real_time": real_time_dispersion[horizon],
                "latency_cost": round(
                    revised_dispersion[horizon] - real_time_dispersion[horizon], 9
                ),
            }
            for horizon in sorted(revised_dispersion)
        },
        "reading": (
            "두 순환매 성과가 각각 우연과 구분되지 않으면, 그 둘의 차이는 지연 비용이 아니라 "
            "잡음에서 잡음을 뺀 값이다. 그 경우 이 수치는 '선행 신호가 줄여야 할 목표'가 "
            "아니라 '현재 표본으로는 목표를 세울 수 없다'는 뜻이다."
        ),
    }
