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
from .models.momentum import coordinate_details
from .models.phase import emission_probabilities, phase_definitions
from .models.severity import severity_details, systemic_override
from .models.transition import cyclic_distance, filter_probabilities, transition_matrix
from .preprocessing.frequency import combine_subfactor, weekly_event_matrix
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
    coordinate_audit: pd.DataFrame
    breadth_audit: pd.DataFrame


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
        (
            int(model_config["trend_span_weeks"])
            if "trend_horizon_years" not in model_config
            else None
        ),
        int(model_config["standardization_min_periods"]),
        trend_horizon_years=model_config.get("trend_horizon_years"),
        standardization_method=str(
            model_config.get("standardization_method", "expanding_mean_std")
        ),
        standardization_horizon_years=float(
            model_config.get("standardization_horizon_years", 10.0)
        ),
        standardization_min_history_years=model_config.get("standardization_min_history_years"),
        robust_clip=model_config.get("robust_clip"),
    )
    events = weekly_event_matrix(transformed)
    # 중복군을 하나의 부요인으로 합칠 수 있다. 자료 품질 점수는 원래 지표 기준으로
    # 계속 재야 하므로 합성 결과는 요인 계산에만 쓴다.
    factor_events, factor_settings = combine_subfactor(
        events, core_settings, model_config.get("claims_subfactor")
    )
    composite_estimate = CompositeFactorModel(
        factor_settings,
        settings.indicators["constraints"],
        model_config.get("maturity"),
        model_config.get("robust_clip"),
    ).fit_filter(factor_events)
    dynamic_estimate = DynamicFactorModel(model_config["dynamic_factor"]).fit_filter(events)
    # v0.1의 공식 대표모델은 기여도를 설명할 수 있는 합성요인이다.
    # 동적요인의 상태 기울기는 비교모델에만 속하므로 대표 좌표에 섞지 않는다.
    representative_fallback = False
    # 좌표 표준화는 지표 표준화와 별개의 축이다. 창 길이와 최소 이력을 따로 두는 이유는
    # 두 성숙 요구가 겹쳐 쌓이면 전체 성숙 요구가 15년으로 늘어나기 때문이다.
    coordinate_method = str(
        model_config.get("coordinate_standardization_method", "expanding_mean_std")
    )
    coordinate_window_years = float(
        model_config.get("coordinate_standardization_horizon_years", 10.0)
    )
    coordinate_minimum_years = model_config.get("coordinate_standardization_min_history_years")
    coordinate_full_years = float(
        model_config.get("coordinate_full_history_years", coordinate_window_years)
    )
    coordinate_minimum_history = (
        None if coordinate_minimum_years is None else float(coordinate_minimum_years)
    )
    coordinate_audit = coordinate_details(
        composite_estimate.factor,
        int(model_config["momentum_weeks"]),
        int(model_config["standardization_min_periods"]),
        method=coordinate_method,
        window_years=coordinate_window_years,
        minimum_history_years=coordinate_minimum_history,
    )
    coords = coordinate_audit[["x", "y", "angle", "radius"]].dropna()
    if coords.empty:
        slope = dynamic_estimate.metadata["slopes"]
        if not isinstance(slope, pd.Series):
            raise TypeError("동적요인 기울기 메타데이터가 Series가 아닙니다")
        coordinate_audit = coordinate_details(
            dynamic_estimate.factor,
            int(model_config["momentum_weeks"]),
            int(model_config["standardization_min_periods"]),
            slope,
            method=coordinate_method,
            window_years=coordinate_window_years,
            minimum_history_years=coordinate_minimum_history,
        )
        coords = coordinate_audit[["x", "y", "angle", "radius"]].dropna()
        representative_fallback = True
    if coords.empty:
        raise ValueError("학습기간이 짧아 경기좌표를 계산할 수 없습니다")

    # 현재 침체는 여러 독립 경제영역이 동시에 나빠졌다는 판정이어야 한다. 영역 폭을
    # 주 단위로 세어 관측확률 게이트에 넘긴다. 기여도는 그 주에 보유 중인 신호만
    # 쓰므로 미래 정보가 들어가지 않는다. 실업수당 두 계열은 같은 영역이라 한 번 센다.
    breadth_config = model_config.get("contraction_breadth_gate") or {}
    breadth_minimum = (
        float(breadth_config["minimum_domains"])
        if bool(breadth_config.get("enabled", False))
        else None
    )
    domain_of = {str(key): str(value["domain"]) for key, value in factor_settings.items()}
    contributions_by_domain = (
        composite_estimate.contributions.reindex(coords.index)
        .rename(columns=domain_of)
        .T.groupby(level=0)
        .sum()
        .T
    )
    negative_domains = (contributions_by_domain < 0).sum(axis=1).astype(float)

    phases = phase_definitions(settings.transitions["phases"])
    matrix = transition_matrix(len(phases), settings.transitions["transition"])
    level_scale = (
        float(model_config.get("contraction_level_scale", model_config["phase_origin_scale"]))
        if model_config.get("contraction_level_gate", False)
        else None
    )
    jump_scale = (
        float(model_config.get("low_radius_jump_scale", model_config["phase_origin_scale"]))
        if model_config.get("low_radius_jump_constraint", False)
        else None
    )
    geometry = coords[["angle", "radius", "y"]].to_numpy(dtype=float)

    def _emissions(breadth_values: np.ndarray | None) -> np.ndarray:
        """폭 계열을 바꿔 가며 같은 관측확률 계산을 재사용한다."""

        column = (
            np.full(len(geometry), np.nan)
            if breadth_values is None
            else np.asarray(breadth_values, dtype=float)
        )
        return np.vstack(
            [
                emission_probabilities(
                    float(geometry[position][0]),
                    float(geometry[position][1]),
                    phases,
                    float(model_config["phase_emission_sigma_degrees"]),
                    float(model_config["phase_origin_sigma_multiplier"]),
                    float(model_config["phase_origin_scale"]),
                    float(geometry[position][2]),
                    level_scale,
                    None if breadth_values is None else float(column[position]),
                    None if breadth_values is None else breadth_minimum,
                )
                for position in range(len(geometry))
            ]
        )

    def _filter(emission_matrix: np.ndarray) -> np.ndarray:
        return filter_probabilities(
            emission_matrix,
            matrix,
            coords["radius"].to_numpy(dtype=float),
            jump_scale,
        )

    contraction_indexes = [
        index for index, phase in enumerate(phases) if phase.broad == "contraction"
    ]
    # 체계적 충격 예외. 폭이 한 단계 모자란 주에서만, 청구건수를 빼고도 심각도가
    # 개발구간 밖이고 한 항목을 빼도 남아 있을 때에 한해 침체 판정을 허용한다.
    # 임계값은 1995~2012 개발구간에서만 정했고 특정 사건의 날짜·분기·상수가 없다.
    override_config = model_config.get("systemic_shock_override") or {}
    override_enabled = breadth_minimum is not None and bool(override_config.get("enabled", False))
    raw_weights = composite_estimate.metadata["effective_weights"]
    weights_by_week = (
        {
            pd.Timestamp(str(timestamp)): {str(k): float(v) for k, v in values.items()}
            for timestamp, values in raw_weights.items()
            if isinstance(values, dict)
        }
        if isinstance(raw_weights, dict)
        else {}
    )
    severity = severity_details(
        composite_estimate.contributions,
        weights_by_week,
        domain_of,
        coordinate_audit["coordinate_scale"],
        coords.index,
    )
    override_active = pd.Series(False, index=coords.index)
    ungated_contraction = pd.Series(np.nan, index=coords.index)
    if override_enabled and breadth_minimum is not None:
        ungated = _filter(_emissions(None))
        ungated_contraction = pd.Series(
            ungated[:, contraction_indexes].sum(axis=1), index=coords.index
        )
        override_active = systemic_override(
            severity,
            negative_domains,
            ungated_contraction,
            dynamic_estimate.factor.reindex(coords.index, method="ffill"),
            breadth_minimum,
            override_config,
        )
    effective_breadth = negative_domains.where(~override_active, breadth_minimum)
    emissions = _emissions(None if breadth_minimum is None else effective_breadth.to_numpy())
    filtered = _filter(emissions)
    breadth_audit = severity.copy()
    breadth_audit["negative_domains"] = negative_domains
    breadth_audit["effective_breadth"] = effective_breadth
    breadth_audit["ungated_contraction_probability"] = ungated_contraction
    breadth_audit["systemic_override_active"] = override_active
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
    first_available = min(pd.Timestamp(value) for value in events.attrs["first_available"].values())
    history_years = (as_of_timestamp - first_available).days / 365.2425
    # 성숙 시계는 둘이다. 원자료 이력과, 지표 표준화가 끝난 뒤에야 시작하는 합성요인
    # 이력이다. 두 번째 시계를 세지 않으면 짧고 조용한 표본으로 계산한 척도가 공식
    # 판정처럼 나간다. 다만 두 요구를 이어 붙이면 전체 성숙 요구가 15년이 되므로
    # 좌표 창은 자료 성숙 안에서 끝나도록 짧게 잡는다.
    factor_history = composite_estimate.factor.dropna()
    coordinate_history_years = (
        (as_of_timestamp - pd.Timestamp(factor_history.index.min())).days / 365.2425
        if not factor_history.empty
        else 0.0
    )
    coordinate_mature = coordinate_history_years >= coordinate_full_years
    # 보류(withheld)가 잠정(preliminary)보다 강한 상태다. 자료가 모자라 판정을 내지 않기로
    # 한 경우를 "잠정 판정"으로 낮춰 표시하면 없는 판정이 있는 것처럼 읽힌다.
    if history_years < 5.0:
        status, status_reason = "withheld", f"원자료 이력 {history_years:.1f}년 < 5년"
    elif availability < minimum_availability:
        status, status_reason = (
            "withheld",
            f"핵심지표 확보율 {availability:.0%} < 최소 {minimum_availability:.0%}",
        )
    elif representative_fallback:
        status, status_reason = "withheld", "합성요인을 만들 핵심지표가 부족"
    elif history_years < 10.0:
        status, status_reason = "preliminary", f"원자료 이력 {history_years:.1f}년 < 10년"
    elif not coordinate_mature:
        status, status_reason = (
            "preliminary",
            f"합성요인 이력 {coordinate_history_years:.1f}년 < {coordinate_full_years:.0f}년",
        )
    else:
        status, status_reason = "official", ""
    warnings = [PRELIMINARY_WARNING, *availability_warnings]
    if status == "withheld":
        warnings.append(f"판정 보류: {status_reason}")
    elif status == "preliminary":
        warnings.append(f"잠정 판정: {status_reason}")
    if representative_fallback:
        warnings.append("합성요인 제약을 충족할 핵심지표가 부족해 비교좌표만 표시")
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
            "warmup_years": history_years,
            "warmup_status": "mature" if status == "official" else status,
            "trend_horizon_years": model_config.get("trend_horizon_years"),
            "trend_span_weeks": model_config.get("trend_span_weeks"),
            "standardization_method": str(
                model_config.get("standardization_method", "expanding_mean_std")
            ),
            "standardization_horizon_years": model_config.get("standardization_horizon_years"),
            "contraction_breadth_minimum": breadth_minimum,
            "systemic_override_enabled": override_enabled,
            "systemic_override_active": bool(override_active.iloc[-1]),
            "systemic_override_weeks": int(override_active.sum()),
            "coordinate_standardization_method": coordinate_method,
            "coordinate_standardization_window_years": coordinate_window_years,
            "coordinate_history_years": coordinate_history_years,
            "coordinate_full_history_years": coordinate_full_years,
            "coordinate_mature": coordinate_mature,
            "status_reason": status_reason,
            "maturity_enabled": bool((model_config.get("maturity") or {}).get("enabled", False)),
            "robust_clip": model_config.get("robust_clip"),
            "clipped_observation_count": composite_estimate.metadata["clipped_observation_count"],
            "total_limited_amount": composite_estimate.metadata["total_limited_amount"],
        },
    )
    return PipelineRun(
        result=result,
        history=history,
        events=events,
        composite=composite_estimate.factor,
        dynamic=dynamic_estimate.factor,
        contributions=composite_estimate.contributions,
        coordinate_audit=coordinate_audit,
        breadth_audit=breadth_audit,
    )
