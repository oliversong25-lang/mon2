"""4국면 v1.1 산출물. §11의 순서를 그대로 따른다.

1. 개발구간 3주 왕복 10건 감사
2. 폭 제약 개발 프런티어 (`frontier` 모듈)
3. 분절 재현율 실현가능성 확인
4. 3주 왕복 5건 한도 확인
5. 2단계 전이 5건 한도 확인
6. 새 설정 동결과 해시
7. 최신 수정치 인과 검증
8. 필수 게이트를 모두 통과했을 때만 엄격 ALFRED

중단된 v1.0 산출물(`outputs/four_phase/`)은 감사 기록이므로 건드리지 않는다. 이 모듈은
`outputs/four_phase_v1_1/`에만 쓴다.
"""

# ruff: noqa: E501

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import numpy as np
import pandas as pd

from ..candidate_j import aggregate as A
from ..config import Settings, load_baseline, load_settings
from ..current_state import domains as D
from ..validation.phase4 import END, load_core_observations
from ..validation.real_data import _official_recession_flags
from . import alfred as AL
from . import contract as C
from . import evidence as E
from . import filter as FL
from . import freshness as FR_FRESH
from . import frontier as FR
from . import validation as V
from .engine import (
    STOPPED_CONFIG_NAME,
    FourPhaseConfig,
    FourPhaseRun,
    load_config,
    prepare,
    score,
    verify_frozen,
)

OUTPUT_NAME = "four_phase_v1_1"
STOPPED_OUTPUT_NAME = "four_phase"
FROZEN_BASELINE = "candidate_h_breadth_gate"
AS_OF = pd.Timestamp(END)
DEVELOPMENT = (pd.Timestamp("1995-01-01"), pd.Timestamp("2012-12-31"))
VALIDATION_START = pd.Timestamp("2013-01-01")

#: NBER 침체 시작. 타이밍을 재기 위한 기준일 뿐 모델 로직에 들어가지 않는다.
EPISODE_STARTS: dict[str, str] = {
    "recession_2001": "2001-03-01",
    "recession_2007": "2007-12-01",
    "recession_2020": "2020-02-01",
}


@dataclass(frozen=True)
class FourPhaseResult:
    output_dir: Path
    validated: bool
    adopted: bool
    stop_reason: str


def _row(frame: pd.DataFrame, week: pd.Timestamp) -> pd.Series:
    """한 주의 도메인 행. pandas 스텁이 DataFrame 가능성을 남겨 두어 좁혀 준다."""

    return cast("pd.Series", frame.loc[week])


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def whipsaw_audit(run: FourPhaseRun, window: pd.DatetimeIndex) -> dict[str, Any]:
    """§6의 감사. 왕복 하나하나가 왜 생겼는지 남긴다.

    같은 경계가 반복해서 원인이 됐는지, 발표 군집이 만든 것인지, 필터가 만든 것인지를
    가릴 수 있어야 한다. 감사 없이 왕복 수만 줄이면 원인을 모른 채 덮는 것이 된다.
    """

    phase = run.official_phase.reindex(window)
    raw = run.raw_phase.reindex(window)
    values = [str(value) for value in phase]
    weeks = list(window)
    cases: list[dict[str, Any]] = []
    for position in range(2, len(values)):
        if values[position] != values[position - 2] or values[position] == values[position - 1]:
            continue
        trio = [weeks[position - 2], weeks[position - 1], weeks[position]]
        middle = trio[1]
        raw_scores = _row(run.raw_scores, middle)
        ordered = sorted(float(str(value)) for value in raw_scores)
        cases.append(
            {
                "dates": [str(pd.Timestamp(str(week)).date()) for week in trio],
                "official_sequence": [
                    values[position - 2],
                    values[position - 1],
                    values[position],
                ],
                "raw_sequence": [str(raw.loc[week]) for week in trio],
                "boundary": "|".join(sorted({values[position - 2], values[position - 1]})),
                "activity_level": [round(float(str(run.activity_level.loc[w])), 4) for w in trio],
                "activity_momentum": [
                    round(float(str(run.activity_momentum.loc[w])), 4) for w in trio
                ],
                "negative_level_domains": [
                    int(str(run.negative_level_domains.loc[w])) for w in trio
                ],
                "negative_momentum_domains": [
                    int(str(run.negative_momentum_domains.loc[w])) for w in trio
                ],
                "positive_momentum_domains": [
                    int(str(run.positive_momentum_domains.loc[w])) for w in trio
                ],
                "domain_releases": [int(_row(run.arrived, w).sum()) for w in trio],
                "evidence_quality_high": [bool(run.evidence_quality_high.loc[w]) for w in trio],
                "raw_scores_middle": {
                    name: round(float(str(raw_scores[name])), 4) for name in C.PHASES
                },
                "filtered_scores_middle": {
                    name: round(float(str(run.filtered_scores.at[middle, name])), 4)
                    for name in C.PHASES
                },
                "raw_margin_middle": round(ordered[-1] - ordered[-2], 4),
                "contraction_evidence": [
                    round(float(str(run.contraction_detail.at[w, "contraction_evidence"])), 4)
                    for w in trio
                ],
                "recovery_evidence": [
                    round(float(str(run.recovery_detail.at[w, "recovery_evidence"])), 4)
                    for w in trio
                ],
            }
        )
    boundaries: dict[str, int] = {}
    for case in cases:
        boundaries[case["boundary"]] = boundaries.get(case["boundary"], 0) + 1
    identical = sum(1 for case in cases if case["raw_sequence"] == case["official_sequence"])
    margins = [case["raw_margin_middle"] for case in cases]
    return {
        "count": len(cases),
        "by_boundary": boundaries,
        "identical_raw_sequence": identical,
        "raw_margin_middle": {
            "maximum": max(margins) if margins else None,
            "at_or_below_0.10": sum(1 for value in margins if value <= 0.10),
        },
        "cases": cases,
    }


def recovery_slowdown_audit(run: FourPhaseRun, window: pd.DatetimeIndex) -> dict[str, Any]:
    """§9. 회복과 후퇴가 약한 활동 수준 근처에서 지나치게 대칭인지 본다.

    후보 J의 2단계 진동 10건이 전부 이 경계에서 나왔다. 정의상 두 국면은 대칭이
    아니어야 한다 — 회복은 **넓고 지속되는 개선**을 요구하고, 후퇴는 수축이 아닌
    상태에서 모멘텀이 약해지는 것이다. 한 주 부호가 뒤집혔다는 이유만으로 회복에서
    후퇴로 넘어가서는 안 된다.
    """

    phase = run.official_phase.reindex(window)
    values = [str(value) for value in phase]
    weeks = list(window)
    momentum = run.activity_momentum.reindex(window)
    level = run.activity_level.reindex(window)
    thresholds = run.thresholds

    moves: list[dict[str, Any]] = []
    for position in range(1, len(values)):
        pair = (values[position - 1], values[position])
        if set(pair) != {"recovery", "slowdown"}:
            continue
        week = weeks[position]
        previous = weeks[position - 1]
        before = float(str(momentum.loc[previous]))
        after = float(str(momentum.loc[week]))
        moves.append(
            {
                "date": str(pd.Timestamp(str(week)).date()),
                "move": f"{pair[0]}->{pair[1]}",
                "activity_level": round(float(str(level.loc[week])), 4),
                "momentum_before": round(before, 4),
                "momentum_after": round(after, 4),
                "momentum_sign_flip": bool((before > 0) != (after > 0)),
                "inside_neutral_momentum": bool(abs(after) <= thresholds.neutral_momentum),
                "recovery_evidence": round(
                    float(str(run.recovery_detail.at[week, "recovery_evidence"])), 4
                ),
                "positive_momentum_domains": int(str(run.positive_momentum_domains.loc[week])),
            }
        )

    neutral = (level.abs() <= thresholds.neutral_level) & (
        momentum.abs() <= thresholds.neutral_momentum
    )
    inside = phase[neutral]
    weak = phase[level.lt(-thresholds.neutral_level)]
    return {
        "recovery_slowdown_moves": len(moves),
        "moves_from_a_single_week_sign_flip": sum(
            1 for move in moves if move["momentum_sign_flip"] and move["inside_neutral_momentum"]
        ),
        "moves_into_recovery_without_evidence": sum(
            1
            for move in moves
            if move["move"].endswith("recovery") and move["recovery_evidence"] <= 0.0
        ),
        "neutral_zone_weeks": int(len(inside)),
        "neutral_zone_share": round(float(len(inside) / max(len(phase), 1)), 6),
        "neutral_zone_occupancy": {
            name: round(float(inside.eq(name).mean()), 6) if len(inside) else 0.0
            for name in C.PHASES
        },
        "weak_level_occupancy": {
            name: round(float(weak.eq(name).mean()), 6) if len(weak) else 0.0 for name in C.PHASES
        },
        "recovery_persistence_weeks": thresholds.recovery_persistence_weeks,
        "moves": moves,
    }


def confirmation_audit(
    run: FourPhaseRun, window: pd.DatetimeIndex, config: FourPhaseConfig
) -> dict[str, Any]:
    """§5. 확인 규칙이 후보 H의 고착을 약한 형태로 되살리지 않았는지.

    왕복 수가 0이 됐다는 것만으로 판정하지 않는다. 규칙이 실제로 얼마나 늦추는지,
    강한 증거가 여전히 즉시 통과하는지, 먼 과거가 영향을 잃는지, 지속적으로 반대되는
    증거가 필터를 이기는지를 각각 잰다.
    """

    official = run.official_phase.reindex(window)
    winner = run.filtered_winner.reindex(window)
    raw = run.raw_phase.reindex(window)
    quality = run.evidence_quality_high.reindex(window)
    delays = V.transition_delays(winner, official)
    summary = V.delay_summary(delays)

    high: list[int] = []
    low: list[int] = []
    for item in delays:
        moment = pd.Timestamp(str(item["date"]))
        (high if bool(quality.loc[moment]) else low).append(int(item["delay_weeks"]))
    summary["high_evidence_transitions"] = len(high)
    summary["low_evidence_transitions"] = len(low)
    summary["high_evidence_immediate"] = sum(1 for value in high if value == 0)
    summary["low_evidence_immediate"] = sum(1 for value in low if value == 0)
    summary["raw_official_disagreement_rate"] = round(float(raw.ne(official).mean()), 6)
    summary["longest_disagreement_run"] = V.longest_disagreement(raw, official)
    summary["confirmation_weeks"] = config.confirmation_weeks
    summary["immediate_margin"] = config.immediate_margin

    # 규칙이 약속한 상한을 실제로 지키는지. 넘으면 규칙 밖의 지연이 생긴 것이다.
    summary["delay_within_locked_rule"] = bool(
        summary["maximum_delay_weeks"] <= config.confirmation_weeks - 1
    )
    # 지속적으로 반대되는 증거가 필터를 이기는가. 확인 기간만큼 버틴 도전자는 반드시
    # 이겨야 하고, 그렇지 못한 주가 하나라도 있으면 고착이다.
    stalled = 0
    streak = 0
    previous = ""
    for moment in window:
        candidate = str(winner.loc[moment])
        current = str(official.loc[moment])
        if candidate == current:
            streak, previous = 0, ""
            continue
        streak = streak + 1 if candidate == previous else 1
        previous = candidate
        if streak > config.confirmation_weeks:
            stalled += 1
    summary["weeks_stalled_beyond_the_rule"] = stalled
    summary["recreates_a_state_lock"] = bool(
        stalled > 0 or summary["maximum_delay_weeks"] > config.confirmation_weeks
    )
    summary["finite_memory_convergence"] = FL.convergence(
        run.raw_scores.reindex(window), config.lam, config.epsilon, (4, 13, 26, 52)
    )
    summary["transitions"] = delays
    return summary


def false_positive_audit(
    run: FourPhaseRun, recession: pd.Series, window: pd.DatetimeIndex
) -> dict[str, Any]:
    """§6. 거짓 공식 침체 구간을 하나씩 드러낸다.

    주간 오탐률 하나에 묻어 두면 고립된 한 주와 넉 주 넘게 이어진 거짓 침체가 같은
    숫자에 섞인다. 둘은 투자 프레임워크에 전혀 다른 피해를 준다.
    """

    phase = run.official_phase.reindex(window)
    truth = recession.reindex(window).fillna(False).astype(bool)
    predicted = phase.eq("contraction")
    false_positive = predicted & ~truth
    weeks = list(window)
    spans = V.episodes(pd.Series(false_positive.to_numpy(dtype=bool)))
    truth_spans = V.episodes(truth)

    episodes: list[dict[str, Any]] = []
    for start, end in spans:
        first, last = weeks[start], weeks[end - 1]
        duration = end - start
        raw_first = next(
            (
                str(pd.Timestamp(str(weeks[i])).date())
                for i in range(start, end)
                if str(run.raw_phase.loc[weeks[i]]) == "contraction"
            ),
            None,
        )
        confirmed = str(pd.Timestamp(str(weeks[start + 3])).date()) if duration >= 4 else None
        peak = min(range(start, end), key=lambda i: float(str(run.activity_level.loc[weeks[i]])))
        moment = weeks[peak]
        contributions = _row(run.level_scaled, moment).abs()
        total = float(contributions.sum())
        dominant = sorted(
            (
                {
                    "domain": str(name),
                    "share": round(float(value) / total, 4) if total > 0 else None,
                }
                for name, value in contributions.items()
            ),
            key=lambda item: item["share"] or 0.0,
            reverse=True,
        )[:2]
        distances: list[int] = [min(abs(start - a), abs(end - b)) for a, b in truth_spans]
        episodes.append(
            {
                "start_date": str(pd.Timestamp(str(first)).date()),
                "end_date": str(pd.Timestamp(str(last)).date()),
                "duration_weeks": duration,
                "kind": (
                    "four_week_confirmed"
                    if duration >= 4
                    else ("short_preliminary_signal" if duration > 1 else "isolated_week")
                ),
                "first_raw_contraction_date": raw_first,
                "first_official_contraction_date": str(pd.Timestamp(str(first)).date()),
                "four_week_confirmation_date": confirmed,
                "peak_week": str(pd.Timestamp(str(moment)).date()),
                "activity_level_at_peak": round(float(str(run.activity_level.loc[moment])), 4),
                "activity_momentum_at_peak": round(
                    float(str(run.activity_momentum.loc[moment])), 4
                ),
                "raw_scores_at_peak": {
                    name: round(float(str(run.raw_scores.at[moment, name])), 4) for name in C.PHASES
                },
                "negative_level_domains": int(str(run.negative_level_domains.loc[moment])),
                "negative_momentum_domains": int(str(run.negative_momentum_domains.loc[moment])),
                "confirming_domains": int(str(run.confirming_domains.loc[moment])),
                "concentration": round(float(str(run.concentration.loc[moment])), 4),
                "dominant_domains": dominant,
                "recession_alert": str(run.alert_level.loc[moment]),
                "recession_alert_character": str(run.alert_character.loc[moment]),
                "weeks_to_nearest_nber_recession": min(distances) if distances else None,
                "in_late_2019": bool(
                    pd.Timestamp("2019-07-01") <= first <= pd.Timestamp("2019-12-31")
                ),
                "after_2022": bool(first >= pd.Timestamp("2022-07-01")),
            }
        )
    kinds: dict[str, int] = {}
    for episode in episodes:
        kinds[str(episode["kind"])] = kinds.get(str(episode["kind"]), 0) + 1
    return {
        "false_positive_weeks": int(false_positive.sum()),
        "episodes": len(episodes),
        "by_kind": kinds,
        "longest_episode_weeks": max((e["duration_weeks"] for e in episodes), default=0),
        "four_week_confirmed_episodes": kinds.get("four_week_confirmed", 0),
        "in_late_2019": sum(1 for e in episodes if e["in_late_2019"]),
        "after_2022": sum(1 for e in episodes if e["after_2022"]),
        "detail": episodes,
    }


def alert_audit(
    run: FourPhaseRun, recession: pd.Series, window: pd.DatetimeIndex
) -> dict[str, Any]:
    """§4. 보조 경보가 공식 국면과 분리된 채 제 일을 하는지.

    경보는 공식 국면을 덮어쓰지 않는다. 폭이 모자라 공식 침체를 선언할 수 없을 때도
    심각하지만 한쪽에 몰린 충격을 드러낼 수 있어야 한다.
    """

    alert = run.alert_level.reindex(window)
    character = run.alert_character.reindex(window)
    phase = run.official_phase.reindex(window)
    truth = recession.reindex(window).fillna(False).astype(bool)
    near = truth.rolling(53, center=True, min_periods=1).max().astype(bool)
    quiet = ~near
    high = alert.eq("high")

    spans = V.episodes(pd.Series(high.to_numpy(dtype=bool)))
    weeks = list(window)
    detail: list[dict[str, Any]] = []
    for start, end in spans:
        moment = weeks[start]
        detail.append(
            {
                "start_date": str(pd.Timestamp(str(moment)).date()),
                "end_date": str(pd.Timestamp(str(weeks[end - 1])).date()),
                "duration_weeks": end - start,
                "character": str(character.loc[moment]),
                "aggregate_level": round(float(str(run.activity_level.loc[moment])), 4),
                "aggregate_momentum": round(float(str(run.activity_momentum.loc[moment])), 4),
                "concentration": round(float(str(run.concentration.loc[moment])), 4),
                "confirming_domains": int(str(run.confirming_domains.loc[moment])),
                "labor_stress_level": round(
                    float(str(run.level_scaled.at[moment, "labor_stress"])), 4
                ),
                "labor_stress_momentum": round(
                    float(str(run.momentum_scaled.at[moment, "labor_stress"])), 4
                ),
                "official_contraction_declared": bool(
                    phase.iloc[start:end].eq("contraction").any()
                ),
                "during_nber_recession": bool(truth.iloc[start:end].any()),
            }
        )
    return {
        "high_share_of_quiet_weeks": round(float(high[quiet].mean()), 6) if quiet.any() else None,
        "high_share_of_recession_weeks": round(float(high[truth].mean()), 6)
        if truth.any()
        else None,
        "level_counts": {str(k): int(v) for k, v in alert.value_counts().items()},
        "character_counts": {str(k): int(v) for k, v in character.value_counts().items()},
        "high_episodes": len(detail),
        "concentrated_high_episodes": sum(
            1 for item in detail if item["character"] == "severe_but_concentrated"
        ),
        # 경보가 공식 국면을 덮어쓰지 않는다는 것은 주장이 아니라 측정이어야 한다.
        # 두 방향 모두 실제로 일어나면, 경보가 국면을 결정하지 않는다는 증거가 된다.
        "high_alert_without_official_contraction": int((high & phase.ne("contraction")).sum()),
        "official_contraction_without_high_alert": int((phase.eq("contraction") & ~high).sum()),
        "concentrated_alert_declared_official_contraction": int(
            (character.eq("severe_but_concentrated") & phase.eq("contraction")).sum()
        ),
        "detail": detail,
    }


def current_output(
    run: FourPhaseRun,
    week: pd.Timestamp,
    config: FourPhaseConfig,
    eligibility: FR_FRESH.Eligibility | None = None,
) -> dict[str, Any]:
    """§3·§14의 사용자 대면 출력. 투자 판단은 만들지 않는다."""

    thresholds = run.thresholds
    level = float(str(run.activity_level.loc[week]))
    momentum = float(str(run.activity_momentum.loc[week]))
    phase = str(run.official_phase.loc[week])
    stance = E.domain_stance(
        _row(run.level_scaled, week), _row(run.momentum_scaled, week), phase, thresholds
    )
    scores = {name: float(str(run.filtered_scores.at[week, name])) for name in C.PHASES}
    ordered = sorted(scores.values(), reverse=True)
    concentration = float(str(run.concentration.loc[week]))
    freshness = {
        domain: float(str(run.weeks_since_release.loc[week, domain]))
        for domain in run.weeks_since_release.columns
    }
    reasons: list[str] = []
    if abs(level) <= thresholds.neutral_level and abs(momentum) <= thresholds.neutral_momentum:
        reasons.append("activity level and momentum are both inside the neutral zone")
    if np.isfinite(concentration) and concentration > thresholds.concentration_flag:
        reasons.append(f"one domain carries {concentration:.1%} of the level signal")
    stale = [domain for domain, age in freshness.items() if age > config.stale_weeks]
    if stale:
        reasons.append(f"stale domains ({', '.join(sorted(stale))})")
    if ordered[0] - ordered[1] < config.separation_floor:
        reasons.append("phase scores are not strongly separated")
    if str(run.raw_phase.loc[week]) != phase:
        reasons.append("raw and filtered readings disagree")
    quality = "high" if not reasons else ("medium" if len(reasons) == 1 else "low")

    order = {name: index for index, name in enumerate(C.PHASES)}
    forward = C.PHASES[(order[phase] + 1) % len(C.PHASES)]
    backward = C.PHASES[(order[phase] - 1) % len(C.PHASES)]
    adjacent = {name: scores[name] for name in (forward, backward)}
    leader = max(adjacent, key=lambda name: adjacent[name])
    watch = f"toward_{leader}" if adjacent[leader] > scores[phase] * 0.6 else "none"

    status = eligibility.status if eligibility is not None else "official"
    if eligibility is not None and eligibility.withheld:
        # 판정 보류일 때는 공식 국면을 내지 않는다. 계약이 그것을 강제한다.
        phase = None  # type: ignore[assignment]
    payload = {
        "official_current_phase": phase,
        "phase_status": status,
        "phase_separation": round(ordered[0] - ordered[1], 6),
        "evidence_quality": quality,
        "activity_level": round(level, 6),
        "activity_momentum": round(momentum, 6),
        "domain_breadth": {
            "negative_level_domains": int(str(run.negative_level_domains.loc[week])),
            "negative_momentum_domains": int(str(run.negative_momentum_domains.loc[week])),
            "positive_momentum_domains": int(str(run.positive_momentum_domains.loc[week])),
            "confirming_coincident_domains": int(str(run.confirming_domains.loc[week])),
            "minimum_for_official_contraction": thresholds.minimum_coincident_domains,
            "coincident_domains": len(D.COINCIDENT_DOMAINS),
        },
        "contribution_concentration": round(concentration, 6),
        "supporting_domains": [k for k, v in stance.items() if v == "supports"],
        "opposing_domains": [k for k, v in stance.items() if v == "opposes"],
        "mixed_domains": [k for k, v in stance.items() if v == "mixed"],
        "transition_watch": watch,
        "recession_alert": str(run.alert_level.loc[week]),
        "recession_alert_character": str(run.alert_character.loc[week]),
        "as_of_date": str(pd.Timestamp(str(week)).date()),
        "latest_observation_by_domain": {k: round(v, 1) for k, v in freshness.items()},
        "known_limitations": [
            "official_current_phase는 현재상태 측정이며 예측이 아니다.",
            "공식 침체는 독립적인 동행 도메인 둘 이상의 확인을 요구한다. 한 도메인의 극단적 충격은 경보를 올리되 공식 국면을 바꾸지 않는다.",
            "전체 NBER 재현율 목표를 0.80으로 둔 것은, 현재상태 정의 아래에서 침체 말기의 넓은 개선 주가 회복기에 속할 수 있기 때문이다. 진입과 핵심 탐지는 별도 게이트로 엄격히 잰다.",
            "phase_separation은 내부 점수 차이이며 보정된 확률이 아니다.",
            "evidence_quality도 보정된 확률이 아니다.",
            "이 결과는 투자 판단이 아니다. 섹터·비중·종목 판단은 사용자 몫이다.",
        ],
        "raw_current_phase": str(run.raw_phase.loc[week]),
        "raw_scores": {
            name: round(float(str(run.raw_scores.at[week, name])), 6) for name in C.PHASES
        },
        "filtered_scores": {name: round(value, 6) for name, value in scores.items()},
        "evidence_reasons": reasons,
        "information_freshness": eligibility.to_dict() if eligibility is not None else None,
        "confirmation_pending_weeks": int(str(run.confirmation_pending.loc[week])),
        "filtered_winner_before_confirmation": str(run.filtered_winner.loc[week]),
        "coordinates_diagnostic_only": {
            key: round(float(value), 6)
            for key, value in run.coordinates.loc[week].to_dict().items()
        },
    }
    C.validate(payload)
    return payload


def _confirmed_weeks(phase: pd.Series, window: pd.DatetimeIndex, run_length: int = 4) -> int:
    """그 구간에서 4주 이상 이어진 공식 침체 주 수. 고립된 한 주와 구분한다."""

    if len(window) == 0:
        return 0
    values = phase.reindex(window).eq("contraction").to_numpy(dtype=bool)
    total = streak = 0
    for value in values:
        streak = streak + 1 if value else 0
        if streak >= run_length:
            total += 1
    return total


def window_report(
    run: FourPhaseRun,
    recession: pd.Series,
    window: pd.DatetimeIndex,
    config: FourPhaseConfig,
) -> dict[str, Any]:
    """한 구간의 성적 전부. 분절 재현율을 하나로 합치지 않는다."""

    phase = run.official_phase.reindex(window)
    truth = recession.reindex(window).fillna(False).astype(bool)
    report: dict[str, Any] = {"weeks": int(len(window))}
    report.update(V.recession_metrics(phase, truth))
    report.update(V.stability(phase))
    raw = run.raw_phase.reindex(window)
    report["raw_official_disagreement_rate"] = round(float(raw.ne(phase).mean()), 6)
    report["longest_disagreement_run"] = V.longest_disagreement(raw, phase)
    confirming = run.confirming_domains.reindex(window)
    report["single_domain_official_contraction_weeks"] = int(
        (phase.eq("contraction") & confirming.lt(run.thresholds.minimum_coincident_domains)).sum()
    )
    ordered = np.sort(run.filtered_scores.reindex(window).to_numpy(dtype=float), axis=1)
    separation = pd.Series(ordered[:, -1] - ordered[:, -2], index=window)
    neutral_both = run.activity_level.reindex(window).abs().le(
        run.thresholds.neutral_level
    ) & run.activity_momentum.reindex(window).abs().le(run.thresholds.neutral_momentum)
    reasons = (
        neutral_both.astype(int)
        + run.concentration.reindex(window)
        .gt(run.thresholds.concentration_flag)
        .fillna(False)
        .astype(int)
        + run.weeks_since_release.reindex(window).gt(config.stale_weeks).any(axis=1).astype(int)
        + separation.lt(config.separation_floor).astype(int)
    )
    report["certainty"] = V.certainty_monotonicity(separation, reasons)
    report["recession_alert_share"] = {
        str(key): round(float(value), 6)
        for key, value in run.alert_level.reindex(window).value_counts(normalize=True).items()
    }
    timings: dict[str, Any] = {}
    for name, start in EPISODE_STARTS.items():
        moment = pd.Timestamp(start)
        if window[0] <= moment <= window[-1]:
            timings[name] = V.signal_timing(phase, run.alert_level.reindex(window), raw, moment)
    report["episode_timing"] = timings
    late_2019 = window[
        (window >= pd.Timestamp("2019-07-01")) & (window <= pd.Timestamp("2019-12-31"))
    ]
    post_2022 = window[window >= pd.Timestamp("2022-07-01")]
    report["late_2019_confirmed_contraction_weeks"] = _confirmed_weeks(phase, late_2019)
    report["post_2022_confirmed_contraction_weeks"] = _confirmed_weeks(phase, post_2022)
    report["recession_weeks_as_contraction"] = int((phase.eq("contraction") & truth).sum())
    return report


def adoption_gates(latest: dict[str, Any], config: FourPhaseConfig) -> dict[str, Any]:
    """§12의 필수 게이트. 하나라도 실패하면 엄격 ALFRED를 돌리지 않고 재조정도 하지 않는다."""

    gates = config.gates
    timing = latest["episode_timing"]

    def _weeks(name: str) -> float:
        value = timing.get(name, {}).get("first_official_contraction_weeks")
        return float("inf") if value is None else float(value)

    checks = {
        "all_phases_reachable": len(latest["phases_reached"]) == len(C.PHASES),
        "no_absorbing_state": not latest["unexited_phases"],
        "bounded_raw_official_disagreement": (
            latest["longest_disagreement_run"] <= int(gates["longest_disagreement_run_maximum"])
        ),
        "no_single_domain_official_contraction": (
            latest["single_domain_official_contraction_weeks"] == 0
        ),
        "no_low_evidence_certainty_inversion": bool(latest["certainty"]["no_inversion"]),
        "overall_recall": latest["overall_recall"] >= float(gates["recession_recall_minimum"]),
        "core_recall": latest["core_recall"] >= float(gates["core_recession_recall_minimum"]),
        "false_positive_rate": (
            latest["false_positive_rate"] <= float(gates["false_positive_rate_maximum"])
        ),
        "gfc_first_signal": (
            _weeks("recession_2007") <= float(gates["gfc_first_signal_maximum_weeks"])
        ),
        "pandemic_first_signal": (
            _weeks("recession_2020") <= float(gates["pandemic_first_signal_maximum_weeks"])
        ),
        "pandemic_contraction_weeks": (
            latest["recession_weeks_as_contraction"]
            >= int(gates["pandemic_recession_weeks_as_contraction_minimum"])
        ),
        "late_2019_confirmed_contraction": (
            latest["late_2019_confirmed_contraction_weeks"]
            <= int(gates["late_2019_confirmed_contraction_maximum"])
        ),
        "post_2022_confirmed_contraction": (
            latest["post_2022_confirmed_contraction_weeks"]
            <= int(gates["post_2022_confirmed_contraction_maximum"])
        ),
        "two_step_transitions": (
            latest["two_step_transitions"] <= int(gates["two_step_transitions_maximum"])
        ),
        "three_week_whipsaws": (
            latest["three_week_whipsaws"] <= int(gates["three_week_whipsaws_maximum"])
        ),
    }
    return {"checks": checks, "passed": all(checks.values())}


def _gate_failure_text(gates: dict[str, Any]) -> str:
    failed = [name for name, passed in gates["checks"].items() if not passed]
    return "§12의 필수 게이트 실패: " + ", ".join(failed)


def _provenance(settings: Settings, output: Path) -> dict[str, Any]:
    """§7이 요구한 출처 기록. 어느 소스·어느 프런티어에서 나온 설정인지 남긴다."""

    frontier_path = output / "development_frontier.json"
    frontier: dict[str, Any] = {}
    if frontier_path.exists():
        document = json.loads(frontier_path.read_text(encoding="utf-8"))
        frontier = {
            "artifact_sha256": hashlib.sha256(frontier_path.read_bytes()).hexdigest(),
            "csv_sha256": document.get("frontier_csv_sha256"),
            "selection_rule_sha256": document.get("selection_rule_sha256"),
            "source_commit": document.get("source_commit"),
            "generated_at_utc": document.get("generated_at_utc"),
            "feasible_combinations": document.get("feasible_combinations"),
            "combinations_evaluated": document.get("combinations_evaluated"),
        }
    return {
        "source_commit": FR.source_commit(),
        "selection_rule_sha256": FR.selection_rule_digest(),
        "frontier": frontier,
    }


def build_report(settings: Settings | None = None, run_alfred: bool = True) -> FourPhaseResult:
    base = settings or load_settings()
    output = base.root / "outputs" / OUTPUT_NAME
    output.mkdir(parents=True, exist_ok=True)
    digest = verify_frozen(base, output)
    config = load_config(base)
    stopped = load_config(base, STOPPED_CONFIG_NAME)

    frozen = load_baseline(FROZEN_BASELINE, base)
    core, source = load_core_observations(base)
    prepared = prepare(core, frozen, AS_OF, config)
    recession = _official_recession_flags(source, prepared.index)
    index = prepared.index
    development = index[(index >= DEVELOPMENT[0]) & (index <= DEVELOPMENT[1])]
    validation = index[index >= VALIDATION_START]

    # 1. 중단된 v1.0 설정의 왕복 감사. 원인을 특정하지 않은 채 수만 줄이지 않는다.
    stopped_run = score(prepared, stopped)
    audit = whipsaw_audit(stopped_run, development)
    _write_json(output / "whipsaw_audit.json", audit)

    # §12의 필수 게이트는 **최신 수정치 인과 실행 전체**에서 잰다. 2013년 이후로만
    # 자르면 금융위기 +10주 타이밍 게이트를 잴 수가 없고(금융위기는 개발구간 안에 있다),
    # 그 창에는 8주짜리 침체 하나뿐이라 재현율이 한 주에 12.5%p씩 움직인다. 개발구간은
    # 모수를 **뽑은** 곳이지 게이트를 재는 곳이 아니다. 2013년 이후 홀드아웃은 따로
    # 보고한다.
    run = score(prepared, config)
    development_report = window_report(run, recession, development, config)
    latest_report = window_report(run, recession, index, config)
    holdout_report = window_report(run, recession, validation, config)
    gates = adoption_gates(latest_report, config)

    # §9. 회복·후퇴 경계가 여전히 지나치게 대칭인지. 2026년 결과에 맞춰 조정하지 않는다.
    boundary = recovery_slowdown_audit(run, index)
    _write_json(output / "recovery_slowdown_audit.json", boundary)

    # §5. 확인 규칙이 고착을 되살리지 않았는지. 왕복 수 하나로 판정하지 않는다.
    confirmation = confirmation_audit(run, index, config)
    _write_json(output / "confirmation_audit.json", confirmation)

    # §6. 거짓 공식 침체 구간을 하나씩. 주간 오탐률 안에 묻지 않는다.
    false_positives = false_positive_audit(run, recession, index)
    _write_json(output / "false_positive_audit.json", false_positives)

    # §4. 보조 경보가 공식 국면과 분리된 채 제 일을 하는지.
    alerts = alert_audit(run, recession, index)
    _write_json(output / "recession_alert_audit.json", alerts)

    week = AS_OF if AS_OF in index else index[-1]
    # 최신 수정치 경로에서는 as-of가 마지막 모델링 주와 같으므로 정보 지연이 0이다.
    # 그래도 정책을 통과시켜 상태를 계산한다 — 예외를 두지 않기 위해서다.
    eligibility = FR_FRESH.evaluate(
        AS_OF, index, run.weeks_since_release, run.arrived, config.freshness
    )
    current = current_output(run, week, config, eligibility)
    _write_json(output / "current_reading.json", current)
    _write_json(output / "country_contract.json", C.country_schema())

    matrix = FL.transition_matrix(config.lam, config.epsilon)
    convergence = FL.convergence(
        run.raw_scores.reindex(development), config.lam, config.epsilon, (4, 13, 26, 52)
    )
    # §11. 실행 전 캐시 감사. 덮지 못하는 주는 최신값으로 메우지 않고 보류한다.
    cache = AL.cache_audit(base, AS_OF)
    _write_json(output / "alfred_cache_audit.json", cache)

    alfred_summary: dict[str, Any] | None = None
    alfred_reason = ""
    if not gates["passed"]:
        alfred_reason = (
            "§12의 필수 게이트를 통과하지 못했습니다. §13에 따라 엄격 ALFRED를 돌리지 "
            "않고 재조정도 하지 않습니다."
        )
    elif not run_alfred:
        alfred_reason = "이 실행에서는 엄격 ALFRED를 건너뛰도록 요청했습니다."
    else:
        # 실행 직전에 동결 해시를 한 번 더 확인한다. 어긋나면 여기서 멈춘다.
        if verify_frozen(base, output) != digest:
            raise RuntimeError("엄격 ALFRED 직전에 동결 설정이 바뀌었습니다")
        path = AL.run_strict(
            base,
            config,
            FROZEN_BASELINE,
            AS_OF,
            checkpoint=output / "strict_alfred.checkpoint.csv",
            withhold=set(cache["weeks_to_withhold"]),
        )
        path.to_csv(output / "strict_alfred_path.csv")
        alfred_summary = AL.summarise(path, recession, digest)
        alfred_summary["cache_audit"] = {
            key: value for key, value in cache.items() if key != "weeks_to_withhold"
        }
        _write_json(output / "strict_alfred_summary.json", alfred_summary)

    adopted = bool(gates["passed"] and alfred_summary is not None)
    stop_reason = "" if gates["passed"] else _gate_failure_text(gates)

    summary: dict[str, Any] = {
        "model": config.document["model"],
        "version": config.document["version"],
        "status": "adopted" if adopted else "rejected_before_adoption",
        "frozen_config_sha256": digest,
        "previous_stopped_config": {
            "file": STOPPED_CONFIG_NAME,
            "sha256": stopped.sha256,
            "status": stopped.document["status"],
        },
        "conceptual_decision": config.document["conceptual_decision"],
        "provenance": _provenance(base, output),
        # §9. 표본의 역할을 이름으로 못박는다. 2001년과 금융위기는 개발구간 안에 있으므로
        # 표본 밖 검증이 아니다. 전체 표본 요약은 운영 요약일 뿐 순수 표본 밖 성적이 아니다.
        "sample_roles": {
            "development": {
                "window": [str(DEVELOPMENT[0].date()), str(DEVELOPMENT[1].date())],
                "role": "프런티어 선택에 쓴 구간. 2001년 침체와 금융위기를 포함한다.",
                "out_of_sample": False,
            },
            "holdout_latest_vintage": {
                "window": [str(validation[0].date()), str(validation[-1].date())],
                "role": "모수 선택에 쓰지 않은 구간. 2020년 침체를 포함한다.",
                "out_of_sample": True,
            },
            "full_causal_latest_vintage": {
                "window": [str(index[0].date()), str(index[-1].date())],
                "role": "개발구간과 홀드아웃을 합친 운영 요약. 순수 표본 밖 성적이 아니다.",
                "out_of_sample": False,
            },
            "strict_alfred": {
                "window": [str(AL.STRICT_START.date()), str(AS_OF.date())],
                "role": "실시간 빈티지 검증. NBER 침체 에피소드가 2020년 하나뿐이다.",
                "out_of_sample": True,
            },
        },
        "development": development_report,
        "latest_vintage_window": [str(index[0].date()), str(index[-1].date())],
        "latest_vintage": latest_report,
        "holdout_window": [str(validation[0].date()), str(validation[-1].date())],
        "holdout": holdout_report,
        "adoption_gates": gates,
        "confirmation_rule": {
            key: value for key, value in confirmation.items() if key != "transitions"
        },
        "false_positive_episodes": {
            key: value for key, value in false_positives.items() if key != "detail"
        },
        "recession_alert": {key: value for key, value in alerts.items() if key != "detail"},
        "alfred_cache": {key: value for key, value in cache.items() if key != "weeks_to_withhold"},
        "whipsaw_audit_summary": {key: value for key, value in audit.items() if key != "cases"},
        "recovery_slowdown_boundary": {
            key: value for key, value in boundary.items() if key != "moves"
        },
        "bounded_aggregate": {
            "level": A.summary(run.level_aggregate, recession),
            "momentum": A.summary(run.momentum_aggregate, recession),
        },
        "release_carry": {
            "domain_weeks_without_new_observation": round(
                float((~run.arrived).to_numpy().mean()), 6
            ),
            "domain_weeks_with_exact_zero_momentum": int(
                (run.momentum_scaled.abs() < 1e-12).to_numpy().sum()
            ),
        },
        "soft_filter": {
            "lambda": config.lam,
            "epsilon": config.epsilon,
            "confirmation_weeks": config.confirmation_weeks,
            "immediate_margin": config.immediate_margin,
            "ergodic": FL.is_ergodic(matrix),
            "minimum_entry": round(float(matrix.min()), 8),
            "matrix": [[round(float(value), 6) for value in row] for row in matrix],
        },
        "finite_memory_convergence": convergence,
        "strict_alfred": alfred_summary,
        "strict_alfred_reason": alfred_reason,
        "adopted": adopted,
        "stop_reason": stop_reason,
        "freshness_policy": config.freshness.to_dict(),
        "current_information_freshness": eligibility.to_dict(),
        "current": {
            "as_of": current["as_of_date"],
            "phase_status": current["phase_status"],
            "official_current_phase": current["official_current_phase"],
            "raw_current_phase": current["raw_current_phase"],
            "evidence_quality": current["evidence_quality"],
            "recession_alert": current["recession_alert"],
            "recession_alert_character": current["recession_alert_character"],
        },
    }
    _write_json(output / "validation_summary.json", summary)

    pd.DataFrame(
        {
            "official_phase": run.official_phase,
            "raw_phase": run.raw_phase,
            "filtered_winner": run.filtered_winner,
            "confirmation_pending": run.confirmation_pending,
            "recession_alert": run.alert_level,
            "recession_alert_character": run.alert_character,
            "activity_level": run.activity_level,
            "activity_momentum": run.activity_momentum,
            "negative_level_domains": run.negative_level_domains,
            "negative_momentum_domains": run.negative_momentum_domains,
            "confirming_domains": run.confirming_domains,
            "concentration": run.concentration,
            "contraction_evidence": run.contraction_detail["contraction_evidence"],
            "alert_evidence": run.contraction_detail["alert_evidence"],
            "recovery_evidence": run.recovery_detail["recovery_evidence"],
            "usrec": recession.astype(int),
        }
    ).to_csv(output / "weekly_state.csv")
    return FourPhaseResult(output, validated=True, adopted=adopted, stop_reason=stop_reason)


def main() -> int:
    result = build_report()
    print(f"산출물: {result.output_dir}")
    print(f"검증 실행: {result.validated} | 채택: {result.adopted}")
    if result.stop_reason:
        print("중단 사유:")
        print("  " + result.stop_reason)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
