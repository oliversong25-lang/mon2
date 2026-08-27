"""단계 A-4: 후보 G의 사건 날짜 정의·기하·영점 중심·점프·워밍업 민감도를 감사한다.

단계 A-3은 후보 G에 두 가지 의문을 남겼다. 2020년 조기 수축 신호와 다단계 점프 6건,
그리고 한계값에 정확히 걸린 8주 결과다. 여기서는 모델을 고치기 전에 **정의부터** 검증한다.
"보고된 수치가 무엇을 세고 있는가"를 확인하지 않고 모델을 바꾸면 없는 병을 고치게 된다.
"""

# ruff: noqa: E501

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from ..config import Settings
from ..models.phase import phase_definitions
from .phase2 import ModelEvaluation
from .phase2_metrics import binary_episodes, recession_prediction

#: 팬데믹 구간 감사 창. NBER 시작 이전 반년부터 본다.
PANDEMIC_WINDOW = ("2019-09-01", "2020-05-31")

#: 4주 연속 확인 규칙. 백테스트 지표와 같은 값을 쓴다.
CONFIRMATION_WEEKS = 4

#: 실업수당 두 계열은 독립 영역이 아니라 하나의 하위군이다.
CLAIMS_SUBGROUP = ("ICSA", "CCSA")


@dataclass(frozen=True)
class EventTiming:
    """서로 다른 시점 개념을 한 이름으로 뭉치지 않는다."""

    case: str
    first_contraction_signal_date: str
    continuous_episode_start_date: str
    confirmation_decision_date: str
    confirmed_episode_effective_date: str
    nber_reference_week: str
    entry_lead_lag_from_first_signal: float
    entry_lead_lag_from_confirmation_decision: float
    pre_nber_false_positive_weeks: int
    post_nber_false_positive_weeks: int
    within_nber_true_positive_weeks: int


def _weeks(left: pd.Timestamp, right: pd.Timestamp) -> float:
    return round((left - right).days / 7.0, 1)


def event_timing(
    history: pd.DataFrame,
    actual: pd.Series,
    case: str,
    nber_start: pd.Timestamp,
    nber_end: pd.Timestamp,
    search_start: pd.Timestamp,
) -> EventTiming:
    """한 사례의 시점 개념들을 각각 따로 계산한다.

    ``confirmation_decision_date``는 4주 연속이 처음 충족된 주다.
    ``confirmed_episode_effective_date``는 그 연속이 시작된 주로, 결정일보다 3주 앞선다.
    둘을 한 필드로 쓰면 "언제 알았는가"와 "언제부터였는가"가 섞인다.
    """

    predicted = recession_prediction(history)
    flags = actual.reindex(history.index).astype(bool)
    scope = predicted.loc[search_start:]

    first_signal = scope[scope].index.min() if scope.any() else pd.NaT
    confirmed = predicted.rolling(CONFIRMATION_WEEKS).sum().eq(CONFIRMATION_WEEKS)
    confirmed_scope = confirmed.loc[search_start:]
    decision = confirmed_scope[confirmed_scope].index.min() if confirmed_scope.any() else pd.NaT
    index = pd.DatetimeIndex(history.index)
    effective: pd.Timestamp | None = None
    episode_start: pd.Timestamp | None = None
    if pd.notna(decision):
        position = int(index.get_indexer(pd.DatetimeIndex([decision]))[0])
        effective = pd.Timestamp(index[max(0, position - CONFIRMATION_WEEKS + 1)])
    if pd.notna(first_signal):
        # 연속 구간의 시작은 검색 시작일 이전으로 거슬러 올라갈 수 있다.
        for start, end, _ in binary_episodes(predicted):
            if start <= first_signal <= end:
                episode_start = pd.Timestamp(start)
                break

    before = predicted & ~flags & (predicted.index < nber_start)
    after = predicted & ~flags & (predicted.index > nber_end)
    window = (predicted.index >= search_start) & (
        predicted.index <= nber_end + pd.Timedelta(weeks=60)
    )
    return EventTiming(
        case=case,
        first_contraction_signal_date=str(first_signal.date()) if pd.notna(first_signal) else "",
        continuous_episode_start_date=str(episode_start.date())
        if episode_start is not None
        else "",
        confirmation_decision_date=str(decision.date()) if pd.notna(decision) else "",
        confirmed_episode_effective_date=str(effective.date()) if effective is not None else "",
        nber_reference_week=str(nber_start.date()),
        entry_lead_lag_from_first_signal=(
            _weeks(first_signal, nber_start) if pd.notna(first_signal) else float("nan")
        ),
        entry_lead_lag_from_confirmation_decision=(
            _weeks(decision, nber_start) if pd.notna(decision) else float("nan")
        ),
        pre_nber_false_positive_weeks=int((before & window).sum()),
        post_nber_false_positive_weeks=int((after & window).sum()),
        within_nber_true_positive_weeks=int((predicted & flags).sum()),
    )


def pandemic_timeline(evaluation: ModelEvaluation, actual: pd.Series, name: str) -> pd.DataFrame:
    """2019년 가을부터 2020년 봄까지 주 단위로 모든 층을 남긴다."""

    run = evaluation.backtest.run
    start, end = PANDEMIC_WINDOW
    history = run.history.loc[start:end]
    predicted = recession_prediction(run.history)
    confirmed = predicted.rolling(CONFIRMATION_WEEKS).sum().eq(CONFIRMATION_WEEKS)
    episodes = {}
    for index, (episode_start, episode_end, _) in enumerate(binary_episodes(predicted), start=1):
        for week in run.history.loc[episode_start:episode_end].index:
            episodes[week] = index
    contributions = run.contributions.reindex(run.history.index, method="ffill")
    events = run.events.reindex(run.history.index)
    audit = run.coordinate_audit.reindex(run.history.index)
    settings = evaluation.settings.indicators["indicators"]
    domains = sorted({str(value["domain"]) for value in settings.values()})
    weights = evaluation.effective_weights

    rows: list[dict[str, Any]] = []
    for week, entry in history.iterrows():
        timestamp = pd.Timestamp(str(week))
        contribution = contributions.loc[timestamp]
        domain_totals = {
            domain: float(
                sum(
                    float(contribution.get(indicator, 0.0))
                    for indicator, config in settings.items()
                    if str(config["domain"]) == domain and indicator in contribution
                )
            )
            for domain in domains
        }
        released = [
            str(column) for column in events.columns if pd.notna(events.loc[timestamp, column])
        ]
        missing = [
            str(column)
            for column in events.columns
            if pd.isna(contributions.loc[timestamp, column])
            or float(str(contributions.loc[timestamp, column])) == 0.0
        ]
        week_weights = weights.get(timestamp, {})
        row = {
            "candidate": name,
            "week": str(timestamp.date()),
            "usrec": int(bool(actual.reindex([timestamp]).fillna(False).iloc[0])),
            "broad_phase": str(entry["broad_phase"]),
            "detail_phase": str(entry["phase_code"]),
            "contraction_probability": float(
                sum(
                    float(entry[column])
                    for column in history.columns
                    if str(column).startswith("p_contraction_")
                )
            ),
            "slowdown_probability": float(
                sum(
                    float(entry[column])
                    for column in history.columns
                    if str(column).startswith("p_slowdown_")
                )
            ),
            "first_call": bool(predicted.loc[timestamp]),
            "four_week_confirmed": bool(confirmed.loc[timestamp]),
            "episode_id": episodes.get(timestamp, 0),
            "x": float(entry["x"]),
            "y": float(entry["y"]),
            "radius": float(entry["radius"]),
            "angle": float(entry["angle"]),
            "coordinate_scale": float(str(audit.loc[timestamp, "coordinate_scale"])),
            "available_indicators": int(len(week_weights)),
            "released_this_week": ",".join(released),
            "missing_or_zero": ",".join(missing),
            "weight_sum": float(sum(week_weights.values())) if week_weights else 0.0,
            "negative_domains": int(sum(1 for value in domain_totals.values() if value < 0)),
            "strongly_negative_domains": int(
                sum(1 for value in domain_totals.values() if value < -0.05)
            ),
        }
        for domain, value in domain_totals.items():
            row[f"domain_{domain}"] = value
        for indicator in contributions.columns:
            row[f"contrib_{indicator}"] = float(str(contribution.get(indicator, np.nan)))
        rows.append(row)
    return pd.DataFrame(rows)


def geometry_audit(evaluation: ModelEvaluation, name: str) -> pd.DataFrame:
    """후보 E처럼 각도가 축으로 무너지지 않았는지 측정한다."""

    run = evaluation.backtest.run
    history = run.history
    audit = run.coordinate_audit.reindex(history.index)
    phases = phase_definitions(evaluation.settings.transitions["phases"])
    unscaled_x = audit["unscaled_x"].dropna()
    unscaled_y = audit["unscaled_y"].dropna()
    edges = [float(value) for value in np.arange(0, 391, 30)]
    sectors = pd.Series(pd.cut(history["angle"], bins=edges, right=False))
    occupancy = sectors.value_counts(normalize=True).sort_index()
    probability_columns = [f"p_{phase.code}" for phase in phases]
    probabilities = history[probability_columns].to_numpy(dtype=float)
    safe = np.where(probabilities > 0, probabilities, 1.0)
    entropy = -np.sum(np.where(probabilities > 0, probabilities * np.log(safe), 0.0), axis=1)
    top = probabilities.max(axis=1)
    order = np.sort(probabilities, axis=1)
    gap = order[:, -1] - order[:, -2]
    boundary = history["angle"].apply(lambda value: float(min(value % 30.0, 30.0 - value % 30.0)))
    movement = (
        history["angle"]
        .diff()
        .apply(
            lambda value: float("nan") if pd.isna(value) else abs((value + 180.0) % 360.0 - 180.0)
        )
    )
    near_origin = history["radius"] < float(
        evaluation.settings.model.get("phase_origin_scale", 0.75)
    )
    return pd.DataFrame(
        [
            {
                "candidate": name,
                "unscaled_x_std": float(unscaled_x.std()),
                "unscaled_y_std": float(unscaled_y.std()),
                "unscaled_ratio": float(unscaled_x.std() / unscaled_y.std()),
                "scaled_x_std": float(history["x"].std()),
                "scaled_y_std": float(history["y"].std()),
                "scaled_ratio": float(history["x"].std() / history["y"].std()),
                "max_sector_share": float(occupancy.max()),
                "top_two_sector_share": float(occupancy.nlargest(2).sum()),
                "vertical_sector_share": float(
                    occupancy.iloc[2] + occupancy.iloc[3] + occupancy.iloc[8] + occupancy.iloc[9]
                ),
                "sector_shares": json.dumps(
                    [round(float(value), 4) for value in occupancy.to_numpy()]
                ),
                "max_detail_phase_share": float(
                    history["phase_code"].value_counts(normalize=True).max()
                ),
                "max_broad_phase_share": float(
                    history["broad_phase"].value_counts(normalize=True).max()
                ),
                "near_origin_share": float(near_origin.mean()),
                "radius_median": float(history["radius"].median()),
                "radius_p90": float(history["radius"].quantile(0.9)),
                "boundary_distance_median": float(boundary.median()),
                "weekly_angle_move_median": float(movement.median()),
                "weekly_angle_move_p95": float(movement.quantile(0.95)),
                "emission_entropy_median": float(np.median(entropy)),
                "weeks_top_probability_above_90pct": int((top > 0.9).sum()),
                "weeks_large_gap_near_origin": int(((gap > 0.7) & near_origin.to_numpy()).sum()),
            }
        ]
    )


def zero_center_audit(evaluation: ModelEvaluation, name: str, actual: pd.Series) -> pd.DataFrame:
    """영점 중심 가정이 자료에서 얼마나 지켜지는지 확인한다.

    가중치·결측·성숙도가 시간에 따라 바뀌므로 실현 평균이 매 시점 정확히 0일 수는 없다.
    영점을 원칙적 기준으로 쓸 수 있는지는 편차의 크기로 판단한다.
    """

    run = evaluation.backtest.run
    factor = run.composite.dropna()
    flags = actual.reindex(factor.index).fillna(False).astype(bool)
    scale = float(run.coordinate_audit["coordinate_scale"].median())
    rows: list[dict[str, Any]] = []

    def add(scope: str, key: str, series: pd.Series) -> None:
        if series.empty:
            return
        rows.append(
            {
                "candidate": name,
                "scope": scope,
                "key": key,
                "weeks": int(len(series)),
                "mean": float(series.mean()),
                "median": float(series.median()),
                "std": float(series.std()),
                # 중심 편차를 척도로 나눈 값이 곧 Y가 밀리는 정도다.
                "mean_in_scale_units": float(series.mean() / scale) if scale > 0 else np.nan,
            }
        )

    add("full_sample", "all", factor)
    years = pd.DatetimeIndex(factor.index).year
    for decade in sorted({int(year) // 10 * 10 for year in years}):
        add("decade", str(decade), factor[(years // 10 * 10) == decade])
    add("regime", "recession", factor[flags])
    add("regime", "expansion", factor[~flags])
    rolling = factor.shift(1).rolling(522, min_periods=104).mean().dropna()
    add("causal_rolling_mean", "10y", rolling)
    weights = evaluation.effective_weights
    counts = pd.Series(
        {timestamp: len(values) for timestamp, values in weights.items()}, dtype=float
    ).reindex(factor.index)
    for available in sorted(counts.dropna().unique()):
        add("available_indicators", str(int(available)), factor[counts.eq(available)])
    return pd.DataFrame(rows)


def jump_audit(
    evaluation: ModelEvaluation, actual: pd.Series, name: str, settings: Settings
) -> pd.DataFrame:
    """다단계 점프를 하나씩 증거와 함께 기록하고 분류한다."""

    run = evaluation.backtest.run
    history = evaluation.history
    phases = phase_definitions(settings.transitions["phases"])
    order = {phase.code: index for index, phase in enumerate(phases)}
    size = len(phases)
    contributions = run.contributions.reindex(history.index, method="ffill")
    events = run.events.reindex(history.index)
    indicator_settings = settings.indicators["indicators"]
    origin_scale = float(settings.model.get("phase_origin_scale", 0.75))
    dynamic = run.dynamic.reindex(history.index)
    composite = run.composite.reindex(history.index)

    rows: list[dict[str, Any]] = []
    codes = history["phase_code"].astype(str)
    for position in range(1, len(codes)):
        previous, current = codes.iloc[position - 1], codes.iloc[position]
        if previous == current:
            continue
        distance = abs(order[previous] - order[current])
        steps = min(distance, size - distance)
        if steps <= 1:
            continue
        week = history.index[position]
        contribution = contributions.loc[week]
        domain_totals: dict[str, float] = {}
        for indicator, config in indicator_settings.items():
            if indicator in contribution:
                domain = str(config["domain"])
                domain_totals[domain] = domain_totals.get(domain, 0.0) + float(
                    contribution[indicator]
                )
        # 실업수당 두 계열은 하나의 하위군이므로 영역 폭에서 한 번만 센다.
        negative = sum(1 for value in domain_totals.values() if value < 0)
        released = [str(column) for column in events.columns if pd.notna(events.loc[week, column])]
        absolute = contribution.abs()
        dominant_share = (
            float(absolute.max() / absolute.sum()) if float(absolute.sum()) > 0 else np.nan
        )
        radius = float(history.iloc[position]["radius"])
        forward = codes.iloc[position : min(position + 5, len(codes))]
        persists = bool((forward == current).sum() >= 4)
        rows.append(
            {
                "candidate": name,
                "date": str(pd.Timestamp(str(week)).date()),
                "previous_phase": previous,
                "new_phase": current,
                "steps": steps,
                "x": float(history.iloc[position]["x"]),
                "y": float(history.iloc[position]["y"]),
                "radius": radius,
                "angle": float(history.iloc[position]["angle"]),
                "near_origin": bool(radius < origin_scale),
                "winner_probability": float(history.iloc[position][f"p_{current}"]),
                "previous_phase_probability": float(history.iloc[position][f"p_{previous}"]),
                "negative_domains": negative,
                "dominant_indicator": str(absolute.idxmax()) if len(absolute) else "",
                "dominant_share": dominant_share,
                "released_this_week": ",".join(released),
                "release_count": len(released),
                "persists_four_weeks": persists,
                "usrec": int(bool(actual.reindex([week]).fillna(False).iloc[0])),
                "composite": float(composite.loc[week]),
                "dynamic": float(dynamic.loc[week]) if pd.notna(dynamic.loc[week]) else np.nan,
                "dynamic_agrees_sign": bool(
                    pd.notna(dynamic.loc[week])
                    and np.sign(float(dynamic.loc[week])) == np.sign(float(composite.loc[week]))
                ),
                "contributions": json.dumps(
                    {str(k): round(float(v), 4) for k, v in contribution.items()},
                    ensure_ascii=False,
                ),
                "domain_contributions": json.dumps(
                    {k: round(v, 4) for k, v in domain_totals.items()}, ensure_ascii=False
                ),
            }
        )
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    frame["classification"] = [
        _classify_jump({str(key): value for key, value in row.items()})
        for row in frame.to_dict("records")
    ]
    frame["justified"] = frame["classification"].isin(
        {
            "economically justified shock jump",
            "economically plausible but uncertain",
        }
    )
    return frame


#: 같은 대국면 안에서의 이동인지, 대국면을 넘는 이동인지를 먼저 나눈다.
BROAD_OF = {
    "recovery_early": "recovery",
    "recovery_mid": "recovery",
    "recovery_late": "recovery",
    "expansion_early": "expansion",
    "expansion_mid": "expansion",
    "expansion_late": "expansion",
    "slowdown_early": "slowdown",
    "slowdown_mid": "slowdown",
    "slowdown_late": "slowdown",
    "contraction_early": "contraction",
    "contraction_mid": "contraction",
    "contraction_late": "contraction",
}


def _classify_jump(row: dict[str, Any]) -> str:
    """증거로 분류한다. 반지름·영역 폭·기여 집중·지속성·대국면 이동을 함께 본다.

    같은 대국면 안에서의 세부국면 재배치는 침체 판정에 영향을 주지 않는다. 원점 근처가
    아니고 한 지표가 지배하지도 않으며 지속되는 이동이라면 해석 가능한 이동으로 본다.
    대국면을 넘는 이동에는 더 넓은 근거를 요구한다.
    """

    radius = float(row["radius"])
    breadth = int(row["negative_domains"])
    dominant = float(row["dominant_share"]) if pd.notna(row["dominant_share"]) else 0.0
    crosses_broad = BROAD_OF.get(str(row["previous_phase"])) != BROAD_OF.get(str(row["new_phase"]))
    if radius >= 1.5 and breadth >= 3:
        return "economically justified shock jump"
    if row["near_origin"] and not row["persists_four_weeks"]:
        return "low-radius model instability"
    if dominant >= 0.5:
        return "single-indicator domination"
    if int(row["release_count"]) <= 1 and row["near_origin"]:
        return "release-step artifact"
    if not row["persists_four_weeks"]:
        return "unresolved"
    if not crosses_broad:
        # 대국면이 그대로면 침체 판정은 바뀌지 않는다. 지속되고 원점 밖이면 해석 가능하다.
        return "economically plausible but uncertain"
    if breadth >= 2:
        return "economically plausible but uncertain"
    return "unresolved"
