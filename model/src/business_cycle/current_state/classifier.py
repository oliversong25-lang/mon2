"""현재상태 분류기. 각도가 아니라 현재 수준·모멘텀·폭으로 국면을 정한다.

후보 H는 가중합 좌표의 **각도**로 국면을 정했다. 그래서 반지름이 작아 각도가 의미를
잃은 주에도 국면이 나왔고, 한 도메인이 좌표를 끌고 갈 수 있었다. 여기서는 그 두 성질을
모두 없앤다.

* 총량은 도메인 **중앙값**이다. 한 도메인이 아무리 극단이어도 결과를 끌고 가지 못한다.
* 대국면은 (수준 부호, 모멘텀 부호)의 네 조합이다. 회전이나 각도가 없다.
* 침체는 **동행 도메인**의 폭을 요구한다. 노동시장 스트레스는 동행 도메인이 아니므로
  단독으로 침체를 선언할 수 없다.
* 하위국면은 각 대국면 안에서 **현재 심각도**의 단조 함수다. 각도 구역이 아니다.

12개 국면 점수는 합이 1인 분포이며 **어느 것도 정확히 0이 되지 않는다**. 0을 만들면
그 국면으로 가는 길이 막히고, 그것이 후보 H를 133주 가둔 구조였다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Final

import numpy as np
import pandas as pd

MAJORS: Final[tuple[str, ...]] = ("recovery", "expansion", "slowdown", "contraction")
SUBPHASES: Final[tuple[str, ...]] = ("early", "middle", "late")

#: 12개 국면의 표준 순서. 해석층 계약과 같은 표기를 쓴다.
PHASES: Final[tuple[str, ...]] = tuple(
    f"{major}_{sub}"
    for major in ("recovery", "expansion", "slowdown", "contraction")
    for sub in SUBPHASES
)

#: 어떤 국면도 정확히 0이 되지 않게 하는 하한. 도달 불가능한 상태를 만들지 않는다.
SCORE_FLOOR: Final[float] = 1e-6


def _sigmoid(value: float) -> float:
    if value > 40:
        return 1.0
    if value < -40:
        return 0.0
    return 1.0 / (1.0 + math.exp(-value))


@dataclass(frozen=True)
class StateThresholds:
    """개발구간(1995~2012)에서 도출해 잠근 값. 검증 결과로 바꾸지 않는다."""

    neutral_level: float
    neutral_momentum: float
    contraction_level: float
    contraction_level_domains: int
    contraction_momentum_domains: int
    concentration_flag: float
    progression_cuts: dict[str, tuple[float, float]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "neutral_level": self.neutral_level,
            "neutral_momentum": self.neutral_momentum,
            "contraction_level": self.contraction_level,
            "contraction_level_domains": self.contraction_level_domains,
            "contraction_momentum_domains": self.contraction_momentum_domains,
            "concentration_flag": self.concentration_flag,
            "progression_cuts": {k: list(v) for k, v in self.progression_cuts.items()},
        }


def progression_score(
    major: str, activity_level: float, activity_momentum: float, negative_level_domains: int
) -> float:
    """대국면 안에서 '얼마나 진행됐나'. 각 대국면마다 경제적으로 정의한다.

    recovery   회복 정도 — 수준이 정상에 가까울수록 늦은 단계
    expansion  성숙 정도 — 모멘텀이 약할수록 늦은 단계
    slowdown   침체 근접도 — 수준이 낮고 모멘텀이 나쁘고 폭이 넓을수록 늦은 단계
    contraction 침체 깊이 — 수준이 깊을수록 늦은 단계
    """

    if major == "recovery":
        return activity_level
    if major == "expansion":
        return -activity_momentum
    if major == "slowdown":
        return -activity_level - activity_momentum + 0.5 * float(negative_level_domains)
    if major == "contraction":
        return -activity_level
    raise ValueError(f"알 수 없는 대국면: {major}")


def breadth_factor(
    negative_level_domains: int,
    negative_momentum_domains: int,
    thresholds: StateThresholds,
) -> float:
    """침체 점수를 폭으로 눌러 준다. 계단이 아니라 한 도메인 폭으로 연속이다.

    두 조건 모두 필요하다 — 수준도 넓게 낮고, 모멘텀도 넓게 나빠야 한다. 둘 중 약한
    쪽이 곧 폭이다. 동행 도메인만 세므로 노동시장 스트레스가 단독으로 침체를 만들 수 없다.
    """

    level_evidence = min(
        1.0, max(0.0, float(negative_level_domains) - (thresholds.contraction_level_domains - 1))
    )
    momentum_evidence = min(
        1.0,
        max(0.0, float(negative_momentum_domains) - (thresholds.contraction_momentum_domains - 1)),
    )
    return min(level_evidence, momentum_evidence)


def major_scores(
    activity_level: float,
    activity_momentum: float,
    negative_level_domains: int,
    negative_momentum_domains: int,
    thresholds: StateThresholds,
) -> dict[str, float]:
    """네 대국면 점수. 합이 1이고 어느 것도 0이 아니다.

    수준·모멘텀 부호의 부드러운 조합이다. 중립대 폭이 곧 부드러움의 척도이므로,
    총량이 중립대 안에 있으면 네 국면이 서로 비슷해지고 분리도가 낮게 나온다 —
    약한 증거가 강한 확신으로 바뀌지 않는다.
    """

    level_width = max(thresholds.neutral_level, 1e-9)
    momentum_width = max(thresholds.neutral_momentum, 1e-9)
    strong = _sigmoid(activity_level / level_width)
    rising = _sigmoid(activity_momentum / momentum_width)
    scores = {
        "expansion": strong * rising,
        "slowdown": strong * (1.0 - rising),
        "recovery": (1.0 - strong) * rising,
        "contraction": (1.0 - strong) * (1.0 - rising),
    }
    # 침체는 폭과 수준을 추가로 요구한다. 깎인 몫은 후퇴기로 넘긴다 — 침체가 아니면
    # "아직 광범위한 침체는 아니지만 약해지는 중"이 남는 설명이다.
    depth = min(1.0, max(0.0, -activity_level / max(abs(thresholds.contraction_level), 1e-9)))
    allowed = min(
        breadth_factor(negative_level_domains, negative_momentum_domains, thresholds), depth
    )
    removed = scores["contraction"] * (1.0 - allowed)
    scores["contraction"] -= removed
    scores["slowdown"] += removed
    return scores


def phase_scores(
    activity_level: float,
    activity_momentum: float,
    negative_level_domains: int,
    negative_momentum_domains: int,
    thresholds: StateThresholds,
) -> dict[str, float]:
    """12개 국면 점수. 대국면 점수를 진행도에 따라 세 하위국면으로 나눈다."""

    majors = major_scores(
        activity_level,
        activity_momentum,
        negative_level_domains,
        negative_momentum_domains,
        thresholds,
    )
    scores: dict[str, float] = {}
    for major, weight in majors.items():
        value = progression_score(major, activity_level, activity_momentum, negative_level_domains)
        low, high = thresholds.progression_cuts[major]
        width = max((high - low) / 2.0, 1e-6)
        early = 1.0 - _sigmoid((value - low) / width)
        late = _sigmoid((value - high) / width)
        middle = max(0.0, 1.0 - early - late)
        total = early + middle + late
        for name, share in zip(SUBPHASES, (early, middle, late), strict=True):
            scores[f"{major}_{name}"] = weight * (share / total if total > 0 else 1.0 / 3.0)
    floored = {name: max(SCORE_FLOOR, scores.get(name, 0.0)) for name in PHASES}
    total = sum(floored.values())
    return {name: value / total for name, value in floored.items()}


def raw_phase(scores: dict[str, float]) -> str:
    """안정화 이전의 현재상태 국면. 지금 증거만으로 고른다."""

    return max(scores, key=lambda name: scores[name])


def separation(scores: dict[str, float]) -> float:
    """1·2순위 점수 차이. 안정화 보너스를 넣지 않은 값이라 증거의 세기를 그대로 잰다."""

    ordered = sorted(scores.values(), reverse=True)
    return float(ordered[0] - ordered[1])


def classify_frame(
    activity_level: pd.Series,
    activity_momentum: pd.Series,
    negative_level_domains: pd.Series,
    negative_momentum_domains: pd.Series,
    thresholds: StateThresholds,
) -> pd.DataFrame:
    """주 단위 점수 행렬. 각 행의 합은 1이다."""

    index = activity_level.index
    rows: list[dict[str, float]] = []
    for week in index:
        level = float(activity_level.loc[week])
        momentum = float(activity_momentum.loc[week])
        if not (np.isfinite(level) and np.isfinite(momentum)):
            rows.append({name: 1.0 / len(PHASES) for name in PHASES})
            continue
        rows.append(
            phase_scores(
                level,
                momentum,
                int(negative_level_domains.loc[week]),
                int(negative_momentum_domains.loc[week]),
                thresholds,
            )
        )
    return pd.DataFrame(rows, index=index, columns=list(PHASES))
