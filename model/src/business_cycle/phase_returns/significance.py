"""48칸을 동시에 검정한다. 그러면 우연히 유의한 칸이 반드시 나온다.

## 왜 t 검정을 쓰지 않는가

전방 26주 수익률은 주마다 25주를 공유한다. 국면 라벨도 몇 달씩 이어진다. 둘 다 자기상관이
극심해서 독립 표본을 가정한 t 검정은 표준오차를 몇 배로 과소평가한다.

그래서 **순환 이동 검정**을 쓴다. 국면 라벨 계열 전체를 통째로 k주 밀어 수익률과 다시
맞춘다. 라벨의 지속 구조와 수익률의 자기상관이 둘 다 그대로 보존되고, 깨지는 것은 **둘
사이의 대응**뿐이다. 그것이 정확히 귀무가설이다 — "국면 라벨은 전방 수익률에 대해 아무
정보가 없다".

0 근처의 이동은 원래 정렬과 거의 같아서 귀무분포를 부풀린다. 그래서 ±``MINIMUM_SHIFT``
안쪽은 뺀다.
"""

from __future__ import annotations

from typing import Any, Final

import numpy as np
import pandas as pd

from .french import INDUSTRIES
from .labels import PHASES

#: 이 주 수 안쪽의 이동은 귀무표본으로 쓰지 않는다. 국면 하나의 전형적 길이보다 길게 잡는다.
MINIMUM_SHIFT: Final[int] = 52

#: 다중비교 통제. BH는 위양성 비율을, Bonferroni는 하나라도 틀릴 확률을 잡는다.
FDR_LEVEL: Final[float] = 0.10
FAMILYWISE_LEVEL: Final[float] = 0.05


def _matrices(
    phase: pd.Series, relative: pd.DataFrame
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    aligned = phase.reindex(relative.index).to_numpy()
    masks = np.vstack([(aligned == name) for name in PHASES]).astype(float)
    values = relative[list(INDUSTRIES)].to_numpy(dtype=float)
    valid = np.isfinite(values).astype(float)
    return masks, np.nan_to_num(values), valid


def _cell_means(masks: np.ndarray, values: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """(국면, 산업) 평균. 관측이 없는 칸은 NaN."""

    total = masks @ values
    count = masks @ valid
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(count > 0, total / np.where(count == 0, 1.0, count), np.nan)


def _dispersion(means: np.ndarray, counts: np.ndarray, overall: np.ndarray) -> float:
    """국면 평균이 전체 평균에서 벌어진 정도. 칸 하나가 아니라 분류 전체를 재는 통계량.

    주 수로 가중한다. 넓게 벌어지지만 1년에 두 주뿐인 국면은 실무에서 쓸 자리가 거의 없다.
    """

    deviation = (means - overall) ** 2
    weights = np.repeat(counts[:, None], means.shape[1], axis=1)
    finite = np.isfinite(deviation)
    if not finite.any():
        return float("nan")
    return float((deviation[finite] * weights[finite]).sum() / weights[finite].sum())


def _phase_dispersion(means: np.ndarray, overall: np.ndarray) -> np.ndarray:
    """국면마다 따로. 네 국면을 하나의 점수로 합치면 부분적 성공이 보이지 않는다."""

    deviation = (means - overall) ** 2
    return np.array(
        [float(np.nanmean(row)) if np.isfinite(row).any() else float("nan") for row in deviation]
    )


def shift_test(
    phase: pd.Series,
    relative: pd.DataFrame,
    minimum_shift: int = MINIMUM_SHIFT,
) -> dict[str, Any]:
    """순환 이동 귀무분포로 48칸과 전체 분류를 함께 검정한다."""

    masks, values, valid = _matrices(phase, relative)
    weeks = masks.shape[1]
    observed_means = _cell_means(masks, values, valid)
    counts = masks.sum(axis=1)

    with np.errstate(invalid="ignore", divide="ignore"):
        overall = np.where(valid.sum(axis=0) > 0, values.sum(axis=0) / valid.sum(axis=0), np.nan)
    observed_effect = observed_means - overall
    observed_dispersion = _dispersion(observed_means, counts, overall)
    observed_by_phase = _phase_dispersion(observed_means, overall)

    offsets = [k for k in range(weeks) if minimum_shift <= k <= weeks - minimum_shift]
    extreme = np.zeros_like(observed_effect)
    dispersion_extreme = 0
    phase_extreme = np.zeros(len(PHASES))
    null_dispersion: list[float] = []
    null_by_phase: list[np.ndarray] = []
    for offset in offsets:
        rolled = np.roll(masks, offset, axis=1)
        means = _cell_means(rolled, values, valid)
        effect = means - overall
        extreme += (np.abs(effect) >= np.abs(observed_effect)).astype(float)
        value = _dispersion(means, counts, overall)
        null_dispersion.append(value)
        if value >= observed_dispersion:
            dispersion_extreme += 1
        by_phase_now = _phase_dispersion(means, overall)
        null_by_phase.append(by_phase_now)
        phase_extreme += (by_phase_now >= observed_by_phase).astype(float)

    trials = len(offsets)
    # +1 보정. 관측 자체를 귀무표본 하나로 세어 p가 0이 되지 않게 한다.
    cell_p = (extreme + 1.0) / (trials + 1.0)

    # 관측이 하나도 없는 칸은 검정 대상이 아니다. NaN 비교는 항상 거짓이라 이동 통계량이
    # 관측을 한 번도 넘지 못하고, 그대로 두면 **비어 있는 칸이 가장 유의한 칸으로** 나온다.
    # 2020년을 뺀 실시간 창의 침체 칸이 정확히 그랬다.
    observed_counts = masks @ valid
    rows: list[dict[str, Any]] = []
    for row, name in enumerate(PHASES):
        for column, industry in enumerate(INDUSTRIES):
            empty = not np.isfinite(observed_effect[row, column])
            rows.append(
                {
                    "phase": name,
                    "industry": industry,
                    "weeks": int(counts[row]),
                    "observations": int(observed_counts[row, column]),
                    "mean": None if empty else round(float(observed_means[row, column]), 6),
                    "effect_versus_all_weeks": (
                        None if empty else round(float(observed_effect[row, column]), 6)
                    ),
                    "p_value": None if empty else round(float(cell_p[row, column]), 4),
                }
            )

    null_phase_matrix = np.vstack(null_by_phase) if null_by_phase else np.zeros((1, len(PHASES)))
    per_phase: list[dict[str, Any]] = []
    for row, name in enumerate(PHASES):
        draws = null_phase_matrix[:, row]
        median = float(np.nanmedian(draws)) if np.isfinite(draws).any() else float("nan")
        # 이 표본에 그 국면 주가 하나도 없으면 검정할 것이 없다. 0을 p 값으로 내보내면
        # 없는 국면이 가장 유의한 국면으로 읽힌다 — 2020년을 뺀 실시간 창이 정확히 그 경우다.
        absent = counts[row] == 0 or not np.isfinite(observed_by_phase[row])
        per_phase.append(
            {
                "phase": name,
                "weeks": int(counts[row]),
                "dispersion": None if absent else round(float(observed_by_phase[row]), 9),
                "p_value": (
                    None if absent else round(float((phase_extreme[row] + 1) / (trials + 1)), 4)
                ),
                "null_median": None if not np.isfinite(median) else round(median, 9),
                "ratio_to_null_median": (
                    round(float(observed_by_phase[row] / median), 3)
                    if not absent and np.isfinite(median) and median > 0
                    else None
                ),
            }
        )

    return {
        "shifts_used": trials,
        "by_phase": per_phase,
        "minimum_shift_weeks": minimum_shift,
        "overall_mean_by_industry": {
            industry: round(float(overall[i]), 6) for i, industry in enumerate(INDUSTRIES)
        },
        "cells": rows,
        "taxonomy_dispersion": round(observed_dispersion, 9),
        "taxonomy_dispersion_p_value": round((dispersion_extreme + 1) / (trials + 1), 4),
        "taxonomy_dispersion_null_median": round(float(np.median(null_dispersion)), 9),
        "taxonomy_dispersion_ratio_to_null_median": round(
            observed_dispersion / float(np.median(null_dispersion)), 3
        )
        if null_dispersion and float(np.median(null_dispersion)) > 0
        else None,
    }


def correct(rows: list[dict[str, Any]], fdr: float = FDR_LEVEL) -> dict[str, Any]:
    """어느 칸이 다중비교를 견디는가. 견디지 못한 칸도 함께 적는다."""

    testable = [row for row in rows if row["p_value"] is not None]
    skipped = len(rows) - len(testable)
    ordered = sorted(testable, key=lambda row: float(row["p_value"]))
    total = len(ordered)
    bonferroni = FAMILYWISE_LEVEL / total if total else float("nan")

    # Benjamini-Hochberg: p_(k) <= k/m * q 를 만족하는 가장 큰 k까지 기각.
    cutoff_rank = 0
    for rank, row in enumerate(ordered, start=1):
        if float(row["p_value"]) <= rank / total * fdr:
            cutoff_rank = rank
    survivors = ordered[:cutoff_rank]

    for rank, row in enumerate(ordered, start=1):
        row["bh_rank"] = rank
        row["survives_bh"] = rank <= cutoff_rank
        row["survives_bonferroni"] = float(row["p_value"]) <= bonferroni

    for row in rows:
        if row["p_value"] is None:
            row["bh_rank"] = None
            row["survives_bh"] = False
            row["survives_bonferroni"] = False

    return {
        "cells_tested": total,
        "cells_skipped_for_lack_of_observations": skipped,
        "fdr_level": fdr,
        "familywise_level": FAMILYWISE_LEVEL,
        "bonferroni_threshold": round(bonferroni, 6),
        "expected_false_positives_at_five_percent": round(total * 0.05, 1),
        "nominally_significant_at_five_percent": sum(
            1 for row in ordered if float(row["p_value"]) <= 0.05
        ),
        "survives_bh": [
            {
                "phase": r["phase"],
                "industry": r["industry"],
                "p": r["p_value"],
                "effect": r["effect_versus_all_weeks"],
            }
            for r in survivors
        ],
        "survives_bonferroni": [
            {
                "phase": r["phase"],
                "industry": r["industry"],
                "p": r["p_value"],
                "effect": r["effect_versus_all_weeks"],
            }
            for r in ordered
            if r["survives_bonferroni"]
        ],
        "fails_correction_but_nominally_significant": [
            {"phase": r["phase"], "industry": r["industry"], "p": r["p_value"]}
            for r in ordered
            if float(r["p_value"]) <= 0.05 and not r["survives_bh"]
        ],
    }
