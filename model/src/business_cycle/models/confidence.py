"""대국면·세부국면·데이터 신뢰도를 분리한다."""

from __future__ import annotations

from typing import Any

import numpy as np

from ..schemas import PhaseDefinition
from .phase import boundary_distance


def score_label(score: float) -> str:
    """0~100 점수를 사용자용 등급으로 바꾼다."""

    if score >= 80:
        return "높음"
    if score >= 60:
        return "보통"
    if score >= 40:
        return "낮음"
    return "매우 낮음"


def broad_confidence(
    probabilities: np.ndarray, winner: int, phases: list[PhaseDefinition]
) -> float:
    """대표 상태와 같은 대국면의 세 상태 확률 합을 0~100으로 반환한다."""

    broad = phases[winner].broad
    return float(
        sum(probabilities[i] for i, phase in enumerate(phases) if phase.broad == broad) * 100
    )


def detail_confidence(
    probabilities: np.ndarray,
    winner: int,
    angle: float,
    radius: float,
    agreement: float,
    persistence: float,
    phases: list[PhaseDefinition],
    config: dict[str, Any],
) -> float:
    """후보 격차·경계·원점·모델 일치·지속성을 조합한다."""

    ordered = np.sort(probabilities)[::-1]
    gap = float(ordered[0] - ordered[1])
    components = {
        "detail_probability": float(probabilities[winner]),
        "detail_gap": min(1.0, gap * 4.0),
        "detail_boundary": boundary_distance(angle, phases[winner]),
        "detail_radius": 1.0 - np.exp(-max(radius, 0.0)),
        "detail_agreement": max(0.0, min(1.0, agreement)),
        "detail_persistence": max(0.0, min(1.0, persistence)),
    }
    score = sum(float(config[key]) * value for key, value in components.items())
    return float(max(0.0, min(100.0, score * 100.0)))


def data_confidence(
    freshness: float,
    availability: float,
    revision_stability: float | None,
    model_agreement: float,
    config: dict[str, Any],
) -> tuple[float, dict[str, float | None]]:
    """근거 없는 수정 안정성 고득점을 피하고 보수적 기본값을 명시한다."""

    revision = (
        float(revision_stability)
        if revision_stability is not None
        else float(config["conservative_revision_default"])
    )
    score = 100.0 * (
        float(config["data_freshness"]) * freshness
        + float(config["data_availability"]) * availability
        + float(config["data_revision_stability"]) * revision
        + float(config["data_model_agreement"]) * model_agreement
    )
    return float(max(0.0, min(100.0, score))), {
        "freshness": freshness,
        "availability": availability,
        "revision_stability": revision_stability,
        "revision_assumption": revision if revision_stability is None else None,
        "model_agreement": model_agreement,
    }
