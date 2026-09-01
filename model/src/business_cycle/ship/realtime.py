"""persist17w 실시간 경로를 **동결 감사와 같은 열**로 만든다.

## 왜 감사 코드를 고치지 않고 옮겨 적었는가

`four_phase/alfred_audit.py`는 보호 경로다. 거기에 변형 인자를 더하면 동결 모델을 고치는
것이 되고, 이후 단계들이 그것을 감지해 멈춘다 — 옳은 동작이다.

그래서 한 주의 행을 만드는 부분만 여기로 옮겨 적고, **부르는 함수는 전부 동결 쪽 것**을
쓴다. 바뀌는 것은 단 한 줄이다: `score(prepared, config)` 대신 후퇴기 게이트를 건
관측층 + 같은 `decide`. 그 한 줄이 이 파일이 존재하는 이유 전부다.

옮겨 적은 대가는 두 곳이 갈라질 수 있다는 것이다. 그래서 만든 뒤에 **동결 경로와
대조**한다 — 임계값과 무관한 열(수준·모멘텀·도메인 수·신선도)은 두 경로에서 같아야
하고, 다르면 옮겨 적기가 어긋난 것이므로 멈춘다.
"""

from __future__ import annotations

from typing import Any, Final

import pandas as pd

from ..current_state.domains import DOMAINS
from ..data.alfred import observations_as_of, slice_vintage
from ..four_phase import contract as C
from ..four_phase import freshness as FRESH
from ..four_phase.alfred_audit import AuditInputs
from ..four_phase.engine import decide, prepare
from ..slowdown_boundary.scoring import SlowdownGate, observation_layer

#: 두 경로에서 반드시 같아야 하는 열. 게이트 **이전에** 정해지는 것들이며, 다르면
#: 옮겨 적기가 어긋난 것이다.
#:
#: `evidence_quality_high`는 여기 없다. 처음에는 넣었는데 검사가 21주 불일치를 잡았고,
#: 확인해 보니 그것이 옳았다 — 증거 품질은 `separation >= separation_floor`를 포함하고
#: 분리도는 게이트가 바꾸는 점수에서 나온다. **분류를 잘못한 것은 나였고 검사가 맞았다.**
#: 그 21주는 감춰야 할 오차가 아니라 경계 수정의 결과이므로 `evidence_quality_changed`로
#: 세어 산출물에 싣는다.
THRESHOLD_INDEPENDENT: Final[tuple[str, ...]] = (
    "activity_level",
    "activity_momentum",
    "negative_level_domains",
    "negative_momentum_domains",
    "positive_momentum_domains",
    "confirming_domains",
    "concentration",
    "recession_alert",
    "information_lag_weeks",
    "phase_status",
)

#: 게이트가 바꾸는 것이 정상인 열. 세어서 보고하되 일치를 요구하지 않는다.
GATE_DEPENDENT: Final[tuple[str, ...]] = (
    "official_phase",
    "raw_phase",
    "phase_separation",
    "evidence_quality_high",
    "filtered_winner",
)


def audit_week(inputs: AuditInputs, vintage: pd.Timestamp, gate: SlowdownGate) -> dict[str, Any]:
    """한 as-of 시점의 전체 기록. 동결 `audit_week`와 같은 열, 같은 순서."""

    settings, config = inputs.settings, inputs.config
    observations = observations_as_of(inputs.frames, vintage, settings.indicators["indicators"])
    future = int((observations["release_date"] > vintage).sum())
    prepared = prepare(observations, inputs.baseline, vintage, config)
    row: dict[str, Any] = {"as_of": str(vintage.date()), "future_observations": future}
    if len(prepared.index) == 0:
        row.update({"phase_status": "withheld", "official_phase": "", "raw_phase": ""})
        return row

    eligibility = FRESH.evaluate(
        vintage, prepared.index, prepared.weeks_since_release, prepared.arrived, config.freshness
    )
    # ── 여기가 동결 경로와 다른 유일한 자리다. ──────────────────────────────
    built = observation_layer(prepared, config.thresholds, config.stale_weeks, gate)
    run = decide(
        prepared,
        built.layer,
        config.lam,
        config.epsilon,
        config.confirmation_weeks,
        config.immediate_margin,
        config.separation_floor,
    )
    # ────────────────────────────────────────────────────────────────────────
    week = run.official_phase.index[-1]
    withheld = eligibility.withheld

    scores = {name: float(str(run.filtered_scores.at[week, name])) for name in C.PHASES}
    ordered = sorted(scores.values(), reverse=True)
    magnitude = run.level_scaled.loc[week].abs()
    total = float(magnitude.sum())
    dominant = str(magnitude.idxmax()) if total > 0 else ""

    latest_observation: dict[str, str] = {}
    for series_id, frame in inputs.frames.items():
        visible = slice_vintage(frame, vintage)
        if not visible.empty:
            latest_observation[series_id] = str(pd.Timestamp(visible["date"].max()).date())

    row.update(
        {
            "last_modelled_week": str(pd.Timestamp(str(week)).date()),
            "raw_phase": str(run.raw_phase.loc[week]),
            "official_phase": "" if withheld else str(run.official_phase.loc[week]),
            "phase_status": eligibility.status,
            "activity_level": float(str(run.activity_level.loc[week])),
            "activity_momentum": float(str(run.activity_momentum.loc[week])),
            "negative_level_domains": int(str(run.negative_level_domains.loc[week])),
            "negative_momentum_domains": int(str(run.negative_momentum_domains.loc[week])),
            "positive_momentum_domains": int(str(run.positive_momentum_domains.loc[week])),
            "confirming_domains": int(str(run.confirming_domains.loc[week])),
            "concentration": float(str(run.concentration.loc[week])),
            "dominant_domain": dominant,
            "labor_stress_level": float(str(run.level_scaled.at[week, "labor_stress"])),
            "labor_stress_momentum": float(str(run.momentum_scaled.at[week, "labor_stress"])),
            "recession_alert": str(run.alert_level.loc[week]),
            "recession_alert_character": str(run.alert_character.loc[week]),
            "evidence_quality_high": bool(run.evidence_quality_high.loc[week]),
            "phase_separation": ordered[0] - ordered[1],
            "confirmation_pending": int(str(run.confirmation_pending.loc[week])),
            "filtered_winner": str(run.filtered_winner.loc[week]),
            "contraction_evidence": float(
                str(run.contraction_detail.at[week, "contraction_evidence"])
            ),
            "alert_evidence": float(str(run.contraction_detail.at[week, "alert_evidence"])),
            "slowdown_evidence": float(str(built.slowdown_detail.at[week, "slowdown_evidence"])),
            "information_lag_weeks": eligibility.information_lag_weeks,
            "weeks_since_any_new_observation": eligibility.weeks_since_any_new_observation,
            "fresh_coincident_domains": eligibility.fresh_coincident_domains,
            "stale_domains": "|".join(eligibility.stale_domains),
            "carried_forward_domains": sum(
                1 for value in eligibility.domain_carried_forward.values() if value
            ),
            "withheld": int(withheld),
        }
    )
    for name in C.PHASES:
        row[f"raw_{name}"] = float(str(run.raw_scores.at[week, name]))
        row[f"filtered_{name}"] = scores[name]
    for domain in DOMAINS:
        row[f"age_{domain}"] = float(str(run.weeks_since_release.at[week, domain]))
        row[f"arrived_{domain}"] = int(bool(run.arrived.at[week, domain]))
    for series_id, observed in latest_observation.items():
        row[f"latest_observation_{series_id}"] = observed
    return row


def build(
    inputs: AuditInputs,
    vintages: list[pd.Timestamp],
    gate: SlowdownGate,
    progress_every: int = 50,
) -> pd.DataFrame:
    """빈티지별 한 줄. 동결 감사와 같은 순서, 같은 열."""

    rows = []
    for position, vintage in enumerate(vintages, start=1):
        rows.append(audit_week(inputs, vintage, gate))
        if progress_every and position % progress_every == 0:
            print(f"  {position}/{len(vintages)} {vintage.date()}", flush=True)
    frame = pd.DataFrame(rows)
    frame["gate"] = gate.name
    return frame


def agrees_with_frozen(variant: pd.DataFrame, frozen: pd.DataFrame) -> dict[str, Any]:
    """임계값과 무관한 열이 두 경로에서 같은지 본다. 옮겨 적기가 어긋났으면 여기서 걸린다."""

    left = variant.set_index("as_of")
    right = frozen.set_index("as_of")
    weeks = [week for week in left.index if week in set(right.index)]
    disagreeing: dict[str, int] = {}
    for column in THRESHOLD_INDEPENDENT:
        if column not in left.columns or column not in right.columns:
            continue
        a = left.loc[weeks, column]
        b = right.loc[weeks, column]
        if a.dtype.kind in "fc" or b.dtype.kind in "fc":
            differs = ~(a.astype(float) - b.astype(float)).abs().le(1e-9)
        else:
            differs = a.astype(str) != b.astype(str)
        count = int(differs.sum())
        if count:
            disagreeing[column] = count
    changed: dict[str, int] = {}
    for column in GATE_DEPENDENT:
        if column not in left.columns or column not in right.columns:
            continue
        a = left.loc[weeks, column].astype(str)
        b = right.loc[weeks, column].astype(str)
        changed[column] = int((a != b).sum())

    return {
        "weeks_compared": len(weeks),
        "columns_compared": [c for c in THRESHOLD_INDEPENDENT if c in left.columns],
        "disagreeing": disagreeing,
        "gate_dependent_changes": changed,
        "agrees": not disagreeing,
        "why_it_must_agree": (
            "후퇴기 게이트는 국면 배분만 바꾼다. 수준·모멘텀·도메인 수·신선도는 게이트 "
            "이전에 정해지므로 두 경로에서 같아야 하고, 다르면 감사 코드를 옮겨 적은 "
            "쪽이 어긋난 것이다."
        ),
    }
