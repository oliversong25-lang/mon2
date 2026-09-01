"""트랙 17 기계를 persist17w 라벨 위에서 그대로 돌린다.

새 절차를 만들지 않는다. 만들면 전후 비교가 "라벨이 달라져서"인지 "재는 법이 달라져서"인지
갈리지 않는다. 에피소드 분할, 이동 귀무분포, BH 보정, 두 라벨링, 2020 의존성 검사 —
전부 `phase_returns`의 것을 그대로 부른다.

## 천장이 막혔을 때 무엇을 계속하는가

순환매는 판정이 끝났으므로 **숫자만 남기고 결론을 달지 않는다.** 판별력은 다른 질문이라
예정대로 낸다 — 트랙 22가 "분류에 뜻이 있는가"에 답했고, 여기서는 그 답이 트랙 17의
격자 위에서 어떻게 보이는지를 나란히 놓는다.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from ..phase_returns import distribution as D
from ..phase_returns import forward as F
from ..phase_returns import rotation as R
from ..phase_returns import samples as SA
from ..phase_returns import significance as SIG
from ..phase_returns.labels import PHASES
from . import prespec

#: 트랙 17이 쓴 것과 같은 이동 간격. 확장 창 재추정이 이동마다 필요해 솎는다.
ROTATION_NULL_STRIDE = 4

#: 688주 창에서는 52주 요건을 그대로 쓰면 거래 주가 너무 줄어든다. 트랙 17과 같은 값.
SHORT_WINDOW_MINIMUM_HISTORY = 26


def analyse(sample: SA.Sample, weekly: pd.DataFrame) -> dict[str, Any]:
    """한 표본의 세 지평선 판별력. 트랙 17의 `_analyse`와 같은 순서다."""

    phase = sample.phase.reindex(sample.weeks)
    out: dict[str, Any] = {"profile": sample.profile(), "horizons": {}}
    for horizon in prespec.HORIZONS:
        relative = F.forward_relative(weekly, horizon).reindex(sample.weeks)
        test = SIG.shift_test(phase, relative)
        out["horizons"][str(horizon)] = {
            "coverage": F.coverage(relative),
            "cells": D.cells(phase, relative),
            "separability": D.separability(phase, relative),
            "shift_test": test,
            "multiple_comparison": SIG.correct(test["cells"]),
        }
    return out


def rotate(sample: SA.Sample, weekly: pd.DataFrame, minimum: int) -> dict[str, Any]:
    """한 표본의 순환매. 천장이 막혔더라도 숫자는 남긴다."""

    phase = sample.phase.reindex(sample.weeks)
    frame = weekly.reindex(sample.weeks)
    result = R.run(phase, frame, minimum=minimum)
    result["null"] = R.shift_null(
        phase, frame, SIG.MINIMUM_SHIFT, minimum=minimum, stride=ROTATION_NULL_STRIDE
    )
    result["sample"] = sample.name
    result["excess_over_equal_weight"] = result["rotation_minus_equal_weight"]
    return result


def _phase_map(block: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["phase"]: row for row in block["shift_test"]["by_phase"]}


def taxonomy_rows(
    before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """분류 전체의 분산 비율. 표본 x 지평선 한 줄씩, 트랙 17 숫자와 나란히."""

    rows: list[dict[str, Any]] = []
    for name in sorted(set(before) & set(after)):
        for horizon in prespec.HORIZONS:
            key = str(horizon)
            old = before[name]["horizons"][key]
            new = after[name]["horizons"][key]
            rows.append(
                {
                    "sample": name,
                    "horizon_weeks": horizon,
                    "v1_1_dispersion_ratio": old["shift_test"][
                        "taxonomy_dispersion_ratio_to_null_median"
                    ],
                    "v1_1_p": old["shift_test"]["taxonomy_dispersion_p_value"],
                    "persist17w_dispersion_ratio": new["shift_test"][
                        "taxonomy_dispersion_ratio_to_null_median"
                    ],
                    "persist17w_p": new["shift_test"]["taxonomy_dispersion_p_value"],
                    "v1_1_cells_surviving_bh": len(old["multiple_comparison"]["survives_bh"]),
                    "persist17w_cells_surviving_bh": len(new["multiple_comparison"]["survives_bh"]),
                    "cells_tested": new["multiple_comparison"]["cells_tested"],
                    "expected_false_positives": new["multiple_comparison"][
                        "expected_false_positives_at_five_percent"
                    ],
                }
            )
    return rows


def per_phase_rows(
    before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """국면별 분산 비율. 한 국면만 좋아지고 다른 국면이 나빠지면 그것은 교환이다."""

    rows: list[dict[str, Any]] = []
    for name in sorted(set(before) & set(after)):
        for horizon in prespec.HORIZONS:
            key = str(horizon)
            old = _phase_map(before[name]["horizons"][key])
            new = _phase_map(after[name]["horizons"][key])
            for phase in PHASES:
                rows.append(
                    {
                        "sample": name,
                        "horizon_weeks": horizon,
                        "phase": phase,
                        "v1_1_weeks": old[phase]["weeks"],
                        "v1_1_ratio": old[phase]["ratio_to_null_median"],
                        "v1_1_p": old[phase]["p_value"],
                        "persist17w_weeks": new[phase]["weeks"],
                        "persist17w_ratio": new[phase]["ratio_to_null_median"],
                        "persist17w_p": new[phase]["p_value"],
                    }
                )
    return rows


def covid_dependence(
    full: dict[str, Any], ex_covid: dict[str, Any], ex_gfc: dict[str, Any]
) -> dict[str, Any]:
    """트랙 17의 핵심 발견을 다시 건다 — 결과가 2020년 하나에 얹혀 있는가."""

    rows: list[dict[str, Any]] = []
    for horizon in prespec.HORIZONS:
        key = str(horizon)
        whole = full["horizons"][key]["shift_test"]
        without_covid = ex_covid["horizons"][key]["shift_test"]
        without_gfc = ex_gfc["horizons"][key]["shift_test"]
        base = whole["taxonomy_dispersion_ratio_to_null_median"]
        removed = without_covid["taxonomy_dispersion_ratio_to_null_median"]
        rows.append(
            {
                "horizon_weeks": horizon,
                "full": base,
                "full_p": whole["taxonomy_dispersion_p_value"],
                "ex_covid": removed,
                "ex_covid_p": without_covid["taxonomy_dispersion_p_value"],
                "ex_gfc": without_gfc["taxonomy_dispersion_ratio_to_null_median"],
                "ex_gfc_p": without_gfc["taxonomy_dispersion_p_value"],
                "retained_without_covid": (
                    round(float(removed) / float(base), 3) if base and removed is not None else None
                ),
            }
        )
    kept = [row["retained_without_covid"] for row in rows if row["retained_without_covid"]]
    average = round(sum(kept) / len(kept), 3) if kept else None

    # 비율만 보면 남았다고 읽힌다. p가 어떻게 되는지를 함께 봐야 한다 — 비율이 남아도
    # 유의성이 사라지면 그것은 "남았다"가 아니라 "표본이 줄었다"일 수 있다.
    significant_before = sum(1 for row in rows if float(row["full_p"]) <= 0.10)
    significant_after = sum(1 for row in rows if float(row["ex_covid_p"]) <= 0.10)
    return {
        "rows": rows,
        "average_retained_without_covid": average,
        "horizons_significant_at_ten_percent_full": significant_before,
        "horizons_significant_at_ten_percent_ex_covid": significant_after,
        "reading": (
            (
                "2020년을 빼면 판별력 비율이 크게 줄어든다. 결과가 그 한 에피소드에 얹혀 있다."
                if average is not None and average < 0.6
                else f"2020년을 빼도 판별력 비율은 {average:.0%} 남는다 — 트랙 17에서 "
                "무너졌던 것과 다르다. 경계 수정이 실제로 바꾼 것이 여기다."
            )
            + (
                f" 다만 **유의성은 남지 않는다.** 10% 수준에서 유의한 지평선이 "
                f"{significant_before}개에서 {significant_after}개로 준다. 비율이 남았다는 "
                "것과 통계적으로 성립한다는 것은 다른 말이고, 여기서는 앞의 것만 참이다."
                if significant_after < significant_before
                else ""
            )
        ),
    }
