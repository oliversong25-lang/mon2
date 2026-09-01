"""변형 관측 층. 동결 엔진의 `prepare`와 `decide`는 그대로 쓰고 가운데만 갈아 끼운다.

`four_phase` 아래 파일은 **하나도 건드리지 않는다.** 대신 관측 점수를 만드는 부분을
여기에 다시 쓰고, 게이트를 끄면 v1.1의 점수를 비트 단위로 재현하는지 테스트가 검사한다.
재현하지 못하면 이 패키지의 모든 비교가 무의미하므로, 그것이 첫 번째 계약이다.

## 후퇴기 게이트를 어디에 다는가

회복과 같은 자리다 — **무정보 기준선(1/4)을 넘는 몫만** 깎고, 깎인 몫은 나머지 셋에
비례 배분한다. 침체처럼 전체를 깎지 않는 이유도 회복과 같다. 침체에는 "한 도메인 단독
금지"라는 명시적 금지가 있어 증거가 없으면 0에 가까워져야 하지만, 후퇴기의 요건은
금지가 아니라 **세기**의 문제다. 모멘텀이 살짝 음인 주가 후퇴기가 **아니라는** 것이
아니라, 그것만으로는 후퇴기라고 부르기에 약하다는 것이다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import pandas as pd

from ..current_state import domains as D
from ..four_phase import evidence as E
from ..four_phase.engine import ObservationLayer, PreparedInputs, _persistent_positive, _row

#: 게이트를 끈 값. 이 값이면 감쇠가 정확히 0이어야 한다.
DISABLED: Final[float] = 1.0


@dataclass(frozen=True)
class SlowdownGate:
    """후퇴기 경계 후보. 세 성분을 따로 켜고 끌 수 있다.

    ``deadband``     모멘텀이 이 배수만큼 중립대 아래로 내려가야 온전한 후퇴기 주장이 된다.
                     0이면 v1.1과 같은 "0 미만이면 후퇴기".
    ``persistence``  그 상태가 몇 주 이어져야 하는가. 회복의 9주와 같은 종류의 요건.
    ``breadth``      감속하는 동행 도메인이 몇 개여야 하는가. 침체의 폭 요건과 같은 종류.
    """

    deadband: float = 0.0
    persistence_weeks: int = 0
    breadth_domains: int = 0
    #: 지속을 셀 때 쓰는 중립대의 배수. 1.0이면 모델의 `neutral_momentum` 그대로다.
    #: 조건 안의 다른 자유 모수이며, 트랙 21에서는 한 번도 흔들어 보지 않았다.
    persistence_band: float = 1.0

    @property
    def enabled(self) -> bool:
        return self.deadband > 0 or self.persistence_weeks > 0 or self.breadth_domains > 0

    @property
    def name(self) -> str:
        if not self.enabled:
            return "boundary:off"
        parts = []
        if self.deadband > 0:
            parts.append(f"dead{self.deadband:g}")
        if self.persistence_weeks > 0:
            parts.append(f"persist{self.persistence_weeks}w")
        if self.breadth_domains > 0:
            parts.append(f"breadth{self.breadth_domains}")
        if self.persistence_band != 1.0:
            parts.append(f"band{self.persistence_band:g}")
        return "+".join(parts)


@dataclass(frozen=True)
class VariantLayer:
    """관측 층과 후퇴기 증거를 함께 든다.

    후퇴기 증거는 동결 `ObservationLayer`에 자리가 없다. 그 자료구조를 고치는 대신
    옆에 붙여 든다 — 동결 코드를 건드리지 않기 위해서다.
    """

    layer: ObservationLayer
    slowdown_detail: pd.DataFrame
    gate: SlowdownGate


def _persistent_negative(momentum: pd.Series, band: float) -> pd.Series:
    """총량 모멘텀이 연속으로 중립대 아래였던 주 수.

    회복이 쓰는 `_persistent_positive`의 거울상이다. 같은 함수를 부호만 뒤집어 쓰지
    않고 따로 둔 이유는, 동결 코드를 건드리지 않기 위해서다.
    """

    counts: list[int] = []
    streak = 0
    for value in momentum:
        streak = streak + 1 if float(value) < -band else 0
        counts.append(streak)
    return pd.Series(counts, index=momentum.index)


def slowdown_evidence(
    momentum: float,
    decelerating_domains: int,
    persistent_weeks: int,
    gate: SlowdownGate,
    thresholds: E.Thresholds,
) -> dict[str, float]:
    """후퇴기 증거. 켜지지 않은 성분은 1.0이라 곱해도 아무 일이 없다.

    회복 증거와 같은 모양으로 만든다 — 성분들의 곱이고, 각 성분은 `unit`으로 0..1에
    갇힌다. 그래야 두 국면의 요건을 같은 눈금으로 읽을 수 있다.
    """

    depth = (
        E.unit(-momentum - gate.deadband * thresholds.neutral_momentum, thresholds.neutral_momentum)
        if gate.deadband > 0
        else DISABLED
    )
    persistence = (
        E.unit(float(persistent_weeks), float(gate.persistence_weeks))
        if gate.persistence_weeks > 0
        else DISABLED
    )
    breadth = (
        E.unit(float(decelerating_domains) - (gate.breadth_domains - 1), 1.0)
        if gate.breadth_domains > 0
        else DISABLED
    )
    return {
        "depth": depth,
        "persistence": persistence,
        "breadth": breadth,
        "slowdown_evidence": depth * persistence * breadth,
    }


def observation_scores(
    level: float,
    momentum: float,
    contraction: float,
    recovery: float,
    slowdown: float,
    breadth: dict[str, float],
    thresholds: E.Thresholds,
) -> dict[str, float]:
    """v1.1의 관측 점수에 후퇴기 감쇠 한 단계를 더한 것.

    ``slowdown``이 1.0이면 그 단계가 정확히 아무 일도 하지 않으므로 v1.1과 같은 값이
    나온다. 나머지 순서와 계산은 동결 코드와 한 줄씩 같다.
    """

    strong = E.sigmoid(level / max(thresholds.neutral_level, 1e-9))
    rising = E.sigmoid(momentum / max(thresholds.neutral_momentum, 1e-9))
    quadrant = {
        "expansion": strong * rising,
        "slowdown": strong * (1.0 - rising),
        "recovery": (1.0 - strong) * rising,
        "contraction": (1.0 - strong) * (1.0 - rising),
    }
    entry = thresholds.contraction_entry
    allowed = E.unit(contraction - entry, max(1.0 - entry, 1e-9))
    residual = quadrant["contraction"] * (1.0 - allowed)
    quadrant["contraction"] *= allowed
    others = ("recovery", "expansion", "slowdown")
    weight = sum(quadrant[name] for name in others)
    for name in others:
        share = quadrant[name] / weight if weight > 0 else 1.0 / len(others)
        quadrant[name] += residual * share

    baseline = 1.0 / len(quadrant)
    held = max(0.0, quadrant["recovery"] - baseline) * (1.0 - recovery)
    quadrant["recovery"] -= held
    remaining = ("expansion", "slowdown", "contraction")
    weight = sum(quadrant[name] for name in remaining)
    for name in remaining:
        share = quadrant[name] / weight if weight > 0 else 1.0 / len(remaining)
        quadrant[name] += held * share

    # 여기가 유일하게 새로운 단계다. 회복과 같은 자리, 같은 방식으로 기준선 초과분만
    # 깎는다. 다만 **깎은 몫은 확장기로만 보낸다.**
    #
    # 처음에는 회복과 똑같이 나머지 셋에 비례 배분했다. 그랬더니 2020년 1~3월에
    # 회복기가 9주 연속으로 켜졌다 — 코로나 폭락이 시작되던 구간이다. 이유는 순서다.
    # 회복 감쇠가 이 단계보다 **먼저** 끝나므로, 여기서 회복에 넘긴 몫은 회복 자신의
    # 폭·지속 게이트를 통과하지 않은 채로 들어간다. 침체도 마찬가지다.
    #
    # 확장기만 받는 것은 임시방편이 아니라 진단과 같은 말이다 — 후퇴기가 흡수하던 것은
    # 확장기의 애매한 주였고, 증거가 모자라 후퇴기라 부르지 못하는 높은 수준의 주는
    # 확장기로 돌아가는 것이 맞다. 그리고 확장기는 자기 게이트가 없어서 우회할 게이트도
    # 없다.
    withheld = max(0.0, quadrant["slowdown"] - baseline) * (1.0 - slowdown)
    quadrant["slowdown"] -= withheld
    quadrant["expansion"] += withheld

    tilt = thresholds.breadth_weight
    scores = {
        name: max(E.FLOOR, value * (1.0 + tilt * breadth.get(name, 0.0)))
        for name, value in quadrant.items()
    }
    total = sum(scores.values())
    return {name: value / total for name, value in scores.items()}


def observation_layer(
    prepared: PreparedInputs,
    thresholds: E.Thresholds,
    stale_weeks: float,
    gate: SlowdownGate,
) -> VariantLayer:
    """동결 `observation_layer`와 같은 일을 하되 후퇴기 감쇠를 더한다."""

    index = prepared.index
    level_scaled = prepared.level_scaled
    momentum_scaled = prepared.momentum_scaled
    activity_level = prepared.activity_level
    activity_momentum = prepared.activity_momentum
    coincident = list(D.COINCIDENT_DOMAINS)
    negative_level, _, _ = D.count_states(level_scaled[coincident], thresholds.neutral_level)
    negative_momentum, positive_momentum, _ = D.count_states(
        momentum_scaled[coincident], thresholds.neutral_momentum
    )
    persistence = _persistent_positive(activity_momentum, thresholds.neutral_momentum)
    negative_persistence = _persistent_negative(
        activity_momentum, gate.persistence_band * thresholds.neutral_momentum
    )

    contraction_rows: list[dict[str, float]] = []
    recovery_rows: list[dict[str, float]] = []
    slowdown_rows: list[dict[str, float]] = []
    score_rows: list[dict[str, float]] = []
    for week in index:
        level = float(str(activity_level.loc[week]))
        momentum = float(str(activity_momentum.loc[week]))
        contraction = E.contraction_evidence(
            level,
            momentum,
            int(str(negative_level.loc[week])),
            int(str(negative_momentum.loc[week])),
            float(str(level_scaled.at[week, "labor_stress"])),
            float(str(momentum_scaled.at[week, "labor_stress"])),
            thresholds,
        )
        recovery = E.recovery_evidence(
            level,
            momentum,
            int(str(positive_momentum.loc[week])),
            int(str(persistence.loc[week])),
            thresholds,
        )
        slowdown = slowdown_evidence(
            momentum,
            int(str(negative_momentum.loc[week])),
            int(str(negative_persistence.loc[week])),
            gate,
            thresholds,
        )
        breadth = E.breadth_support(
            _row(level_scaled, week), _row(momentum_scaled, week), thresholds
        )
        contraction_rows.append(contraction)
        recovery_rows.append(recovery)
        slowdown_rows.append(slowdown)
        score_rows.append(
            observation_scores(
                level,
                momentum,
                contraction["contraction_evidence"],
                recovery["recovery_evidence"],
                slowdown["slowdown_evidence"],
                breadth,
                thresholds,
            )
        )

    contraction_detail_frame = pd.DataFrame(contraction_rows, index=index)
    recovery_detail_frame = pd.DataFrame(recovery_rows, index=index)
    confirming_counts = [
        E.confirming_coincident_domains(
            _row(level_scaled, week), _row(momentum_scaled, week), thresholds
        )
        for week in index
    ]
    confirming = pd.Series(confirming_counts, index=index, name="confirming_domains")
    raw_scores = pd.DataFrame(score_rows, index=index, columns=list(E.PHASES))
    alert_rows = [
        E.recession_alert(
            float(str(contraction_detail_frame.at[week, "alert_evidence"])),
            int(str(confirming.loc[week])),
            thresholds,
        )
        for week in index
    ]
    stale = prepared.weeks_since_release.gt(stale_weeks).any(axis=1)
    crowded = prepared.concentration.gt(thresholds.concentration_flag).fillna(False)
    neutral_both = activity_level.abs().le(thresholds.neutral_level) & activity_momentum.abs().le(
        thresholds.neutral_momentum
    )
    return VariantLayer(
        layer=ObservationLayer(
            raw_scores=raw_scores,
            contraction_detail=contraction_detail_frame,
            recovery_detail=recovery_detail_frame,
            negative_level_domains=negative_level,
            negative_momentum_domains=negative_momentum,
            positive_momentum_domains=positive_momentum,
            confirming_domains=confirming,
            alert_level=pd.Series([a for a, _ in alert_rows], index=index, name="recession_alert"),
            alert_character=pd.Series(
                [c for _, c in alert_rows], index=index, name="recession_alert_character"
            ),
            neutral_both=neutral_both,
            stale=stale,
            crowded=crowded,
            thresholds=thresholds,
        ),
        slowdown_detail=pd.DataFrame(slowdown_rows, index=index),
        gate=gate,
    )
