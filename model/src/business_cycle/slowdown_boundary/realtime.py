"""실시간(ALFRED) 경로에서 2x2를 다시 돌린다.

장기 경로는 **유효성**을 묻고 실시간 경로는 **사용 가능성**을 묻는다. 트랙 16의 게이트가
검증된 곳도 실시간 경로였으므로, 그 게이트가 장기 경로에서 거의 아무것도 하지 않는다는
관찰은 실시간에서 다시 확인해야 뜻이 생긴다.

빈티지마다 `prepare`를 한 번 하고 네 변형이 그것을 나눠 쓴다. 준비가 임계값과 무관해서
가능한 절약이고, 덕분에 네 변형이 하나보다 크게 비싸지 않다.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from ..config import Settings
from ..data.alfred import observations_as_of
from ..four_phase import freshness as FRESH
from ..four_phase.alfred_audit import load_audit_inputs
from ..four_phase.engine import decide, prepare
from ..transition_gate.gate import apply as apply_gate
from .scoring import observation_layer
from .variants import RECOMMENDED_GATE, Variant


def _week_row(prepared: Any, config: Any, variant: Variant, withheld: bool) -> dict[str, Any]:
    """그 빈티지의 마지막 주 한 줄. 동결 감사와 **같은 자리에서** 값을 읽는다.

    분리도는 `filtered_scores`에서 읽는다 — 동결 감사가 그렇게 하고, `raw_scores`에서
    읽으면 같은 이름의 다른 값이 된다.

    보류 판정은 `decide`가 하지 않는다. 신선도 정책이 따로 내리며, 그것을 빼면 동결
    ALFRED 경로의 보류 10주를 재현하지 못한다.
    """

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
    last = run.official_phase.index[-1]
    scores = run.filtered_scores.loc[last].tolist()
    ordered = sorted((float(value) for value in scores), reverse=True)
    return {
        "official_phase": "" if withheld else str(run.official_phase.loc[last]),
        "raw_phase": "" if withheld else str(run.raw_phase.loc[last]),
        "filtered_winner": str(run.filtered_winner.loc[last]),
        "activity_level": float(run.activity_level.loc[last]),
        "activity_momentum": float(run.activity_momentum.loc[last]),
        "confirming_domains": int(run.confirming_domains.loc[last]),
        "phase_separation": ordered[0] - ordered[1] if len(ordered) > 1 else 0.0,
        "phase_status": "withheld" if withheld else "official",
        "slowdown_evidence": float(built.slowdown_detail["slowdown_evidence"].loc[last]),
    }


def run(
    vintages: list[str],
    variants: tuple[Variant, ...],
    settings: Settings | None = None,
    progress_every: int = 50,
) -> dict[str, pd.DataFrame]:
    """빈티지별 그 주의 판정. 변형마다 하나의 주간 경로를 만든다."""

    inputs = load_audit_inputs(settings)
    config = inputs.config
    indicators = inputs.settings.indicators["indicators"]

    rows: dict[str, list[dict[str, Any]]] = {variant.key: [] for variant in variants}
    for position, moment in enumerate(vintages):
        stamp = pd.Timestamp(moment)
        observations = observations_as_of(inputs.frames, stamp, indicators)
        prepared = prepare(observations, inputs.baseline, stamp, config)
        eligibility = FRESH.evaluate(
            stamp,
            prepared.index,
            prepared.weeks_since_release,
            prepared.arrived,
            config.freshness,
        )
        for variant in variants:
            rows[variant.key].append(
                _week_row(prepared, config, variant, bool(eligibility.withheld))
            )
        if progress_every and position % progress_every == 0:
            print(f"  {position}/{len(vintages)} {moment}", flush=True)

    out: dict[str, pd.DataFrame] = {}
    for variant in variants:
        frame = pd.DataFrame(rows[variant.key], index=pd.Index(vintages, name="week"))
        if variant.transition_gate:
            gated = apply_gate(frame, RECOMMENDED_GATE)
            frame = frame.copy()
            frame["official_phase"] = [
                value if value else "" for value in gated["gated_phase"].tolist()
            ]
            frame["phase_status"] = gated["gated_status"].tolist()
            frame["gate_reason"] = gated["gate_reason"].tolist()
        out[variant.key] = frame
    return out
