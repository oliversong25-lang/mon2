"""NBER 기준일 대조. 게이트가 **가짜 전이만** 걷어냈는지, 진짜까지 지웠는지 본다.

매끄럽지만 틀린 경로는 지금 출력보다 나쁘다. 그래서 채터링이 줄었다는 사실만으로
게이트를 좋다고 하지 않는다.

ALFRED 창(2013-06-14 ~ 2026-08-14) 안의 NBER 침체는 하나뿐이다 — 2020년 2월 정점,
2020년 4월 저점. 표본이 하나라는 사실이 이 대조의 가장 큰 한계이며, 그것을 결과와
함께 적는다.
"""

from __future__ import annotations

from typing import Any, Final

import pandas as pd

from .characterise import PHASES
from .gate import GateConfig, apply

#: NBER 기준일. 월 단위 라벨이며 그 달 안의 주간 전환점을 지목하지 않는다.
NBER_PEAK_MONTH: Final[str] = "2020-02"
NBER_TROUGH_MONTH: Final[str] = "2020-04"

#: 침체 구간(월 포함). 주간 격자에 얹을 때 월 전체를 구간으로 다룬다.
RECESSION_START: Final[str] = "2020-02-01"
RECESSION_END: Final[str] = "2020-04-30"

#: 침체 밖에서 침체를 부르면 오탐이다. 창 전체에서 센다.
WINDOW_START: Final[str] = "2013-06-14"


def audit(frame: pd.DataFrame, config: GateConfig) -> dict[str, Any]:
    """한 게이트가 NBER 대비 무엇을 맞히고 무엇을 놓쳤는지."""

    result = apply(frame, config)
    weeks = [str(week) for week in result.index]
    phases = result["gated_phase"].tolist()
    status = result["gated_status"].tolist()

    inside = [
        (week, phase)
        for week, phase in zip(weeks, phases, strict=True)
        if RECESSION_START <= week <= RECESSION_END
    ]
    outside = [
        (week, phase)
        for week, phase in zip(weeks, phases, strict=True)
        if not (RECESSION_START <= week <= RECESSION_END)
    ]

    hit = [week for week, phase in inside if phase == "contraction"]
    false_positive = [week for week, phase in outside if phase == "contraction"]

    # 침체를 처음 부른 주와 NBER 정점 사이의 거리. 월 라벨이라 월초부터 잰다.
    first_call = next((week for week, phase in inside if phase == "contraction"), None)
    lead = (
        int((pd.Timestamp(first_call) - pd.Timestamp(RECESSION_START)).days // 7)
        if first_call
        else None
    )

    # 저점 이후 회복 인식. NBER 저점 월말부터 첫 회복 주까지.
    recovery = next(
        (
            week
            for week, phase in zip(weeks, phases, strict=True)
            if week > RECESSION_END and phase == "recovery"
        ),
        None,
    )
    recovery_lag = (
        int((pd.Timestamp(recovery) - pd.Timestamp(RECESSION_END)).days // 7) if recovery else None
    )

    # 오탐 구간. 흩어진 주 하나와 이어진 덩어리는 다른 문제다.
    episodes: list[dict[str, Any]] = []
    run: list[str] = []
    for week in false_positive:
        if run and pd.Timestamp(week) - pd.Timestamp(run[-1]) > pd.Timedelta(weeks=1):
            episodes.append({"start": run[0], "end": run[-1], "weeks": len(run)})
            run = []
        run.append(week)
    if run:
        episodes.append({"start": run[0], "end": run[-1], "weeks": len(run)})

    return {
        "gate": config.name,
        "nber_recession": f"{NBER_PEAK_MONTH} ~ {NBER_TROUGH_MONTH}",
        "recession_weeks_in_window": len(inside),
        "recession_weeks_called_contraction": len(hit),
        "recall": round(len(hit) / len(inside), 3) if inside else None,
        "first_contraction_call": first_call,
        "weeks_after_the_peak_month_started": lead,
        "false_positive_contraction_weeks": len(false_positive),
        "false_positive_episodes": episodes,
        "first_recovery_after_the_trough_month": recovery,
        "recovery_lag_weeks_from_trough_month_end": recovery_lag,
        "withheld_weeks": sum(1 for value in status if value == "withheld"),
        "phases_present": sorted({phase for phase in phases if phase in PHASES}),
        "single_recession_limitation": (
            "이 창에는 NBER 침체가 하나뿐이다. 재현율·오탐을 하나의 에피소드에서 재는 것이므로 "
            "일반화할 수 없다."
        ),
    }
