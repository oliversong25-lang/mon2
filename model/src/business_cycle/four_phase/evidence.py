"""네 국면의 현재상태 증거. 약한 조건 하나가 전체를 막지 않게 만든다.

후보 J의 감사가 원인을 특정했다.

* **금융위기 +30주 지연** — 침체 경로가 ``min(심각도, 폭, 비확장, 뒷받침)``이었다.
  2007년 7월에 모멘텀 심각도와 폭은 이미 충족(음수 모멘텀 도메인 4/4)이었는데
  노동시장이 아직 나빠지지 않아 뒷받침 항이 하한 0.5에 묶였고, ``min``이 경로 전체를
  0.5로 잘랐다. 진입 문턱 0.807을 **영원히** 넘을 수 없는 구조였다.
  → 뒷받침을 **더하는 항**으로 바꾼다. 강한 모멘텀 증거만으로도 문턱을 넘을 수 있고,
    노동시장만으로는 최대 기여가 작아 단독 선언이 여전히 불가능하다.

* **회복 ↔ 후퇴 2단계 진동 10건** — 전부 수준이 정상 아래인 구간에서 모멘텀 부호가
  뒤집히며 생겼고, 필터가 만든 것은 0건이었다(원시 국면이 이미 그랬다).
  → 회복은 모멘텀 부호만으로 성립하지 않는다. **폭과 지속**을 함께 요구한다.

* **후퇴기 53.2% 점유** — 중립대 안 469주(28.0%)의 **79.7%**가 후퇴기였다. 침체 증거가
  모자랄 때 침체 몫을 전부 후퇴기로 몰아준 재분배가 원인이다.
  → 남는 몫을 나머지 셋에 **비례 배분**한다. 어느 국면도 특별대우를 받지 않는다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Final

import numpy as np
import pandas as pd

from ..current_state.domains import COINCIDENT_DOMAINS, DOMAINS

PHASES: Final[tuple[str, ...]] = ("recovery", "expansion", "slowdown", "contraction")

#: 어떤 국면도 정확히 0이 되지 않게 하는 하한. 0은 도달 불가능을 뜻한다.
FLOOR: Final[float] = 1e-6


def sigmoid(value: float) -> float:
    if value > 40:
        return 1.0
    if value < -40:
        return 0.0
    return 1.0 / (1.0 + math.exp(-value))


def unit(value: float, scale: float) -> float:
    """0~1로 자른 선형 증거. 척도는 개발구간에서 나온다."""

    if not np.isfinite(value):
        return 0.0
    return float(min(1.0, max(0.0, value / max(scale, 1e-9))))


@dataclass(frozen=True)
class Thresholds:
    """개발구간(1995~2012)에서만 도출해 잠근 값."""

    neutral_level: float
    neutral_momentum: float
    level_severity: float
    momentum_severity: float
    level_breadth: int
    momentum_breadth: int
    corroboration_share: float
    contraction_entry: float
    recovery_breadth: int
    recovery_momentum: float
    recovery_persistence_weeks: int
    breadth_weight: float
    concentration_flag: float
    #: §2의 금지. 공식 침체는 독립적인 동행 도메인 이만큼의 확인을 요구한다.
    #: 중단된 v1.0 설정에는 이 항목이 없으므로 0을 기본값으로 두어 감사 재현이 가능하다.
    minimum_coincident_domains: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "neutral_level": self.neutral_level,
            "neutral_momentum": self.neutral_momentum,
            "level_severity": self.level_severity,
            "momentum_severity": self.momentum_severity,
            "level_breadth": self.level_breadth,
            "momentum_breadth": self.momentum_breadth,
            "corroboration_share": self.corroboration_share,
            "contraction_entry": self.contraction_entry,
            "recovery_breadth": self.recovery_breadth,
            "recovery_momentum": self.recovery_momentum,
            "recovery_persistence_weeks": self.recovery_persistence_weeks,
            "breadth_weight": self.breadth_weight,
            "concentration_flag": self.concentration_flag,
            "minimum_coincident_domains": self.minimum_coincident_domains,
        }


def contraction_evidence(
    level: float,
    momentum: float,
    negative_level_domains: int,
    negative_momentum_domains: int,
    labor_stress_level: float,
    labor_stress_momentum: float,
    thresholds: Thresholds,
) -> dict[str, float]:
    """두 경로의 연속 증거. 약한 항 하나가 경로 전체의 천장이 되지 않는다.

    넓은 현재하락 경로   현재 수준이 상당히 음수이고 그 약세가 동행 도메인에 걸쳐 넓다.
                        총량 모멘텀이 비양수이거나 악화 중이어야 한다.
    급속 현재악화 경로   음수 모멘텀이 심하고 여러 동행 도메인에 걸쳐 넓다. 수준이 더는
                        분명한 확장이 아니고, 다른 독립 도메인이 이를 뒷받침한다.

    뒷받침은 **더하는 항**이다. 노동시장 단독 기여는 ``corroboration_share``로 묶여 있어
    그것만으로는 진입 문턱을 넘을 수 없다.
    """

    level_severity = unit(-level, thresholds.level_severity)
    momentum_severity = unit(-momentum, thresholds.momentum_severity)
    level_breadth = unit(float(negative_level_domains) - (thresholds.level_breadth - 1), 1.0)
    momentum_breadth = unit(
        float(negative_momentum_domains) - (thresholds.momentum_breadth - 1), 1.0
    )
    momentum_nonpositive = unit(
        -momentum + thresholds.neutral_momentum, 2.0 * thresholds.neutral_momentum
    )
    not_expansionary = unit(thresholds.neutral_level - level, 2.0 * thresholds.neutral_level)
    corroboration = max(
        unit(-labor_stress_level, thresholds.level_severity),
        unit(-labor_stress_momentum, thresholds.momentum_severity),
    )

    share = thresholds.corroboration_share
    core_broad = 0.5 * (level_severity + level_breadth)
    core_rapid = 0.5 * (momentum_severity + momentum_breadth)
    broad_route = (1.0 - share) * core_broad * momentum_nonpositive + share * corroboration
    rapid_route = (1.0 - share) * core_rapid * not_expansionary + share * corroboration

    # §2의 금지를 연속 항이 아니라 **하드 게이트**로 건다. 각 경로는 그 경로가 실제로
    # 읽는 폭에서 최소 도메인 수를 채워야 살아남는다. 넓은 하락 경로는 음수 수준
    # 도메인을, 급속 악화 경로는 음수 모멘텀 도메인을 본다. 노동시장은 동행 도메인이
    # 아니므로 이 셈에 들어가지 않는다 — 뒷받침만 할 수 있다.
    minimum = thresholds.minimum_coincident_domains
    broad_gate = 1.0 if negative_level_domains >= minimum else 0.0
    rapid_gate = 1.0 if negative_momentum_domains >= minimum else 0.0
    official = max(broad_route * broad_gate, rapid_route * rapid_gate)

    # 경보 증거는 폭을 **아예 빼고** 다시 잰다. 폭 항을 0으로 두는 것으로는 부족하다 —
    # 평균 안에 0이 들어가면 한 도메인의 극단적 충격조차 절반 아래로 눌려 경보가 영영
    # 울리지 않는다. 그러면 §3이 요구한 "심각하지만 한쪽에 몰림"이라는 상태가 도달
    # 불가능해진다. 그래서 공식 증거와 경보 증거의 차이는 정확히 폭 하나다.
    alert_broad = (1.0 - share) * level_severity * momentum_nonpositive + share * corroboration
    alert_rapid = (1.0 - share) * momentum_severity * not_expansionary + share * corroboration

    return {
        "broad_level_route": broad_route,
        "rapid_deterioration_route": rapid_route,
        "contraction_evidence": official,
        "alert_evidence": max(alert_broad, alert_rapid),
        "broad_route_gate": broad_gate,
        "rapid_route_gate": rapid_gate,
        "level_severity": level_severity,
        "momentum_severity": momentum_severity,
        "level_breadth": level_breadth,
        "momentum_breadth": momentum_breadth,
        "corroboration": corroboration,
        "momentum_nonpositive": momentum_nonpositive,
        "not_expansionary": not_expansionary,
    }


#: §3의 경보 성격. 공식 국면이 아니라 경보가 어떤 종류인지 설명하는 어휘다.
ALERT_CHARACTER: Final[tuple[str, ...]] = (
    "broad_and_confirmed",
    "severe_but_concentrated",
    "preliminary",
    "absent",
)


def confirming_coincident_domains(
    level_scaled: pd.Series, momentum_scaled: pd.Series, thresholds: Thresholds
) -> int:
    """침체를 실제로 확증하는 **동행** 도메인 수.

    한 도메인이 확증한다는 것은 그 도메인 자신이 중립대 아래로 약하거나 중립대 아래로
    악화 중이라는 뜻이다. 노동시장 스트레스는 동행 활동지표가 아니므로 이 셈에
    들어가지 않는다 — 뒷받침만 할 수 있다.
    """

    count = 0
    for domain in COINCIDENT_DOMAINS:
        if domain not in level_scaled.index:
            continue
        weak = float(level_scaled[domain]) < -thresholds.neutral_level
        falling = float(momentum_scaled[domain]) < -thresholds.neutral_momentum
        if weak or falling:
            count += 1
    return count


def recession_alert(
    alert_evidence: float, confirming_domains: int, thresholds: Thresholds
) -> tuple[str, str]:
    """§3의 보조 경보. 공식 국면과 별개이며 다섯 번째 국면이 아니다.

    경보는 폭 금지를 적용하지 **않은** 증거를 쓴다. 한 도메인의 극심한 충격도 경보를
    올릴 수 있다. 그래야 공식 국면이 폭 확인을 기다리는 동안 그 사실을 숨기지 않고
    드러낼 수 있다. 대신 경보의 **성격**을 함께 낸다 — 넓게 확인됐는지, 심각하지만
    한쪽에 몰렸는지, 아직 예비 단계인지, 없는지.
    """

    entry = thresholds.contraction_entry
    if alert_evidence >= entry:
        broad = confirming_domains >= thresholds.minimum_coincident_domains
        return "high", ("broad_and_confirmed" if broad else "severe_but_concentrated")
    if alert_evidence >= 0.8 * entry:
        return "elevated", "preliminary"
    if alert_evidence >= 0.5 * entry:
        return "watch", "preliminary"
    return "none", "absent"


def recovery_evidence(
    level: float,
    momentum: float,
    positive_momentum_domains: int,
    persistent_weeks: int,
    thresholds: Thresholds,
) -> dict[str, float]:
    """회복 증거. 모멘텀 부호 하나로 성립하지 않는다.

    후보 J의 2단계 진동 10건이 전부 여기서 나왔다. 수준이 정상 아래인 구간에서
    모멘텀이 한 주 양수로 뒤집히면 곧바로 회복기가 됐고, 다음 주 다시 후퇴기가 됐다.
    그래서 세 가지를 함께 요구한다 — 약한 수준에서 출발할 것, 개선이 넓을 것,
    잡음과 구분될 만큼 이어질 것.
    """

    weak_level = unit(-level + thresholds.neutral_level, 2.0 * thresholds.neutral_level)
    improvement = unit(momentum, thresholds.recovery_momentum)
    breadth = unit(float(positive_momentum_domains) - (thresholds.recovery_breadth - 1), 1.0)
    persistence = unit(float(persistent_weeks), float(thresholds.recovery_persistence_weeks))
    return {
        "weak_level": weak_level,
        "improvement": improvement,
        "breadth": breadth,
        "persistence": persistence,
        "recovery_evidence": weak_level * (0.5 * (improvement + breadth)) * persistence,
    }


def breadth_support(
    level_scaled: pd.Series, momentum_scaled: pd.Series, thresholds: Thresholds
) -> dict[str, float]:
    """각 국면을 실제로 뒷받침하는 동행 도메인 비율. 중립대 안에서 결정을 돕는다."""

    coincident = [d for d in COINCIDENT_DOMAINS if d in level_scaled.index]
    total = float(len(coincident)) or 1.0
    strong = sum(
        1
        for d in coincident
        if float(level_scaled[d]) > thresholds.neutral_level
        and float(momentum_scaled[d]) > -thresholds.neutral_momentum
    )
    decelerating = sum(
        1 for d in coincident if float(momentum_scaled[d]) < -thresholds.neutral_momentum
    )
    improving_weak = sum(
        1
        for d in coincident
        if float(level_scaled[d]) < thresholds.neutral_level
        and float(momentum_scaled[d]) > thresholds.neutral_momentum
    )
    declining = sum(
        1
        for d in coincident
        if float(level_scaled[d]) < -thresholds.neutral_level
        and float(momentum_scaled[d]) < -thresholds.neutral_momentum
    )
    return {
        "expansion": strong / total,
        "slowdown": decelerating / total,
        "recovery": improving_weak / total,
        "contraction": declining / total,
    }


def observation_scores(
    level: float,
    momentum: float,
    contraction: float,
    recovery: float,
    breadth: dict[str, float],
    thresholds: Thresholds,
) -> dict[str, float]:
    """네 국면 관측 점수. 합은 1이고 어느 것도 0이 아니다.

    사분면 가중치에서 출발하되, 침체 증거가 모자라 남는 몫은 나머지 **셋에 비례
    배분**한다. 후보 J는 그 몫을 전부 후퇴기로 보냈고, 그래서 중립대 안 주의 79.7%가
    후퇴기가 됐다. 어느 국면도 구조적으로 유리해서는 안 된다.

    그 다음 폭이 각 국면을 얼마나 뒷받침하는지로 가볍게 기울인다. 중립대 안에서
    사분면이 거의 균등할 때 결정을 돕는 것이 폭이다.
    """

    strong = sigmoid(level / max(thresholds.neutral_level, 1e-9))
    rising = sigmoid(momentum / max(thresholds.neutral_momentum, 1e-9))
    quadrant = {
        "expansion": strong * rising,
        "slowdown": strong * (1.0 - rising),
        "recovery": (1.0 - strong) * rising,
        "contraction": (1.0 - strong) * (1.0 - rising),
    }
    entry = thresholds.contraction_entry
    allowed = unit(contraction - entry, max(1.0 - entry, 1e-9))
    residual = quadrant["contraction"] * (1.0 - allowed)
    quadrant["contraction"] *= allowed
    others = ("recovery", "expansion", "slowdown")
    weight = sum(quadrant[name] for name in others)
    for name in others:
        share = quadrant[name] / weight if weight > 0 else 1.0 / len(others)
        quadrant[name] += residual * share

    # 회복은 폭·지속을 갖추지 못하면 그 주장을 줄인다. 다만 줄이는 대상은 **무정보
    # 기준선(1/4)을 넘는 몫**뿐이다. 전체를 깎아 한 국면에 몰아주면 후보 J가 침체 몫을
    # 후퇴기로 몰아준 것과 같은 편향이 생긴다 — 중립대 한가운데서 후퇴기가 과반이 됐다.
    # 침체와 다루는 방식이 다른 이유는 §9의 금지 조항 때문이다. 침체에는 "한 도메인
    # 단독 금지" 같은 명시적 금지가 있어 증거가 없으면 0에 가까워져야 하지만, 회복의
    # 요건은 금지가 아니라 세기의 문제다.
    baseline = 1.0 / len(quadrant)
    held = max(0.0, quadrant["recovery"] - baseline) * (1.0 - recovery)
    quadrant["recovery"] -= held
    remaining = ("expansion", "slowdown", "contraction")
    weight = sum(quadrant[name] for name in remaining)
    for name in remaining:
        share = quadrant[name] / weight if weight > 0 else 1.0 / len(remaining)
        quadrant[name] += held * share

    tilt = thresholds.breadth_weight
    scores = {
        name: max(FLOOR, value * (1.0 + tilt * breadth.get(name, 0.0)))
        for name, value in quadrant.items()
    }
    total = sum(scores.values())
    return {name: value / total for name, value in scores.items()}


def concentration(frame: pd.Series) -> float:
    magnitude = frame.abs()
    total = float(magnitude.sum())
    return float(magnitude.max() / total) if total > 0 else float("nan")


def domain_stance(
    level_scaled: pd.Series, momentum_scaled: pd.Series, phase: str, thresholds: Thresholds
) -> dict[str, str]:
    """도메인이 공식 국면을 뒷받침하는지·반대하는지·엇갈리는지."""

    expected = {
        "expansion": (+1, +1),
        "slowdown": (+1, -1),
        "contraction": (-1, -1),
        "recovery": (-1, +1),
    }[phase]
    out: dict[str, str] = {}
    for domain in DOMAINS:
        if domain not in level_scaled.index:
            continue
        level = float(level_scaled[domain])
        momentum = float(momentum_scaled[domain])
        level_ok = (level > 0) == (expected[0] > 0)
        momentum_ok = (momentum > 0) == (expected[1] > 0)
        out[domain] = (
            "supports"
            if level_ok and momentum_ok
            else ("opposes" if not level_ok and not momentum_ok else "mixed")
        )
    return out
