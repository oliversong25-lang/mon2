"""2차 실자료 보정의 감사 가능한 평가 지표와 에피소드 추출."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pandas as pd

from ..models.transition import cyclic_distance
from ..pipeline import PipelineRun


@dataclass(frozen=True)
class ConfirmedSignals:
    state: pd.Series
    entries: list[pd.Timestamp]
    exits: list[pd.Timestamp]


def recession_prediction(history: pd.DataFrame) -> pd.Series:
    """후퇴기는 제외하고 오직 세 침체 국면만 양성으로 계산한다."""

    if "broad_phase" in history:
        return history["broad_phase"].eq("contraction").rename("predicted_recession")
    if "phase_code" not in history:
        raise ValueError("침체 판정에 broad_phase 또는 phase_code가 필요합니다")
    return (
        history["phase_code"]
        .astype(str)
        .str.startswith("contraction_")
        .rename("predicted_recession")
    )


def classification_metrics(predicted: pd.Series, actual: pd.Series) -> dict[str, float | int]:
    """주간 이진 분류 공식을 혼동 없이 한 곳에서 계산한다."""

    predicted_bool = predicted.astype(bool)
    actual_bool = actual.reindex(predicted.index).astype(bool)
    tp = int((predicted_bool & actual_bool).sum())
    fn = int((~predicted_bool & actual_bool).sum())
    fp = int((predicted_bool & ~actual_bool).sum())
    tn = int((~predicted_bool & ~actual_bool).sum())
    recall = tp / max(1, tp + fn)
    false_positive_rate = fp / max(1, fp + tn)
    precision = tp / max(1, tp + fp)
    specificity = tn / max(1, tn + fp)
    f1 = 2 * precision * recall / max(1e-12, precision + recall)
    balanced_accuracy = (recall + specificity) / 2
    return {
        "true_positive_weeks": tp,
        "false_negative_weeks": fn,
        "false_positive_weeks": fp,
        "true_negative_weeks": tn,
        "recession_recall": recall,
        "recession_false_positive_rate": false_positive_rate,
        "recession_precision": precision,
        "recession_specificity": specificity,
        "recession_f1": f1,
        "balanced_accuracy": balanced_accuracy,
    }


def causal_confirmed_signals(flags: pd.Series, minimum_weeks: int = 4) -> ConfirmedSignals:
    """연속 판정을 미래로 소급하지 않고 확인된 주부터 상태를 바꾼다."""

    if minimum_weeks < 1:
        raise ValueError("연속 확인 주 수는 1 이상이어야 합니다")
    raw = flags.astype(bool)
    confirmed = pd.Series(False, index=raw.index, dtype=bool, name="confirmed_recession")
    entries: list[pd.Timestamp] = []
    exits: list[pd.Timestamp] = []
    state = False
    true_run = 0
    false_run = 0
    for position, (timestamp, value) in enumerate(raw.items()):
        if bool(value):
            true_run += 1
            false_run = 0
        else:
            false_run += 1
            true_run = 0
        current = pd.Timestamp(str(timestamp))
        if not state and true_run >= minimum_weeks:
            state = True
            entries.append(current)
        elif state and false_run >= minimum_weeks:
            state = False
            exits.append(current)
        confirmed.iloc[position] = state
    return ConfirmedSignals(confirmed, entries, exits)


def binary_episodes(flags: pd.Series) -> list[tuple[pd.Timestamp, pd.Timestamp, int]]:
    """True가 연속된 구간을 시작·종료·주수로 반환한다."""

    values = flags.astype(bool)
    starts = values & ~values.shift(1, fill_value=False)
    episode_ids = starts.cumsum()
    episodes: list[tuple[pd.Timestamp, pd.Timestamp, int]] = []
    for _, group in values[values].groupby(episode_ids[values]):
        index = pd.DatetimeIndex(group.index)
        episodes.append((index.min(), index.max(), len(index)))
    return episodes


def turning_point_metrics(
    predicted: pd.Series,
    actual: pd.Series,
    minimum_weeks: int = 4,
    window_weeks: int = 52,
) -> list[dict[str, Any]]:
    """4주 연속 causal 확인일을 공식 침체 에피소드와 비교한다."""

    signals = causal_confirmed_signals(predicted, minimum_weeks)
    rows: list[dict[str, Any]] = []
    for start, end, _ in binary_episodes(actual):
        entries = [date for date in signals.entries if abs((date - start).days) <= 7 * window_weeks]
        entry = min(entries, key=lambda date: abs((date - start).days)) if entries else None
        exits = [
            date
            for date in signals.exits
            if abs((date - end).days) <= 7 * window_weeks and (entry is None or date > entry)
        ]
        exit_date = min(exits, key=lambda date: abs((date - end).days)) if exits else None
        rows.append(
            {
                "official_start_week": start.date().isoformat(),
                "official_end_week": end.date().isoformat(),
                "confirmed_entry_week": None if entry is None else entry.date().isoformat(),
                "entry_lead_lag_weeks": (
                    None if entry is None else round((entry - start).days / 7, 1)
                ),
                "confirmed_exit_week": None if exit_date is None else exit_date.date().isoformat(),
                "exit_lead_lag_weeks": (
                    None if exit_date is None else round((exit_date - end).days / 7, 1)
                ),
                "confirmation_rule": f"{minimum_weeks} consecutive weeks, no backdating",
            }
        )
    return rows


def stability_metrics(history: pd.DataFrame, phase_order: list[str]) -> dict[str, int]:
    phases = history["phase_code"].astype(str).tolist()
    broad = history["broad_phase"].astype(str).tolist()
    positions = {code: index for index, code in enumerate(phase_order)}
    jumps = sum(
        cyclic_distance(positions[left], positions[right], len(phase_order)) > 1
        for left, right in zip(phases, phases[1:], strict=False)
        if left != right
    )
    whipsaws = sum(
        phases[index] == phases[index + 2] != phases[index + 1]
        for index in range(max(0, len(phases) - 2))
    )
    return {
        "phase_changes": sum(a != b for a, b in zip(phases, phases[1:], strict=False)),
        "broad_changes": sum(a != b for a, b in zip(broad, broad[1:], strict=False)),
        "multi_step_jumps": jumps,
        "three_week_whipsaws": whipsaws,
    }


def _episode_cause(
    group: pd.DataFrame,
    contribution_share: pd.Series,
    release_step_events: int,
    renormalization_events: int,
    origin_scale: float,
) -> str:
    median_radius = float(group["radius"].median())
    median_y = float(group["y"].median())
    largest_share = float(contribution_share.max()) if not contribution_share.empty else 0.0
    causes: list[str] = []
    if median_radius < origin_scale:
        causes.append("원점 근처 각도 확정")
    if median_y < -origin_scale:
        causes.append("경기 수준 Y의 큰 하락")
    if largest_share >= 0.35:
        causes.append("단일 지표 신호 크기")
    if release_step_events >= max(1, len(group) // 2):
        causes.append("발표주 계단효과")
    if renormalization_events > 0:
        causes.append("결측 가중치 재정규화")
    return (
        "복합 원인: " + ", ".join(causes)
        if len(causes) > 1
        else (causes[0] if causes else "미확정")
    )


def false_positive_episode_table(
    history: pd.DataFrame,
    actual: pd.Series,
    run: PipelineRun,
    effective_weights: dict[pd.Timestamp, dict[str, float]],
    origin_scale: float,
) -> pd.DataFrame:
    """모든 오탐을 연속 에피소드로 나누고 좌표·기여·결측 원인을 기록한다."""

    predicted = recession_prediction(history)
    false_positive = predicted & ~actual.reindex(history.index).astype(bool)
    rows: list[dict[str, Any]] = []
    for episode_id, (start, end, duration) in enumerate(binary_episodes(false_positive), 1):
        group = history.loc[start:end]
        contributions = run.contributions.reindex(group.index, method="ffill")
        mean_contributions = contributions.mean().sort_values()
        absolute = contributions.abs().sum()
        shares = absolute / max(1e-12, float(absolute.sum()))
        released = run.events.reindex(group.index).notna().sum(axis=1)
        missing: set[str] = set()
        renormalized = 0
        for timestamp in group.index:
            weights = effective_weights.get(pd.Timestamp(timestamp), {})
            missing.update(
                str(column) for column in run.events.columns if str(column) not in weights
            )
            if len(weights) < len(run.events.columns):
                renormalized += 1
        before = history.loc[:start].iloc[:-1].tail(1)
        after = history.loc[end:].iloc[1:].head(1)
        phase_distribution = group["phase_code"].value_counts(normalize=True).round(4).to_dict()
        release_steps = int((released >= 2).sum())
        likely_cause = _episode_cause(group, shares, release_steps, renormalized, origin_scale)
        if duration <= 3:
            episode_type = "짧고 산발적"
        elif (
            not before.empty
            and not after.empty
            and before.iloc[0]["broad_phase"] == after.iloc[0]["broad_phase"]
        ):
            episode_type = "경계 왕복"
        elif float(group["y"].median()) < -origin_scale:
            episode_type = "지속적인 실물 약화, NBER 비침체"
        elif float(group["radius"].median()) < origin_scale:
            episode_type = "원점 근처 장기 오탐"
        else:
            episode_type = "명백한 정상기 오탐 검토"
        rows.append(
            {
                "episode_id": episode_id,
                "start_date": start.date().isoformat(),
                "end_date": end.date().isoformat(),
                "duration_weeks": duration,
                "preceding_phase": None if before.empty else str(before.iloc[0]["phase_code"]),
                "following_phase": None if after.empty else str(after.iloc[0]["phase_code"]),
                "predicted_phase_distribution": json.dumps(phase_distribution, ensure_ascii=False),
                "x_momentum_range": f"{group['x'].min():.4f}..{group['x'].max():.4f}",
                "y_level_range": f"{group['y'].min():.4f}..{group['y'].max():.4f}",
                "radius_range": f"{group['radius'].min():.4f}..{group['radius'].max():.4f}",
                "detail_confidence_range": (
                    f"{group['detail_confidence'].min():.2f}.."
                    f"{group['detail_confidence'].max():.2f}"
                ),
                "data_confidence_range": (
                    f"{group['data_confidence'].min():.2f}..{group['data_confidence'].max():.2f}"
                ),
                "top_supporting_indicators": "; ".join(
                    f"{key}={value:+.4f}"
                    for key, value in mean_contributions.tail(3)
                    .sort_values(ascending=False)
                    .items()
                ),
                "top_conflicting_indicators": "; ".join(
                    f"{key}={value:+.4f}" for key, value in mean_contributions.head(3).items()
                ),
                "weight_renormalization_events": renormalized,
                "missing_indicators": "; ".join(sorted(missing)) or "none",
                "release_step_events": release_steps,
                "episode_type": episode_type,
                "likely_cause": likely_cause,
            }
        )
    return pd.DataFrame(rows)
