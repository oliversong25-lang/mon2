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
    level: float | None = None,
    contraction_level_scale: float | None = None,
) -> np.ndarray:
    """경계에서 끊기지 않고 원점에서 넓어지는 원형 Gaussian 확률을 계산한다.

    선택적인 침체 수준 게이트는 Y가 약한 음수일 뿐인 원점 부근에서 각도만으로
    현재 침체를 확정하지 않게 한다. 0/1 임계값 대신 기존 원점 척도까지 연속적으로
    근거를 늘리므로 강한 실제 침체의 관측확률은 바꾸지 않는다.
    """

    closeness = math.exp(-max(radius, 0.0) / max(origin_scale, 1e-9))
    sigma = base_sigma * (1.0 + origin_multiplier * closeness)
    centers = [((phase.start + phase.end) / 2.0) % 360.0 for phase in phases]
    likelihood = np.array(
        [math.exp(-0.5 * (circular_distance(angle, center) / sigma) ** 2) for center in centers],
        dtype=float,
    )
    if level is not None and contraction_level_scale is not None:
        scale = max(float(contraction_level_scale), 1e-9)
        evidence = min(1.0, max(0.0, -float(level) / scale))
        contraction_indexes = [
            index for index, phase in enumerate(phases) if phase.broad == "contraction"
        ]
        removed = 0.0
        for index, phase in enumerate(phases):
            if phase.broad == "contraction":
                removed += float(likelihood[index] * (1.0 - evidence))
                likelihood[index] *= evidence
        if removed > 0 and contraction_indexes:
            before = (contraction_indexes[0] - 1) % len(phases)
            after = (contraction_indexes[-1] + 1) % len(phases)
            before_distance = circular_distance(angle, centers[before])
            after_distance = circular_distance(angle, centers[after])
            distance_sum = before_distance + after_distance
            before_share = 0.5 if distance_sum <= 1e-12 else after_distance / distance_sum
            likelihood[before] += removed * before_share
            likelihood[after] += removed * (1.0 - before_share)
    total = float(likelihood.sum())
    return likelihood / total if total > 0 else np.full(len(phases), 1.0 / len(phases))
