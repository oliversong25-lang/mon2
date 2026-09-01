"""계층 분류. 대국면을 먼저 고르고, 그 안에서 하위국면을 고른다.

후보 I는 12개 국면이 하나의 평면에서 경쟁했다. 그래서 서로 무관한 두 점수의 순위가
뒤바뀌는 것만으로 `slowdown_early → recovery_late` 같은 4단계 이동이 생겼다
(3단계 이상 점프 93건). 계층으로 나누면 다른 대국면의 하위국면 점수가 선택된
대국면의 하위국면과 직접 경쟁하지 않는다.

침체는 후보 I의 연접 규칙(동행 4개 중 3개가 수준·모멘텀 **양쪽** 모두 음수)을 쓰지
않는다. 그 규칙이 재현율을 0.884에서 0.694로 떨어뜨리고 금융위기 신호를 +2주에서
+11주로 늦췄다. 대신 연속적인 침체 증거 점수를 두 경로로 만든다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Final

import numpy as np
import pandas as pd

MAJORS: Final[tuple[str, ...]] = ("recovery", "expansion", "slowdown", "contraction")
SUBPHASES: Final[tuple[str, ...]] = ("early", "middle", "late")
PHASES: Final[tuple[str, ...]] = tuple(f"{major}_{sub}" for major in MAJORS for sub in SUBPHASES)

#: 어떤 상태도 정확히 0이 되지 않게 하는 하한.
FLOOR: Final[float] = 1e-6


def _sigmoid(value: float) -> float:
    if value > 40:
        return 1.0
    if value < -40:
        return 0.0
    return 1.0 / (1.0 + math.exp(-value))


def _unit(value: float, scale: float) -> float:
    """0~1로 자른 선형 증거. 척도는 개발구간에서 나온다."""

    if not np.isfinite(value):
        return 0.0
    return float(min(1.0, max(0.0, value / max(scale, 1e-9))))


@dataclass(frozen=True)
class MajorThresholds:
    """대국면 규칙. 전부 개발구간(1995~2012)에서만 도출한다."""

    neutral_level: float
    neutral_momentum: float
    level_severity: float
    momentum_severity: float
    level_breadth: int
    momentum_breadth: int
    corroboration_weight: float
    contraction_entry: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "neutral_level": self.neutral_level,
            "neutral_momentum": self.neutral_momentum,
            "level_severity": self.level_severity,
            "momentum_severity": self.momentum_severity,
            "level_breadth": self.level_breadth,
            "momentum_breadth": self.momentum_breadth,
            "corroboration_weight": self.corroboration_weight,
            "contraction_entry": self.contraction_entry,
        }


def contraction_evidence(
    level: float,
    momentum: float,
    negative_level_domains: int,
    negative_momentum_domains: int,
    labor_stress_level: float,
    thresholds: MajorThresholds,
) -> dict[str, float]:
    """침체 증거를 두 경로로 만든다. 어느 경로도 노동시장만으로 성립하지 않는다.

    넓은 수준 경로  현재 동행 도메인 수준이 넓게 낮고, 총량 모멘텀이 비양수다.
    급속 악화 경로  모멘텀이 넓고 심하게 음수이고, 수준이 더는 확장기가 아니며,
                    다른 독립 도메인이 이를 뒷받침한다.
    """

    level_severity = _unit(-level, thresholds.level_severity)
    momentum_severity = _unit(-momentum, thresholds.momentum_severity)
    level_breadth = _unit(float(negative_level_domains) - (thresholds.level_breadth - 1), 1.0)
    momentum_breadth = _unit(
        float(negative_momentum_domains) - (thresholds.momentum_breadth - 1), 1.0
    )
    momentum_nonpositive = _unit(
        -momentum + thresholds.neutral_momentum, thresholds.neutral_momentum
    )
    not_expansionary = _unit(thresholds.neutral_level - level, thresholds.neutral_level)
    corroboration = thresholds.corroboration_weight + (
        1.0 - thresholds.corroboration_weight
    ) * _unit(-labor_stress_level, thresholds.level_severity)

    broad_level_route = min(level_severity, level_breadth, momentum_nonpositive)
    rapid_route = min(momentum_severity, momentum_breadth, not_expansionary, corroboration)
    return {
        "broad_level_route": broad_level_route,
        "rapid_deterioration_route": rapid_route,
        "contraction_evidence": max(broad_level_route, rapid_route),
        "level_severity": level_severity,
        "momentum_severity": momentum_severity,
        "level_breadth": level_breadth,
        "momentum_breadth": momentum_breadth,
        "corroboration": corroboration,
    }


def major_scores(
    level: float,
    momentum: float,
    negative_level_domains: int,
    negative_momentum_domains: int,
    labor_stress_level: float,
    thresholds: MajorThresholds,
) -> dict[str, float]:
    """네 대국면 점수. 합은 1이고 어느 것도 0이 아니다."""

    strong = _sigmoid(level / max(thresholds.neutral_level, 1e-9))
    rising = _sigmoid(momentum / max(thresholds.neutral_momentum, 1e-9))
    scores = {
        "expansion": strong * rising,
        "slowdown": strong * (1.0 - rising),
        "recovery": (1.0 - strong) * rising,
        "contraction": (1.0 - strong) * (1.0 - rising),
    }
    evidence = contraction_evidence(
        level,
        momentum,
        negative_level_domains,
        negative_momentum_domains,
        labor_stress_level,
        thresholds,
    )["contraction_evidence"]
    allowed = _unit(evidence - thresholds.contraction_entry, 1.0 - thresholds.contraction_entry)
    removed = scores["contraction"] * (1.0 - allowed)
    scores["contraction"] -= removed
    scores["slowdown"] += removed
    total = sum(scores.values())
    return {name: max(FLOOR, value / total) for name, value in scores.items()}


@dataclass(frozen=True)
class SubphaseCuts:
    """대국면별 진행도 3분위. 각도가 아니라 현재 심각도로 나눈다."""

    cuts: dict[str, tuple[float, float]]

    def to_dict(self) -> dict[str, Any]:
        return {k: list(v) for k, v in self.cuts.items()}


def progression(
    major: str,
    level: float,
    momentum: float,
    negative_level_domains: int,
    negative_momentum_domains: int,
) -> float:
    """대국면 안에서 얼마나 진행됐나. 값이 클수록 늦은 단계다."""

    if major == "recovery":
        # 침체 수준에서 얼마나 회복했나.
        return level
    if major == "expansion":
        # 모멘텀이 약해질수록 성숙한 확장이다.
        return -momentum
    if major == "slowdown":
        # 수준이 낮고 모멘텀이 나쁘고 폭이 넓을수록 침체에 가깝다.
        return -level - momentum + 0.5 * float(negative_momentum_domains)
    if major == "contraction":
        # 초기는 막 넓어진 상태, 말기는 악화가 잦아드는 상태다.
        return -level + momentum
    raise ValueError(f"알 수 없는 대국면: {major}")


def subphase_scores(
    major: str,
    level: float,
    momentum: float,
    negative_level_domains: int,
    negative_momentum_domains: int,
    cuts: SubphaseCuts,
) -> dict[str, float]:
    """선택된 대국면 안에서만 경쟁한다. 다른 대국면의 하위국면과 섞이지 않는다."""

    value = progression(major, level, momentum, negative_level_domains, negative_momentum_domains)
    low, high = cuts.cuts[major]
    width = max((high - low) / 2.0, 1e-6)
    early = 1.0 - _sigmoid((value - low) / width)
    late = _sigmoid((value - high) / width)
    middle = max(0.0, 1.0 - early - late)
    raw = {"early": early, "middle": middle, "late": late}
    total = sum(raw.values())
    if total <= 0:
        return {name: 1.0 / 3.0 for name in SUBPHASES}
    return {name: max(FLOOR, value / total) for name, value in raw.items()}


def major_frame(
    level: pd.Series,
    momentum: pd.Series,
    negative_level: pd.Series,
    negative_momentum: pd.Series,
    labor_stress: pd.Series,
    thresholds: MajorThresholds,
) -> pd.DataFrame:
    rows = [
        major_scores(
            float(level.iloc[position]),
            float(momentum.iloc[position]),
            int(negative_level.iloc[position]),
            int(negative_momentum.iloc[position]),
            float(labor_stress.iloc[position]),
            thresholds,
        )
        for position in range(len(level))
    ]
    return pd.DataFrame(rows, index=level.index, columns=list(MAJORS))


def subphase_frames(
    level: pd.Series,
    momentum: pd.Series,
    negative_level: pd.Series,
    negative_momentum: pd.Series,
    cuts: SubphaseCuts,
) -> dict[str, pd.DataFrame]:
    frames: dict[str, pd.DataFrame] = {}
    for major in MAJORS:
        rows = [
            subphase_scores(
                major,
                float(level.iloc[position]),
                float(momentum.iloc[position]),
                int(negative_level.iloc[position]),
                int(negative_momentum.iloc[position]),
                cuts,
            )
            for position in range(len(level))
        ]
        frames[major] = pd.DataFrame(rows, index=level.index, columns=list(SUBPHASES))
    return frames
