"""순환 전이행렬과 실시간 forward filtering."""

from __future__ import annotations

from typing import Any

import numpy as np


def transition_matrix(size: int, config: dict[str, Any]) -> np.ndarray:
    """유지·다음·이전 이동을 우대하고 나머지 점프에는 작은 질량을 둔다."""

    if size < 4:
        raise ValueError("순환 전이에는 최소 4개 상태가 필요합니다")
    stay = float(config["stay"])
    forward = float(config["next"])
    backward = float(config["previous"])
    jump_mass = float(config["jump_mass"])
    if abs(stay + forward + backward + jump_mass - 1.0) > 1e-9:
        raise ValueError("전이확률 설정 합은 1이어야 합니다")
    matrix = np.full((size, size), jump_mass / (size - 3), dtype=float)
    for state in range(size):
        matrix[state, state] = stay
        matrix[state, (state + 1) % size] = forward
        matrix[state, (state - 1) % size] = backward
    return matrix


def filter_probabilities(emissions: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    """미래 관측 없이 순방향 상태확률을 계산한다."""

    if emissions.ndim != 2 or emissions.shape[1] != matrix.shape[0]:
        raise ValueError("관측확률과 전이행렬 차원이 다릅니다")
    filtered = np.zeros_like(emissions, dtype=float)
    prior = np.full(matrix.shape[0], 1.0 / matrix.shape[0])
    for index, likelihood in enumerate(emissions):
        prediction = prior @ matrix
        posterior = prediction * likelihood
        total = float(posterior.sum())
        posterior = posterior / total if total > 0 else prediction / prediction.sum()
        filtered[index] = posterior
        prior = posterior
    return filtered


def cyclic_distance(left: int, right: int, size: int) -> int:
    """상태 번호 사이의 최소 순환 거리를 반환한다."""

    direct = abs(left - right)
    return min(direct, size - direct)
