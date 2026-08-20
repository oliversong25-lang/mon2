"""4국면 개발 단계 산출물.

§18의 게이트 모순 조항에 따라 최신 수정치 검증 **전에** 중단했다. 그래서 여기서는
개발구간 진단과 게이트 실현가능성 증거를 남기고, 채택 판정은 하지 않는다.
"""

# ruff: noqa: E501

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..candidate_j import aggregate as A
from ..config import Settings, load_baseline, load_settings
from ..current_state import domains as D
from ..validation.phase4 import END, load_core_observations
from ..validation.real_data import _official_recession_flags
from . import contract as C
from . import evidence as E
from . import filter as FL
from .engine import build, load_config, verify_frozen

OUTPUT_NAME = "four_phase"
FROZEN_BASELINE = "candidate_h_breadth_gate"
AS_OF = pd.Timestamp(END)
DEVELOPMENT = (pd.Timestamp("1995-01-01"), pd.Timestamp("2012-12-31"))


@dataclass(frozen=True)
class FourPhaseResult:
    output_dir: Path
    validated: bool
    adopted: bool
    stop_reason: str


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def gate_feasibility(
    level: pd.Series,
    momentum: pd.Series,
    negative_level: pd.Series,
    recession: pd.Series,
    recall_floor: float,
    fpr_ceiling: float,
) -> dict[str, Any]:
    """게이트가 §9의 금지 조항과 양립 가능한지 전수로 확인한다.

    임계값을 고르기 위한 탐색이 아니다. "그런 규칙이 존재하는가"라는 메타 질문에만
    답하며, 여기서 나온 값은 모델에 넣지 않는다.
    """

    results: dict[str, Any] = {}
    truth = recession.reindex(level.index).fillna(False).astype(bool)
    for minimum in (0, 1, 2, 3):
        best: tuple[float, float, float, float] | None = None
        for a in np.arange(-2.0, 0.51, 0.05):
            for b in np.arange(-1.5, 1.01, 0.05):
                mask = (level <= a) & (momentum <= b) & (negative_level >= minimum)
                true_positive = int((mask & truth).sum())
                false_positive = int((mask & ~truth).sum())
                false_negative = int((~mask & truth).sum())
                true_negative = int((~mask & ~truth).sum())
                recall = true_positive / max(true_positive + false_negative, 1)
                rate = false_positive / max(false_positive + true_negative, 1)
                if recall >= recall_floor and rate <= fpr_ceiling:
                    if best is None or rate < best[1]:
                        best = (recall, rate, float(a), float(b))
        results[f"negative_level_domains_at_least_{minimum}"] = (
            {
                "feasible": True,
                "best_recall": round(best[0], 6),
                "best_false_positive_rate": round(best[1], 6),
                "level_threshold": round(best[2], 4),
                "momentum_threshold": round(best[3], 4),
            }
            if best
            else {"feasible": False}
        )
    return results


def _phase_metrics(codes: pd.Series, truth: pd.Series) -> dict[str, Any]:
    predicted = codes.eq("contraction")
    true_positive = int((predicted & truth).sum())
    false_positive = int((predicted & ~truth).sum())
    false_negative = int((~predicted & truth).sum())
    true_negative = int((~predicted & ~truth).sum())
    recall = true_positive / max(true_positive + false_negative, 1)
    precision = true_positive / max(true_positive + false_positive, 1)
    order = {name: index for index, name in enumerate(C.PHASES)}
    one = two = 0
    for previous, current in zip(codes.iloc[:-1], codes.iloc[1:], strict=True):
        if previous == current:
            continue
        distance = abs(order[str(previous)] - order[str(current)])
        if min(distance, 4 - distance) >= 2:
            two += 1
        else:
            one += 1
    values = list(codes)
    whipsaws = sum(
        1
        for index in range(2, len(values))
        if values[index] == values[index - 2] and values[index] != values[index - 1]
    )
    runs: list[int] = []
    current, length = values[0], 1
    for value in values[1:]:
        if value == current:
            length += 1
        else:
            runs.append(length)
            current, length = value, 1
    runs.append(length)
    return {
        "recall": round(recall, 6),
        "false_positive_rate": round(false_positive / max(false_positive + true_negative, 1), 6),
        "precision": round(precision, 6),
        "f1": round(2 * precision * recall / max(precision + recall, 1e-12), 6),
        "one_step_transitions": one,
        "two_step_transitions": two,
        "three_week_whipsaws": whipsaws,
        "longest_run_weeks": max(runs),
        "phase_share": {
            k: round(float(v), 6) for k, v in codes.value_counts(normalize=True).items()
        },
        "phases_reached": sorted(set(codes.unique())),
    }


def current_output(run: Any, settings: Settings, week: pd.Timestamp) -> dict[str, Any]:
    """§3 계약을 따르는 사용자 대면 출력."""

    thresholds = run.thresholds
    level = float(str(run.activity_level.loc[week]))
    momentum = float(str(run.activity_momentum.loc[week]))
    phase = str(run.official_phase.loc[week])
    stance = E.domain_stance(
        run.level_scaled.loc[week], run.momentum_scaled.loc[week], phase, thresholds
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
    stale = [domain for domain, age in freshness.items() if age > 8.0]
    if stale:
        reasons.append(f"stale domains ({', '.join(sorted(stale))})")
    if ordered[0] - ordered[1] < 0.10:
        reasons.append("phase scores are not strongly separated")
    if str(run.raw_phase.loc[week]) != phase:
        reasons.append("raw and filtered readings disagree")
    quality = "high" if not reasons else ("medium" if len(reasons) == 1 else "low")

    contraction = float(str(run.contraction_detail.at[week, "contraction_evidence"]))
    alert = (
        "active"
        if phase == "contraction"
        else (
            "elevated"
            if contraction >= thresholds.contraction_entry * 0.8
            else ("watch" if contraction >= thresholds.contraction_entry * 0.5 else "none")
        )
    )
    order = {name: index for index, name in enumerate(C.PHASES)}
    forward = C.PHASES[(order[phase] + 1) % 4]
    backward = C.PHASES[(order[phase] - 1) % 4]
    adjacent = {name: scores[name] for name in (forward, backward)}
    leader = max(adjacent, key=lambda name: adjacent[name])
    watch = f"toward_{leader}" if adjacent[leader] > scores[phase] * 0.6 else "none"

    payload = {
        "official_current_phase": phase,
        "phase_status": "official",
        "phase_separation": round(ordered[0] - ordered[1], 6),
        "evidence_quality": quality,
        "activity_level": round(level, 6),
        "activity_momentum": round(momentum, 6),
        "domain_breadth": {
            "negative_level_domains": int(str(run.negative_level_domains.loc[week])),
            "negative_momentum_domains": int(str(run.negative_momentum_domains.loc[week])),
            "positive_momentum_domains": int(str(run.positive_momentum_domains.loc[week])),
            "coincident_domains": len(D.COINCIDENT_DOMAINS),
        },
        "contribution_concentration": round(concentration, 6),
        "supporting_domains": [k for k, v in stance.items() if v == "supports"],
        "opposing_domains": [k for k, v in stance.items() if v == "opposes"],
        "mixed_domains": [k for k, v in stance.items() if v == "mixed"],
        "transition_watch": watch,
        "recession_alert": alert,
        "as_of_date": str(pd.Timestamp(str(week)).date()),
        "latest_observation_by_domain": {k: round(v, 1) for k, v in freshness.items()},
        "known_limitations": [
            "이 모델은 검증을 완료하지 않았고 채택되지 않았다. 게이트 모순으로 최신 수정치 검증 전에 중단했다.",
            "official_current_phase는 현재상태 측정이며 예측이 아니다.",
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
        "coordinates_diagnostic_only": {
            key: round(float(value), 6)
            for key, value in run.coordinates.loc[week].to_dict().items()
        },
    }
    C.validate(payload)
    return payload


def build_report(settings: Settings | None = None) -> FourPhaseResult:
    base = settings or load_settings()
    output = base.root / "outputs" / OUTPUT_NAME
    output.mkdir(parents=True, exist_ok=True)
    digest = verify_frozen(base, output)
    config = load_config(base)

    frozen = load_baseline(FROZEN_BASELINE, base)
    core, source = load_core_observations(base)
    run = build(core, frozen, AS_OF, config)
    index = run.official_phase.index
    actual = _official_recession_flags(source, pd.DatetimeIndex(index))
    development = index[(index >= DEVELOPMENT[0]) & (index <= DEVELOPMENT[1])]

    development_metrics = _phase_metrics(
        run.official_phase.reindex(development),
        actual.reindex(development).fillna(False).astype(bool),
    )
    feasibility = gate_feasibility(
        run.activity_level.reindex(development),
        run.activity_momentum.reindex(development),
        run.negative_level_domains.reindex(development),
        actual.reindex(development),
        float(config.gates["recession_recall_minimum"]),
        float(config.gates["false_positive_rate_maximum"]),
    )
    blocked = [
        key
        for key in ("negative_level_domains_at_least_2", "negative_level_domains_at_least_3")
        if not feasibility[key]["feasible"]
    ]
    stop_reason = ""
    if blocked:
        stop_reason = (
            "§9는 한 동행 도메인 단독 침체 선언을 금지한다. 그 금지를 지키려면 음수 동행 "
            "도메인 2개 이상을 요구해야 하는데, 개발구간에서 그 조건 아래 재현율 "
            f"{config.gates['recession_recall_minimum']} 이상과 오탐률 "
            f"{config.gates['false_positive_rate_maximum']} 이하를 동시에 만족하는 규칙이 "
            "존재하지 않는다. 임계값 선택의 문제가 아니라 정의와 게이트의 모순이다."
        )

    matrix = FL.transition_matrix(config.lam, config.epsilon)
    convergence = FL.convergence(
        run.raw_scores.reindex(development), config.lam, config.epsilon, (4, 13, 26, 52)
    )
    disagreement = run.raw_phase.reindex(development).ne(run.official_phase.reindex(development))
    longest = streak = 0
    for value in disagreement:
        streak = streak + 1 if value else 0
        longest = max(longest, streak)

    week = AS_OF if AS_OF in index else index[-1]
    current = current_output(run, base, week)
    _write_json(output / "current_reading.json", current)
    _write_json(output / "country_contract.json", C.country_schema())

    level_summary = A.summary(run.level_aggregate, actual)
    momentum_summary = A.summary(run.momentum_aggregate, actual)
    summary: dict[str, Any] = {
        "model": config.document["model"],
        "status": "development_locked_not_validated",
        "frozen_config_sha256": digest,
        "development_window": [str(DEVELOPMENT[0].date()), str(DEVELOPMENT[1].date())],
        "development_weeks": int(len(development)),
        "development_metrics": development_metrics,
        "gate_feasibility": feasibility,
        "gate_contradiction": bool(blocked),
        "stop_reason": stop_reason,
        "adoption_gates": config.gates,
        "bounded_aggregate": {"level": level_summary, "momentum": momentum_summary},
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
            "ergodic": FL.is_ergodic(matrix),
            "minimum_entry": round(float(matrix.min()), 8),
            "matrix": [[round(float(v), 6) for v in row] for row in matrix],
        },
        "finite_memory_convergence": convergence,
        "raw_official_disagreement_rate": round(float(disagreement.mean()), 6),
        "longest_disagreement_run": int(longest),
        "latest_vintage_validation_run": False,
        "strict_alfred_run": False,
        "strict_alfred_reason": (
            "§19에 따라 최신 수정치 필수 게이트를 통과해야 실행한다. 게이트 모순으로 "
            "검증 자체를 시작하지 않았다."
        ),
        "adopted": False,
        "current_reading_is_unvalidated": True,
        "current": {
            "as_of": current["as_of_date"],
            "official_current_phase": current["official_current_phase"],
            "raw_current_phase": current["raw_current_phase"],
            "evidence_quality": current["evidence_quality"],
        },
    }
    _write_json(output / "validation_summary.json", summary)

    pd.DataFrame(
        {
            "official_phase": run.official_phase,
            "raw_phase": run.raw_phase,
            "activity_level": run.activity_level,
            "activity_momentum": run.activity_momentum,
            "negative_level_domains": run.negative_level_domains,
            "negative_momentum_domains": run.negative_momentum_domains,
            "concentration": run.concentration,
            "contraction_evidence": run.contraction_detail["contraction_evidence"],
            "recovery_evidence": run.recovery_detail["recovery_evidence"],
            "usrec": actual.astype(int),
        }
    ).to_csv(output / "weekly_state.csv")
    return FourPhaseResult(output, validated=False, adopted=False, stop_reason=stop_reason)


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
