"""각도 기반 12개 국면 정의와 부드러운 관측확률."""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from ..schemas import PhaseDefinition


def phase_definitions(config: list[dict[str, Any]]) -> list[PhaseDefinition]:
    """설정 사전을 국면 정의로 변환한다."""

    return [PhaseDefinition(**item) for item in config]


def map_angle(angle: float, phases: list[PhaseDefinition]) -> PhaseDefinition:
    """0°와 360°를 같은 점으로 처리해 초기 각도 국면을 반환한다."""

    normalized = angle % 360.0
    for phase in phases:
        if phase.start <= normalized < phase.end:
            return phase
    raise ValueError(f"각도 {angle}에 대응하는 국면 설정이 없습니다")


def circular_distance(left: float, right: float) -> float:
    """두 각도의 0~180도 최단 거리를 반환한다."""

    return abs((left - right + 180.0) % 360.0 - 180.0)


def boundary_distance(angle: float, phase: PhaseDefinition) -> float:
    """현재 구간의 가까운 경계까지 거리를 0~1로 정규화한다."""

    width = (phase.end - phase.start) % 360.0 or 360.0
    position = (angle % 360.0 - phase.start) % 360.0
    if position > width:
        return 0.0
    return min(position, width - position) / (width / 2.0)


def emission_probabilities(
    angle: float,
    radius: float,
    phases: list[PhaseDefinition],
    base_sigma: float,
    origin_multiplier: float,
    origin_scale: float,
) -> np.ndarray:
    """경계에서 끊기지 않고 원점에서 넓어지는 원형 Gaussian 확률을 계산한다."""

    closeness = math.exp(-max(radius, 0.0) / max(origin_scale, 1e-9))
    sigma = base_sigma * (1.0 + origin_multiplier * closeness)
    centers = [((phase.start + phase.end) / 2.0) % 360.0 for phase in phases]
    likelihood = np.array(
        [math.exp(-0.5 * (circular_distance(angle, center) / sigma) ** 2) for center in centers],
        dtype=float,
    )
    total = float(likelihood.sum())
    return likelihood / total if total > 0 else np.full(len(phases), 1.0 / len(phases))
