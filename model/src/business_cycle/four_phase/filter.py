"""4상태 소프트 필터. 모수는 lambda 하나와 epsilon 하나뿐이다.

순환은 recovery → expansion → slowdown → contraction → recovery 다. 전이 가중치가
순환 거리로 감쇠하되 **모든 성분이 0보다 크다**. 0을 만들면 그 상태로 가는 길이 닫히고,
후보 H를 133주 가둔 것이 정확히 그 구조였다.

행렬이 모든 성분에서 양수이면 기약·비주기적이므로, 먼 과거의 영향이 기하급수적으로
사라지고 서로 다른 이력이 같은 최근 증거에서 만난다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from .evidence import PHASES


def cycle_distance(left: int, right: int, size: int = 4) -> int:
    direct = abs(left - right)
    return min(direct, size - direct)


def transition_matrix(lam: float, epsilon: float, size: int = 4) -> np.ndarray:
    """거리로 감쇠하되 모든 성분이 양수인 행렬. 행 합은 1이다."""

    if epsilon <= 0:
        raise ValueError("epsilon은 0보다 커야 합니다 — 0이면 그 상태로 가는 길이 막힌다")
    if lam < 0:
        raise ValueError("lambda는 음수일 수 없습니다")
    matrix = np.array(
        [
            [epsilon + float(np.exp(-lam * cycle_distance(i, j, size))) for j in range(size)]
            for i in range(size)
        ]
    )
    normalised: np.ndarray = matrix / matrix.sum(axis=1, keepdims=True)
    return normalised


def is_ergodic(matrix: np.ndarray) -> bool:
    """모든 성분이 양수면 모든 상태가 서로 도달 가능하고 주기가 없다."""

    return bool((matrix > 0).all())


@dataclass(frozen=True)
class FourStateFilter:
    filtered: pd.DataFrame
    official: pd.Series
    raw: pd.Series
    changed: pd.Series
    matrix: np.ndarray
    filtered_winner: pd.Series
    confirmation_pending: pd.Series


def confirm_transitions(
    filtered: pd.DataFrame,
    scores: pd.DataFrame,
    quality_high: pd.Series,
    confirmation_weeks: int,
    immediate_margin: float,
) -> tuple[pd.Series, pd.Series]:
    """전역 단일 확인 규칙. 국면마다 따로 두지 않는다.

    후보 J 감사에서 3주 왕복 10건 중 **7건이 `expansion|slowdown` 경계**였고, 그중 4건이
    2006년 11~12월 한 덩어리였다. 10건 중 9건의 원시 점수 마진이 0.072 이하였다.
    즉 왕복은 증거가 실제로 뒤집혀서가 아니라 거의 동점인 두 국면 사이에서 매주
    승자가 바뀌어 생겼다.

    그래서 두 갈래만 둔다.

    * 새 국면의 증거 품질이 높고 원시 점수 우위가 충분히 크면 **즉시** 전환한다.
    * 그렇지 않으면 새 국면이 개발구간에서 정한 짧은 기간 동안 **계속** 승자여야 한다.

    이 규칙은 유한 기억이다. 상태는 (현재 국면, 도전자, 연속 주 수)뿐이고 연속 주 수는
    확인 기간에서 잘린다. 어떤 국면도 흡수하지 않는다 — 도전자가 확인 기간만 버티면
    증거가 아무리 약해도 반드시 이긴다. 후보 H를 133주 가둔 구조는 도전자가 **영원히**
    이길 수 없게 만든 것이었고, 여기에는 그런 경로가 없다.
    """

    if confirmation_weeks < 1:
        raise ValueError("확인 기간은 최소 1주여야 합니다")
    if not 0.0 <= immediate_margin <= 1.0:
        raise ValueError("즉시 전환 마진은 0과 1 사이여야 합니다")

    winner = filtered[list(PHASES)].idxmax(axis=1).astype(str)
    ordered = np.sort(scores[list(PHASES)].to_numpy(dtype=float), axis=1)
    margin = ordered[:, -1] - ordered[:, -2]

    official: list[str] = []
    pending: list[int] = []
    current = str(winner.iloc[0])
    challenger = ""
    streak = 0
    for position, week in enumerate(winner.index):
        candidate = str(winner.loc[week])
        if candidate == current:
            challenger, streak = "", 0
        else:
            if candidate == challenger:
                streak += 1
            else:
                challenger, streak = candidate, 1
            immediate = bool(quality_high.loc[week]) and margin[position] >= immediate_margin
            if immediate or streak >= confirmation_weeks:
                current = candidate
                challenger, streak = "", 0
        official.append(current)
        pending.append(streak)
    return (
        pd.Series(official, index=winner.index, name="official_phase"),
        pd.Series(pending, index=winner.index, name="confirmation_pending"),
    )


def filter_scores(
    scores: pd.DataFrame,
    lam: float,
    epsilon: float,
    quality_high: pd.Series | None = None,
    confirmation_weeks: int = 1,
    immediate_margin: float = 1.0,
) -> FourStateFilter:
    """순방향 필터와 확인 규칙. 미래 관측을 쓰지 않는다."""

    matrix = transition_matrix(lam, epsilon)
    values = scores[list(PHASES)].to_numpy(dtype=float)
    prior = np.full(len(PHASES), 1.0 / len(PHASES))
    out = np.zeros_like(values)
    for position, likelihood in enumerate(values):
        prediction = prior @ matrix
        posterior = prediction * likelihood
        total = float(posterior.sum())
        posterior = posterior / total if total > 0 else prediction
        out[position] = posterior
        prior = posterior
    filtered = pd.DataFrame(out, index=scores.index, columns=list(PHASES))
    winner = filtered.idxmax(axis=1).astype(str)
    raw = scores[list(PHASES)].idxmax(axis=1).astype(str)
    if quality_high is None:
        official = winner
        pending = pd.Series(0, index=winner.index, name="confirmation_pending")
    else:
        official, pending = confirm_transitions(
            filtered, scores, quality_high, confirmation_weeks, immediate_margin
        )
    return FourStateFilter(filtered, official, raw, official.ne(raw), matrix, winner, pending)


def convergence(
    scores: pd.DataFrame, lam: float, epsilon: float, windows: tuple[int, ...]
) -> dict[str, Any]:
    """먼 과거가 다른 경로들이 같은 최근 증거에서 만나는지. 유한 기억의 실측이다."""

    matrix = transition_matrix(lam, epsilon)
    results: dict[str, Any] = {}
    for window in windows:
        tail = scores[list(PHASES)].tail(window).to_numpy(dtype=float)
        finals: set[str] = set()
        for start in range(len(PHASES)):
            prior = np.full(len(PHASES), 1e-9)
            prior[start] = 1.0
            prior = prior / prior.sum()
            for likelihood in tail:
                posterior = (prior @ matrix) * likelihood
                total = float(posterior.sum())
                prior = posterior / total if total > 0 else prior @ matrix
            finals.add(PHASES[int(np.argmax(prior))])
        results[f"after_{window}_weeks"] = {
            "distinct_final_states": len(finals),
            "converged": len(finals) == 1,
            "final_states": sorted(finals),
        }
    return results


def amplification(scores: pd.DataFrame, result: FourStateFilter) -> pd.Series:
    """필터가 만든 확률 증폭. 유계인지 확인할 수 있어야 한다."""

    gains = [
        float(str(result.filtered.at[week, name])) - float(str(scores.at[week, name]))
        for week, name in result.official.items()
    ]
    return pd.Series(gains, index=scores.index, name="filter_gain")
