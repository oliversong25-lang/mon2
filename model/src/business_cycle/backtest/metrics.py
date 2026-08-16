"""대국면·12국면 안정성·NBER 비교 지표."""

from __future__ import annotations

from itertools import groupby
from typing import Any

import numpy as np
import pandas as pd

from ..models.transition import cyclic_distance
from .nber import NBER_RECESSIONS, recession_flags


def _durations(values: list[str]) -> list[int]:
    return [sum(1 for _ in group) for _, group in groupby(values)]


def _nearest_position(index: pd.DatetimeIndex, date: pd.Timestamp) -> int:
    deltas = (index - date).to_numpy(dtype="timedelta64[ns]").astype("int64")
    return int(np.argmin(np.abs(deltas)))


def _episode_checks(history: pd.DataFrame) -> list[dict[str, Any]]:
    """사용 가능한 NBER 전환점 주변 좌표와 신호 lead/lag를 기록한다."""

    index = pd.DatetimeIndex(history.index)
    predicted = history["broad_phase"].eq("contraction").to_numpy()
    episodes: list[dict[str, Any]] = []
    for raw_start, raw_end in NBER_RECESSIONS:
        start, end = pd.Timestamp(raw_start), pd.Timestamp(raw_end)
        if end < index.min() or start > index.max():
            continue
        start_position = _nearest_position(index, start)
        end_position = _nearest_position(index, end)
        entry_window = range(max(0, start_position - 26), min(len(index), start_position + 27))
        exit_window = range(max(0, end_position - 26), min(len(index), end_position + 27))
        entries = [position for position in entry_window if predicted[position]]
        exits = [position for position in exit_window if not predicted[position]]
        entry = (
            min(entries, key=lambda position: abs(position - start_position)) if entries else None
        )
        exit_position = (
            min(exits, key=lambda position: abs(position - end_position)) if exits else None
        )
        around: dict[str, Any] = {}
        for name, position in (("entry", start_position), ("exit", end_position)):
            around[name] = {
                str(offset): {
                    "x": float(history.iloc[max(0, min(len(history) - 1, position + offset))]["x"]),
                    "y": float(history.iloc[max(0, min(len(history) - 1, position + offset))]["y"]),
                }
                for offset in (-4, 0, 4)
            }
        episodes.append(
            {
                "nber_start": raw_start,
                "nber_end": raw_end,
                "entry_signal_lead_lag_weeks": None if entry is None else entry - start_position,
                "exit_signal_lead_lag_weeks": (
                    None if exit_position is None else exit_position - end_position
                ),
                "coordinates_around_turning_points": around,
            }
        )
    return episodes


def backtest_metrics(history: pd.DataFrame, phase_order: list[str]) -> dict[str, Any]:
    """NBER를 12국면 정답으로 오용하지 않고 평가 영역을 나눠 계산한다."""

    phases = history["phase_code"].astype(str).tolist()
    transitions = sum(left != right for left, right in zip(phases, phases[1:], strict=False))
    whipsaws = sum(
        phases[index] == phases[index + 2] != phases[index + 1]
        for index in range(max(0, len(phases) - 2))
    )
    index_map = {code: index for index, code in enumerate(phase_order)}
    jumps = sum(
        cyclic_distance(index_map[left], index_map[right], len(phase_order)) > 1
        for left, right in zip(phases, phases[1:], strict=False)
        if left != right
    )
    durations = _durations(phases)
    actual = recession_flags(pd.DatetimeIndex(history.index))
    predicted = history["broad_phase"].eq("contraction")
    tp = int((actual & predicted).sum())
    fn = int((actual & ~predicted).sum())
    fp = int((~actual & predicted).sum())
    tn = int((~actual & ~predicted).sum())
    probability_columns = [column for column in history if str(column).startswith("p_")]
    probabilities = history[probability_columns].to_numpy(dtype=float)
    sorted_probabilities = np.sort(probabilities, axis=1)
    gaps = sorted_probabilities[:, -1] - sorted_probabilities[:, -2]
    if len(gaps) > 4:
        high_confidence = gaps[:-4] >= np.quantile(gaps, 0.75)
        stable_next_four = np.array(
            [
                all(phases[index] == phases[index + offset] for offset in range(1, 5))
                for index in range(len(phases) - 4)
            ]
        )
        high_confidence_stability = (
            float(stable_next_four[high_confidence].mean()) if high_confidence.any() else None
        )
    else:
        high_confidence_stability = None
    return {
        "weeks": int(len(history)),
        "phase_transitions": transitions,
        "multi_step_jumps": jumps,
        "boundary_whipsaws": whipsaws,
        "average_phase_duration_weeks": float(np.mean(durations)) if durations else 0.0,
        "weekly_phase_volatility": transitions / max(1, len(history) - 1),
        "nber": {
            "recession_recall": tp / max(1, tp + fn),
            "recession_false_positive_rate": fp / max(1, fp + tn),
            "recession_precision": tp / max(1, tp + fp),
            "true_positive_weeks": tp,
            "false_negative_weeks": fn,
            "false_positive_weeks": fp,
            "true_negative_weeks": tn,
            "episodes": _episode_checks(history),
        },
        "confidence_diagnostics": {
            "top_quartile_gap_next_4w_stability": high_confidence_stability,
            "median_top_two_gap": float(np.median(gaps)),
        },
        "note": "NBER는 침체/비침체 비교에만 사용했으며 12국면 정확도는 계산하지 않았습니다.",
    }
