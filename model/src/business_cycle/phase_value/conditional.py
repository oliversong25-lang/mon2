"""검정 B — 국면에 따라 가치 수익률이 달라지는가.

주 수가 아니라 **에피소드 수**가 실질 표본이다. 104주 지평선에서 침체기 18주는 한
덩어리라 독립 관측이 1이다. 모든 표에 에피소드 수를 같이 싣는다.

겹치는 전방창 때문에 t 검정은 못 쓴다. Track 17과 같은 순환 이동 검정을 쓴다 — 국면
라벨 계열을 통째로 밀어 수익률과 다시 맞추면 두 계열의 자기상관은 보존되고 둘 사이의
대응만 깨진다.
"""

from __future__ import annotations

from typing import Any, Final

import numpy as np
import pandas as pd

from ..phase_returns.forward import _compound_forward
from ..phase_returns.labels import PHASES

#: 0 근처 이동은 원래 정렬과 거의 같아 귀무분포를 부풀린다.
MINIMUM_SHIFT: Final[int] = 52

#: 이보다 관측이 적은 칸은 평균을 내지 않는다.
MINIMUM_OBSERVATIONS: Final[int] = 8


def forward_value(hml: pd.Series, horizon: int) -> pd.Series:
    """판정 주 다음 주부터 h주간의 가치 요인 누적 수익."""

    return _compound_forward(hml, horizon)


def episodes_of(phase: pd.Series) -> dict[str, list[tuple[int, int]]]:
    """국면마다 연속 구간의 (시작, 끝) 위치. 에피소드 단위 제외에 쓴다."""

    values = [str(item) for item in phase.tolist()]
    blocks: dict[str, list[tuple[int, int]]] = {name: [] for name in PHASES}
    start = 0
    for position in range(1, len(values) + 1):
        if position == len(values) or values[position] != values[start]:
            if values[start] in blocks:
                blocks[values[start]].append((start, position - 1))
            start = position
    return blocks


def _mean(values: np.ndarray) -> float | None:
    clean = values[np.isfinite(values)]
    if clean.size < MINIMUM_OBSERVATIONS:
        return None
    return round(float(clean.mean()), 6)


def by_phase(phase: pd.Series, forward: pd.Series) -> list[dict[str, Any]]:
    """국면별 전방 가치 수익. 주 수와 에피소드 수를 나란히 둔다."""

    blocks = episodes_of(phase)
    aligned = phase.reindex(forward.index)
    rows: list[dict[str, Any]] = []
    overall = forward.dropna()
    for name in PHASES:
        values = forward[aligned.eq(name)].dropna().to_numpy(dtype=float)
        rows.append(
            {
                "phase": name,
                "weeks": int(aligned.eq(name).sum()),
                "episodes": len(blocks[name]),
                "observations": int(values.size),
                "mean_forward_value_return": _mean(values),
                "versus_all_weeks": (
                    round(float(values.mean() - overall.mean()), 6)
                    if values.size >= MINIMUM_OBSERVATIONS
                    else None
                ),
                "share_positive": (
                    round(float((values > 0).mean()), 4)
                    if values.size >= MINIMUM_OBSERVATIONS
                    else None
                ),
            }
        )
    return rows


def shift_test(phase: pd.Series, forward: pd.Series) -> dict[str, Any]:
    """국면 라벨을 순환 이동시킨 귀무분포. 칸별 p와 전체 분산 p."""

    aligned = phase.reindex(forward.index).to_numpy()
    values = forward.to_numpy(dtype=float)
    valid = np.isfinite(values).astype(float)
    filled = np.nan_to_num(values)
    masks = np.vstack([(aligned == name) for name in PHASES]).astype(float)
    weeks = masks.shape[1]

    def means(current: np.ndarray) -> np.ndarray:
        total = current @ filled
        count = current @ valid
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(count > 0, total / np.where(count == 0, 1.0, count), np.nan)

    overall = float(filled.sum() / valid.sum()) if valid.sum() else float("nan")
    observed = means(masks)
    counts = masks.sum(axis=1)
    observed_effect = observed - overall

    def dispersion(vector: np.ndarray) -> float:
        deviation = (vector - overall) ** 2
        finite = np.isfinite(deviation)
        if not finite.any():
            return float("nan")
        return float((deviation[finite] * counts[finite]).sum() / counts[finite].sum())

    observed_dispersion = dispersion(observed)
    offsets = [k for k in range(weeks) if MINIMUM_SHIFT <= k <= weeks - MINIMUM_SHIFT]
    extreme = np.zeros(len(PHASES))
    dispersion_extreme = 0
    for offset in offsets:
        drawn = means(np.roll(masks, offset, axis=1))
        effect = drawn - overall
        extreme += (np.abs(effect) >= np.abs(observed_effect)).astype(float)
        if dispersion(drawn) >= observed_dispersion:
            dispersion_extreme += 1

    trials = len(offsets)
    return {
        "shifts_used": trials,
        "overall_mean": round(overall, 6),
        "cells": [
            {
                "phase": name,
                "weeks": int(counts[row]),
                "effect_versus_all_weeks": (
                    round(float(observed_effect[row]), 6)
                    if np.isfinite(observed_effect[row])
                    else None
                ),
                "p_value": (
                    round(float((extreme[row] + 1) / (trials + 1)), 4)
                    if np.isfinite(observed_effect[row])
                    else None
                ),
            }
            for row, name in enumerate(PHASES)
        ],
        "dispersion": round(observed_dispersion, 9),
        "dispersion_p_value": round((dispersion_extreme + 1) / (trials + 1), 4),
    }
