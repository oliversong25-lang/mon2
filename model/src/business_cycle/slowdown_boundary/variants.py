"""2x2 — 경계 수정과 트랙 16 게이트를 **따로, 그리고 함께**.

게이트는 후처리이고 경계는 엔진이다. 둘을 한꺼번에 걸면 어느 쪽이 일을 했는지 보이지
않는다. 근본 원인을 고쳤더니 게이트가 필요 없어지는 경우가 바로 그 2x2에서만 보인다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

import pandas as pd

from ..config import Settings, load_baseline
from ..four_phase.engine import FourPhaseConfig, PreparedInputs, decide
from ..transition_gate.gate import GateConfig
from ..transition_gate.gate import apply as apply_gate
from .scoring import SlowdownGate, observation_layer

#: 트랙 16이 권고한 게이트. 원시 동의만 요구하고 분리도 문턱은 걸지 않는다.
RECOMMENDED_GATE: Final[GateConfig] = GateConfig(require_raw_agreement=True)


@dataclass(frozen=True)
class Variant:
    """2x2의 한 칸. 이름이 곧 어느 축이 켜졌는지를 말한다."""

    key: str
    boundary: SlowdownGate
    transition_gate: bool

    @property
    def label(self) -> str:
        boundary = self.boundary.name if self.boundary.enabled else "boundary:off"
        return f"{boundary} · {'gate:on' if self.transition_gate else 'gate:off'}"


def path(prepared: PreparedInputs, config: FourPhaseConfig, variant: Variant) -> pd.DataFrame:
    """한 변형의 주간 경로. 동결 `decide`를 그대로 쓴다."""

    built = observation_layer(prepared, config.thresholds, config.stale_weeks, variant.boundary)
    run = decide(
        prepared,
        built.layer,
        config.lam,
        config.epsilon,
        config.confirmation_weeks,
        config.immediate_margin,
        config.separation_floor,
    )
    weeks = [str(week.date()) for week in run.official_phase.index]
    frame = pd.DataFrame(
        {
            "official_phase": [str(value) for value in run.official_phase],
            "raw_phase": [str(value) for value in run.raw_phase],
            "filtered_winner": [str(value) for value in run.filtered_winner],
            "activity_level": run.activity_level.to_numpy(),
            "activity_momentum": run.activity_momentum.to_numpy(),
            "confirming_domains": run.confirming_domains.to_numpy(),
            "negative_momentum_domains": run.negative_momentum_domains.to_numpy(),
            "concentration": run.concentration.to_numpy(),
            "contraction_evidence": run.contraction_detail["contraction_evidence"].to_numpy(),
            "recovery_evidence": run.recovery_detail["recovery_evidence"].to_numpy(),
            "slowdown_evidence": built.slowdown_detail["slowdown_evidence"].to_numpy(),
        },
        index=pd.Index(weeks, name="week"),
    )
    # 전이 게이트가 쓰는 이름으로 맞춘다. 게이트는 후처리라 경로만 보면 된다.
    frame["phase_status"] = "official"
    frame["phase_separation"] = _separation(run.raw_scores)
    if variant.transition_gate:
        gated = apply_gate(frame, RECOMMENDED_GATE)
        frame["official_phase"] = [
            value if value else "" for value in gated["gated_phase"].tolist()
        ]
        frame["phase_status"] = gated["gated_status"].tolist()
        frame["gate_reason"] = gated["gate_reason"].tolist()
    return frame


def _separation(raw_scores: pd.DataFrame) -> list[float]:
    """1위와 2위 관측 점수의 차. 게이트가 쓰는 값과 같은 정의다."""

    out: list[float] = []
    for _, row in raw_scores.iterrows():
        ordered = sorted((float(value) for value in row), reverse=True)
        out.append(ordered[0] - ordered[1] if len(ordered) > 1 else 0.0)
    return out


def matrix(
    boundary: SlowdownGate,
) -> tuple[Variant, Variant, Variant, Variant]:
    """네 칸. 순서는 보고서의 표 순서와 같다."""

    off = SlowdownGate()
    return (
        Variant("baseline", off, False),
        Variant("gate_only", off, True),
        Variant("boundary_only", boundary, False),
        Variant("boundary_and_gate", boundary, True),
    )


def reproduces_v1_1(
    prepared: PreparedInputs, config: FourPhaseConfig, frozen: pd.Series
) -> dict[str, Any]:
    """경계를 끈 변형이 동결 v1.1을 그대로 재현하는가.

    재현하지 못하면 이 패키지의 모든 비교가 무의미하다. 그래서 첫 번째 검사다.
    """

    baseline = path(prepared, config, Variant("baseline", SlowdownGate(), False))
    common = [week for week in baseline.index if week in set(frozen.index)]
    agree = sum(
        1 for week in common if str(baseline.at[week, "official_phase"]) == str(frozen[week])
    )
    return {
        "weeks_compared": len(common),
        "weeks_agreeing": agree,
        "reproduces": bool(agree == len(common) == len(frozen)),
    }


def build(settings: Settings) -> tuple[PreparedInputs, FourPhaseConfig]:
    """최신 빈티지 준비 입력. 임계값과 무관하므로 변형마다 다시 만들지 않는다."""

    from ..four_phase.alfred_audit import AS_OF
    from ..four_phase.engine import load_config, prepare
    from ..validation.phase4 import load_core_observations

    config = load_config(settings)
    core, _ = load_core_observations(settings)
    prepared = prepare(core, load_baseline("candidate_h_breadth_gate", settings), AS_OF, config)
    return prepared, config
