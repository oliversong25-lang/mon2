"""거리 인식 유계 소프트 필터. 인접 이동과 4단계 점프를 구분한다.

후보 I의 안정화는 "직전과 같은가"만 봤다. 그래서 인접한 한 칸 이동과 순환의 반대편으로
건너뛰는 이동에 같은 여유를 줬고, 3단계 이상 점프가 93건 나왔다.

여기서는 전이행렬을 쓴다. 가중치가 순환 거리에 따라 지수적으로 줄지만 **모든 성분이
0보다 크다**. 그래서

* 먼 국면으로 가는 길이 닫히지 않는다(후보 H를 133주 가둔 것이 정확히 0이었다),
* 인접 이동이 선호되므로 큰 점프에는 더 많은 증거가 필요하고,
* 행렬이 에르고딕이라 먼 과거의 영향이 기하급수적으로 사라진다.

대국면과 하위국면에 각각 하나씩 두고, 모수는 셋뿐이다 — lambda_major, lambda_subphase,
epsilon. 국면마다 따로 두지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from .hierarchy import MAJORS, SUBPHASES


def cycle_distance(left: int, right: int, size: int) -> int:
    """순환 거리. recovery → expansion → slowdown → contraction → recovery."""

    direct = abs(left - right)
    return min(direct, size - direct)


def transition_matrix(
    size: int, lambda_value: float, epsilon: float, cyclic: bool = True
) -> np.ndarray:
    """거리로 감쇠하되 모든 성분이 양수인 행렬. 행 합은 1이다."""

    if epsilon <= 0:
        raise ValueError("epsilon은 0보다 커야 합니다 — 0이면 그 상태로 가는 길이 막힌다")
    if lambda_value < 0:
        raise ValueError("lambda는 음수일 수 없습니다")
    matrix = np.zeros((size, size), dtype=float)
    for i in range(size):
        for j in range(size):
            distance = cycle_distance(i, j, size) if cyclic else abs(i - j)
            matrix[i, j] = epsilon + float(np.exp(-lambda_value * distance))
    normalised: np.ndarray = matrix / matrix.sum(axis=1, keepdims=True)
    return normalised


def is_ergodic(matrix: np.ndarray) -> bool:
    """모든 성분이 양수면 기약·비주기적이다. 모든 상태가 서로 도달 가능하다."""

    return bool((matrix > 0).all())


@dataclass(frozen=True)
class FilterResult:
    filtered: pd.DataFrame
    official: pd.Series
    raw: pd.Series
    changed: pd.Series


def forward_filter(scores: pd.DataFrame, matrix: np.ndarray) -> pd.DataFrame:
    """순방향 필터. 미래 관측을 쓰지 않는다."""

    values = scores.to_numpy(dtype=float)
    size = matrix.shape[0]
    prior = np.full(size, 1.0 / size)
    out = np.zeros_like(values)
    for position, likelihood in enumerate(values):
        prediction = prior @ matrix
        posterior = prediction * likelihood
        total = float(posterior.sum())
        posterior = posterior / total if total > 0 else prediction
        out[position] = posterior
        prior = posterior
    return pd.DataFrame(out, index=scores.index, columns=scores.columns)


def filter_scores(scores: pd.DataFrame, lambda_value: float, epsilon: float) -> FilterResult:
    matrix = transition_matrix(scores.shape[1], lambda_value, epsilon)
    filtered = forward_filter(scores, matrix)
    official = filtered.idxmax(axis=1).astype(str)
    raw = scores.idxmax(axis=1).astype(str)
    return FilterResult(filtered, official, raw, official.ne(raw))


def convergence(
    scores: pd.DataFrame, lambda_value: float, epsilon: float, windows: tuple[int, ...]
) -> dict[str, Any]:
    """먼 과거가 다른 경로들이 같은 최근 증거에서 만나는지. 에르고딕성의 실측이다."""

    matrix = transition_matrix(scores.shape[1], lambda_value, epsilon)
    size = matrix.shape[0]
    results: dict[str, Any] = {}
    for window in windows:
        tail = scores.tail(window).to_numpy(dtype=float)
        finals: set[str] = set()
        for start in range(size):
            prior = np.full(size, 1e-9)
            prior[start] = 1.0
            prior = prior / prior.sum()
            for likelihood in tail:
                posterior = (prior @ matrix) * likelihood
                total = float(posterior.sum())
                prior = posterior / total if total > 0 else prior @ matrix
            finals.add(str(scores.columns[int(np.argmax(prior))]))
        results[f"after_{window}_weeks"] = {
            "distinct_final_states": len(finals),
            "converged": len(finals) == 1,
            "final_states": sorted(finals),
        }
    return results


def hierarchical_official(
    major_scores: pd.DataFrame,
    subphase_scores: dict[str, pd.DataFrame],
    lambda_major: float,
    lambda_subphase: float,
    epsilon: float,
) -> dict[str, Any]:
    """대국면을 먼저 필터링하고, 고른 대국면 안에서 하위국면을 필터링한다.

    대국면이 바뀌면 새 대국면의 하위국면 상태는 **그 주의 관측 증거로 초기화**한다.
    무조건 `early`로 되돌리지 않는다 — 그러면 국면이 바뀔 때마다 없는 정보를 만든다.
    """

    major = filter_scores(major_scores[list(MAJORS)], lambda_major, epsilon)
    sub_matrix = transition_matrix(len(SUBPHASES), lambda_subphase, epsilon, cyclic=False)
    index = major_scores.index
    official_sub: list[str] = []
    raw_sub: list[str] = []
    prior: np.ndarray | None = None
    previous_major: str | None = None
    for position, week in enumerate(index):
        current_major = str(major.official.iloc[position])
        likelihood = subphase_scores[current_major].loc[week].to_numpy(dtype=float)
        if prior is None or current_major != previous_major:
            posterior = likelihood / float(likelihood.sum())
        else:
            prediction = prior @ sub_matrix
            posterior = prediction * likelihood
            total = float(posterior.sum())
            posterior = posterior / total if total > 0 else prediction
        official_sub.append(str(SUBPHASES[int(np.argmax(posterior))]))
        raw_sub.append(str(SUBPHASES[int(np.argmax(likelihood))]))
        prior = posterior
        previous_major = current_major
    official_subphase = pd.Series(official_sub, index=index)
    return {
        "raw_major_phase": major.raw,
        "official_major_phase": major.official,
        "raw_subphase": pd.Series(raw_sub, index=index),
        "official_subphase": official_subphase,
        "official_current_phase": major.official.str.cat(official_subphase, sep="_"),
        "major_filtered": major.filtered,
    }
