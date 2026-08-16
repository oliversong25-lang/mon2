"""수집된 관측에서 주간 경기좌표와 12개 국면 판정을 만드는 파이프라인."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from .config import Settings
from .data.availability import apply_availability_dates, validate_observations
from .models.composite import CompositeFactorModel
from .models.confidence import broad_confidence, data_confidence, detail_confidence
from .models.dynamic_factor import DynamicFactorModel
from .models.leading import preliminary_leading_score
from .models.momentum import coordinates
from .models.phase import emission_probabilities, phase_definitions
from .models.transition import cyclic_distance, filter_probabilities, transition_matrix
from .preprocessing.frequency import weekly_event_matrix
from .preprocessing.transforms import transform_observations
from .schemas import ModelResult

PRELIMINARY_WARNING = (
    "본 결과는 최신 수정치 기준의 preliminary backtest이며, 당시 실제 공개정보만 사용한 "
    "real-time vintage backtest가 아닙니다."
)


@dataclass
class PipelineRun:
    """현재 결과와 주간 전체 이력을 함께 반환한다."""

    result: ModelResult
    history: pd.DataFrame
    events: pd.DataFrame
    composite: pd.Series
    dynamic: pd.Series
    contributions: pd.DataFrame


def _agreement(left: float, right: float) -> float:
    if not np.isfinite(left) or not np.isfinite(right):
        return 0.0
    return float(np.exp(-abs(left - right)))


def _quality(
    observations: pd.DataFrame,
    as_of: pd.Timestamp,
    indicator_settings: dict[str, Any],
) -> tuple[float, float]:
    latest = observations.groupby("indicator_id")["release_date"].max()
    freshness_values: list[float] = []
    available = 0
    for indicator_id, config in indicator_settings.items():
        date = latest.get(indicator_id, pd.NaT)
        if pd.isna(date):
            freshness_values.append(0.0)
            continue
        age_weeks = max(0.0, float((as_of - pd.Timestamp(date)).days) / 7.0)
        max_age = max(1.0, float(config.get("max_age_weeks", 8)))
        score = float(np.exp(-age_weeks / max_age))
        freshness_values.append(score)
        if age_weeks <= max_age:
            available += 1
    count = max(1, len(indicator_settings))
    return float(np.mean(freshness_values)), available / count


def _movement(winners: np.ndarray, phases: list[Any], angles: pd.Series) -> dict[str, str]:
    if len(winners) < 2:
        return {"from_previous_week": "unavailable", "direction": "unavailable"}
    previous, current = int(winners[-2]), int(winners[-1])
    size = len(phases)
    if current == previous:
        movement = "maintained"
    elif current == (previous + 1) % size:
        movement = "advanced"
    elif current == (previous - 1) % size:
        movement = "retreated"
    else:
        movement = "jumped"
    angle_values = angles.dropna()
    delta = 0.0
    if len(angle_values) >= 2:
        delta = float((angle_values.iloc[-1] - angle_values.iloc[-2] + 180.0) % 360.0 - 180.0)
    if delta > 0.5:
        direction = f"toward_{phases[(current + 1) % size].code}"
    elif delta < -0.5:
        direction = f"toward_{phases[(current - 1) % size].code}"
    else:
        direction = "stable"
    return {"from_previous_week": movement, "direction": direction}


def _indicator_evidence(
    contributions: pd.DataFrame, timestamp: pd.Timestamp
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    latest = contributions.loc[:timestamp].tail(1)
    if latest.empty:
        return [], []
    row = latest.iloc[0].dropna().sort_values()
    conflicting = [
        {"indicator_id": str(key), "contribution": round(float(value), 4)}
        for key, value in row.head(3).items()
        if value < 0
    ]
    supporting = [
        {"indicator_id": str(key), "contribution": round(float(value), 4)}
        for key, value in row.tail(3).sort_values(ascending=False).items()
        if value > 0
    ]
    return supporting, conflicting


def run_pipeline(
    observations: pd.DataFrame,
    settings: Settings,
    as_of: str | pd.Timestamp,
    leading_signals: pd.DataFrame | None = None,
) -> PipelineRun:
    """현재까지 가용한 자료만 사용해 필터링된 국면 판정을 계산한다."""

    as_of_timestamp = pd.Timestamp(as_of).tz_localize(None).normalize()
    core_settings = settings.indicators["indicators"]
    validated = validate_observations(observations)
    available, availability_warnings = apply_availability_dates(validated, core_settings)
    available = available[available["release_date"] <= as_of_timestamp].copy()
    if available.empty:
        raise ValueError(f"{as_of_timestamp.date()}까지 공개된 핵심지표가 없습니다")
    model_config = settings.model
    transformed = transform_observations(
        available,
        core_settings,
        int(model_config["trend_span_weeks"]),
        int(model_config["standardization_min_periods"]),
    )
    events = weekly_event_matrix(transformed)
    composite_estimate = CompositeFactorModel(
        core_settings, settings.indicators["constraints"]
    ).fit_filter(events)
    dynamic_estimate = DynamicFactorModel(model_config["dynamic_factor"]).fit_filter(events)
    # v0.1의 공식 대표모델은 기여도를 설명할 수 있는 합성요인이다.
    # 동적요인의 상태 기울기는 비교모델에만 속하므로 대표 좌표에 섞지 않는다.
    representative_fallback = False
    coords = coordinates(
        composite_estimate.factor,
        int(model_config["momentum_weeks"]),
        int(model_config["standardization_min_periods"]),
    ).dropna()
    if coords.empty:
        slope = dynamic_estimate.metadata["slopes"]
        if not isinstance(slope, pd.Series):
            raise TypeError("동적요인 기울기 메타데이터가 Series가 아닙니다")
        coords = coordinates(
            dynamic_estimate.factor,
            int(model_config["momentum_weeks"]),
            int(model_config["standardization_min_periods"]),
            slope,
        ).dropna()
        representative_fallback = True
    if coords.empty:
        raise ValueError("학습기간이 짧아 경기좌표를 계산할 수 없습니다")

    phases = phase_definitions(settings.transitions["phases"])
    emissions = np.vstack(
        [
            emission_probabilities(
                angle,
                radius,
                phases,
                float(model_config["phase_emission_sigma_degrees"]),
                float(model_config["phase_origin_sigma_multiplier"]),
                float(model_config["phase_origin_scale"]),
                level,
                (
                    float(model_config["phase_origin_scale"])
                    if model_config.get("contraction_level_gate", False)
                    else None
                ),
            )
            for angle, radius, level in coords[["angle", "radius", "y"]].to_numpy(dtype=float)
        ]
    )
    matrix = transition_matrix(len(phases), settings.transitions["transition"])
    filtered = filter_probabilities(
        emissions,
        matrix,
        coords["radius"].to_numpy(dtype=float),
        (
            float(model_config["phase_origin_scale"])
            if model_config.get("low_radius_jump_constraint", False)
            else None
        ),
    )
    winners = filtered.argmax(axis=1)
    probability_columns = [f"p_{phase.code}" for phase in phases]
    history = coords.copy()
    history[probability_columns] = filtered
    history["phase_code"] = [phases[int(index)].code for index in winners]
    history["phase_label_ko"] = [phases[int(index)].label_ko for index in winners]
    history["broad_phase"] = [phases[int(index)].broad for index in winners]

    timestamp = history.index[-1]
    probabilities = filtered[-1]
    order = np.argsort(probabilities)[::-1]
    winner, second = int(order[0]), int(order[1])
    current = phases[winner]
    latest_dynamic = float(dynamic_estimate.factor.reindex([timestamp], method="ffill").iloc[0])
    latest_composite = float(composite_estimate.factor.reindex([timestamp], method="ffill").iloc[0])
    agreement = _agreement(latest_dynamic, latest_composite)
    recent = winners[-4:]
    persistence = float(np.mean(recent == winner))
    freshness, availability = _quality(available, as_of_timestamp, core_settings)
    data_score, quality = data_confidence(
        freshness,
        availability,
        None,
        agreement,
        model_config["confidence"],
    )
    broad_score = broad_confidence(probabilities, winner, phases)
    detail_score = detail_confidence(
        probabilities,
        winner,
        float(history.iloc[-1]["angle"]),
        float(history.iloc[-1]["radius"]),
        agreement,
        persistence,
        phases,
        model_config["confidence"],
    )
    supporting, conflicting = _indicator_evidence(composite_estimate.contributions, timestamp)
    minimum_availability = float(settings.indicators["constraints"]["minimum_availability"])
    status = "ok" if availability >= minimum_availability else "withheld"
    warnings = [PRELIMINARY_WARNING, *availability_warnings]
    if status == "withheld":
        warnings.append(
            f"핵심지표 확보율 {availability:.0%}가 최소 기준 "
            f"{minimum_availability:.0%}보다 낮아 판정 보류"
        )
    if representative_fallback:
        warnings.append("합성요인 제약을 충족할 핵심지표가 부족해 판정을 보류하고 비교좌표만 표시")
    phase_probabilities = [
        {"code": phase.code, "label_ko": phase.label_ko, "probability": float(probabilities[index])}
        for index, phase in enumerate(phases)
    ]
    result = ModelResult(
        as_of_date=as_of_timestamp.date().isoformat(),
        model_version=str(model_config["version"]),
        status=status,
        current_phase={
            "code": current.code,
            "label_ko": current.label_ko,
            "broad_phase": current.broad,
            "broad_label_ko": current.broad_label_ko,
        },
        movement=_movement(winners, phases, history["angle"]),
        confidence={"broad": broad_score, "detail": detail_score, "data": data_score},
        coordinates={
            "x_momentum": float(history.iloc[-1]["x"]),
            "y_level": float(history.iloc[-1]["y"]),
            "angle_degrees": float(history.iloc[-1]["angle"]),
            "radius": float(history.iloc[-1]["radius"]),
        },
        phase_probabilities=phase_probabilities,
        runner_up={
            "code": phases[second].code,
            "label_ko": phases[second].label_ko,
            "probability": float(probabilities[second]),
            "gap_percentage_points": float((probabilities[winner] - probabilities[second]) * 100.0),
        },
        supporting_indicators=supporting,
        conflicting_indicators=conflicting,
        data_quality=quality,
        forecast_13w=preliminary_leading_score(leading_signals),
        warnings=warnings,
        metadata={
            "probability_sum": float(probabilities.sum()),
            "composite_model": composite_estimate.metadata["model"],
            "dynamic_model": dynamic_estimate.metadata["model"],
            "representative_model": "CompositeFactorModel",
            "representative_fallback_used": representative_fallback,
            "uses_backward_smoothing": False,
            "renormalized_weeks": composite_estimate.metadata["renormalized_weeks"],
            "duplicate_pairs": composite_estimate.metadata["duplicate_pairs"],
            "transition_jump_from_previous": (
                cyclic_distance(int(winners[-2]), winner, len(phases))
                if len(winners) >= 2
                else None
            ),
            "revision_basis": "latest_revision",
            "release_date_basis": "actual_when_provided_else_configured_lag",
        },
    )
    return PipelineRun(
        result=result,
        history=history,
        events=events,
        composite=composite_estimate.factor,
        dynamic=dynamic_estimate.factor,
        contributions=composite_estimate.contributions,
    )
