"""후보 J 검증. 기존 산출물은 건드리지 않고 새 디렉터리에만 쓴다."""

# ruff: noqa: E501

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from ..config import Settings, load_baseline, load_settings
from ..current_state import validation as V
from ..current_state.config import load_candidate as load_candidate_i
from ..current_state.engine import build_state as build_candidate_i
from ..validation.phase2 import _evaluate
from ..validation.phase4 import END, START, load_core_observations
from ..validation.real_data import _official_recession_flags
from . import aggregate as A
from . import filters as F
from . import hierarchy as H
from .engine import build, load_config, verify_frozen

OUTPUT_NAME = "candidate_j"
FROZEN_BASELINE = "candidate_h_breadth_gate"
AS_OF = pd.Timestamp(END)


@dataclass(frozen=True)
class CandidateJResult:
    output_dir: Path
    adopted: bool
    failed_gates: list[str]
    official_phase: str


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _state_metrics(codes: pd.Series) -> dict[str, Any]:
    return {**V.occupancy(codes), **V.jump_profile(codes)}


def evaluate_gates(
    config_gates: dict[str, Any],
    recession: dict[str, Any],
    state: dict[str, Any],
    episodes: dict[str, Any],
    monotonic: dict[str, Any],
    reachable: int,
    minimum_score: float,
) -> list[str]:
    """검증 전에 잠근 게이트를 하나씩 판정한다. 평균 뒤에 숨기지 않는다."""

    failures: list[str] = []
    if recession["recall"] < float(config_gates["recession_recall_minimum"]):
        failures.append(
            f"침체 재현율 {recession['recall']:.4f} < 하한 {config_gates['recession_recall_minimum']}"
        )
    if recession["false_positive_rate"] > float(config_gates["false_positive_rate_maximum"]):
        failures.append(
            f"오탐률 {recession['false_positive_rate']:.4f} > 상한 {config_gates['false_positive_rate_maximum']}"
        )
    if state["three_or_more"] > int(config_gates["three_step_or_larger_jumps_maximum"]):
        failures.append(
            f"3단계 이상 점프 {state['three_or_more']}건 > 상한 {config_gates['three_step_or_larger_jumps_maximum']}건"
        )
    if state["longest_run_overall"] > int(config_gates["longest_run_maximum_weeks"]):
        failures.append(
            f"최장 연속 {state['longest_run_overall']}주 > 상한 {config_gates['longest_run_maximum_weeks']}주"
        )
    if episodes["late_2019_confirmed_false_positive_weeks"] > int(
        config_gates["late_2019_confirmed_contraction_maximum"]
    ):
        failures.append("2019년 말 확인 수축이 있다")
    if episodes["post_2022_confirmed_false_positive_weeks"] > int(
        config_gates["post_2022_confirmed_contraction_maximum"]
    ):
        failures.append("2022년 이후 확인 수축 오탐이 있다")
    if reachable < 12:
        failures.append(f"도달 가능한 국면이 {reachable}개뿐이다")
    if minimum_score <= 0:
        failures.append("정확히 0인 전이 지지가 있다")
    if any(result.get("monotonic") is not True for result in monotonic.values()):
        broken = [k for k, v in monotonic.items() if v.get("monotonic") is not True]
        failures.append(f"하위국면 단조성 실패: {', '.join(broken)}")
    return failures


def _markdown(summary: dict[str, Any]) -> str:
    """사람이 읽는 보고서. 수치는 전부 같은 실행에서 온 값이다."""

    rec = summary["recession_metrics"]
    state = summary["state_validity"]
    current = summary["current"]
    lines = [
        "# 후보 J — 계층 현재상태 분류기",
        "",
        "## 1. 한 줄 결과",
        "",
        (
            "후보 J를 채택한다."
            if summary["adopted"]
            else "후보 J를 **채택하지 않는다**. 구조 게이트는 전부 통과했지만 침체 탐지와 "
            "12국면 점프 게이트를 통과하지 못했다."
        ),
        "",
        "## 2. 동결 설정",
        "",
        f"- 후보: `{summary['candidate']}`",
        f"- SHA-256: `{summary['frozen_config_sha256']}`",
        f"- 비교 창: {summary['comparison_window'][0]} ~ {summary['comparison_window'][1]} ({summary['weeks']}주)",
        "",
        "## 3. 침체 지표",
        "",
        "| 항목 | 후보 H | 후보 I | 후보 J |",
        "|---|---:|---:|---:|",
    ]
    for key, label in (
        ("recall", "재현율"),
        ("false_positive_rate", "오탐률"),
        ("precision", "정밀도"),
        ("f1", "F1"),
    ):
        lines.append(
            f"| {label} | {rec['candidate_H'][key]:.4f} | {rec['candidate_I'][key]:.4f} | "
            f"{rec['candidate_J'][key]:.4f} |"
        )
    lines += [
        "",
        "## 4. 상태 타당성",
        "",
        "| 항목 | 후보 H | 후보 I | 후보 J |",
        "|---|---:|---:|---:|",
    ]
    for key, label in (
        ("max_phase_share", "최대 국면 점유"),
        ("longest_run_overall", "최장 연속 주"),
        ("transitions", "전이"),
        ("one_step", "1단계"),
        ("two_step", "2단계"),
        ("three_or_more", "3단계 이상"),
        ("three_week_whipsaws", "3주 왕복"),
    ):
        lines.append(
            f"| {label} | {state['candidate_H'][key]} | {state['candidate_I'][key]} | "
            f"{state['candidate_J'][key]} |"
        )
    soft = summary["soft_filter"]
    lines += [
        "",
        "## 5. 거리 인식 소프트 필터",
        "",
        f"- lambda_major {soft['lambda_major']} · lambda_subphase {soft['lambda_subphase']} · epsilon {soft['epsilon']}",
        f"- 대국면 행렬 에르고딕 {soft['major_matrix_ergodic']} (최소 성분 {soft['major_matrix_minimum']})",
        f"- 하위국면 행렬 에르고딕 {soft['subphase_matrix_ergodic']} (최소 성분 {soft['subphase_matrix_minimum']})",
        f"- 도달 가능한 국면 {summary['phases_reachable']} / 12 · 최소 대국면 점수 {summary['minimum_major_score']}",
        f"- 원시-공식 대국면 불일치 {summary['raw_official_major_disagreement_rate']:.4f} · 최장 {summary['longest_major_disagreement_run']}주",
        "",
        "유한 기억 수렴:",
        "",
    ]
    for key, value in summary["finite_memory_convergence"].items():
        lines.append(
            f"- {key}: 서로 다른 최종 상태 {value['distinct_final_states']}개 · 수렴 {value['converged']}"
        )
    carry = summary["release_carry"]
    level = summary["bounded_aggregate"]["level"]
    momentum = summary["bounded_aggregate"]["momentum"]
    lines += [
        "",
        "## 6. 유계 등가중 총량",
        "",
        f"- 수준: 상한 적용 {level['capped_weeks']}주 ({level['capped_share']:.1%}), "
        f"침체 주 중 {level['capped_share_in_recession']:.1%} · 평상시 {level['capped_share_normal']:.1%}",
        f"  최대 도메인 {level['largest_domain_before_max']} → {level['largest_domain_after_max']}, "
        f"무상한 총량 최저 {level['aggregate_uncapped_min']} → {level['aggregate_bounded_min']}",
        f"- 모멘텀: 상한 적용 {momentum['capped_weeks']}주 ({momentum['capped_share']:.1%}), "
        f"최대 도메인 {momentum['largest_domain_before_max']} → {momentum['largest_domain_after_max']}",
        "",
        "## 7. 월간 발표 0 처리",
        "",
        f"- 새 관측이 없던 도메인-주: {carry['domain_weeks_without_new_observation']:.1%}",
        f"- 모멘텀이 정확히 0인 도메인-주: **{carry['domain_weeks_with_exact_zero_momentum']}건**",
        "",
        "후보 I는 발표 사이의 8주 차분이 우연히 0이 되면 그것을 '중립 모멘텀'으로 내보냈다.",
        "후보 J는 새 관측이 온 주에만 추정을 갱신하고 사이에는 마지막 추정을 유지한다.",
        "",
        "## 8. 하위국면 단조성",
        "",
    ]
    for broad, result in summary["subphase_monotonicity"].items():
        lines.append(f"- {broad}: 단조 **{result.get('monotonic')}**")
    lines += [
        "",
        "## 9. 2026-08-14 현재 판정",
        "",
        f"- 원시 대국면 **{current['raw_major_phase']}** → 공식 대국면 **{current['official_major_phase']}**",
        f"- 원시 하위국면 **{current['raw_subphase']}** → 공식 하위국면 **{current['official_subphase']}**",
        f"- **공식 현재 국면: {current['official_current_phase']}** (상태 {current['phase_status']})",
        f"- 활동 수준 {current['activity_level']} · 활동 모멘텀 {current['activity_momentum']}",
        f"- 음수 동행 도메인: 수준 {current['negative_level_domains']} · 모멘텀 {current['negative_momentum_domains']}",
        f"- 기여 집중도 {current['contribution_concentration']} · 국면 분리도 {current['phase_separation']}",
        f"- 대국면 점수 {current['major_scores']}",
        f"- 침체 증거 {current['contraction_evidence']['contraction_evidence']} "
        f"(넓은 수준 경로 {current['contraction_evidence']['broad_level_route']}, "
        f"급속 악화 경로 {current['contraction_evidence']['rapid_deterioration_route']})",
        f"- 필터 효과 {current['filter_effect']}",
        f"- **legacy_recession_alert: {current['legacy_recession_alert']}** (후보 H의 동결 결과, 공식 현재 국면이 아니다)",
        "",
        "| 도메인 | 수준 | 모멘텀 | 발표 후 경과 |",
        "|---|---:|---:|---:|",
    ]
    for item in current["domains"]:
        lines.append(
            f"| {item['domain']} | {item['level']:+.4f} | {item['momentum']:+.4f} | {item['weeks_since_release']:.0f}주 |"
        )
    lines += [
        "",
        "## 10. 엄격 ALFRED",
        "",
        (
            "실행하지 않았다. " + summary["strict_alfred"]["reason"]
            if not summary["strict_alfred"]["run"]
            else "실행했다."
        ),
        "",
        "## 11. 채택 판정",
        "",
    ]
    if summary["adopted"]:
        lines.append("모든 잠근 게이트를 통과했다.")
    else:
        lines.append("잠근 게이트 중 다음이 실패했다.")
        lines.append("")
        for gate in summary["failed_gates"]:
            lines.append(f"- {gate}")
        lines += [
            "",
            "§18의 정지 규칙에 따라 후보 K를 만들지 않고, 후보 J의 동결 모수도 바꾸지 않는다.",
            "미국 v1 공식 현재국면 모델은 **미채택**으로 보고한다.",
        ]
    return "\n".join(lines) + "\n"


def build_report(settings: Settings | None = None) -> CandidateJResult:
    base = settings or load_settings()
    output = base.root / "outputs" / OUTPUT_NAME
    output.mkdir(parents=True, exist_ok=True)
    digest = verify_frozen(base, output)
    config = load_config(base)

    frozen = load_baseline(FROZEN_BASELINE, base)
    core, source = load_core_observations(base)
    run = build(core, frozen, AS_OF, config)
    official = run.official_current_phase

    # ── 비교 대상: 후보 H와 후보 I ──────────────────────────────────────────
    reference = _evaluate(FROZEN_BASELINE, frozen, core, source, START, END)
    h_codes = (
        reference.history["phase_code"].astype(str).str.replace("_mid$", "_middle", regex=True)
    )
    h_codes.index = pd.DatetimeIndex(h_codes.index)
    candidate_i = load_candidate_i(base)
    run_i = build_candidate_i(
        core,
        frozen,
        AS_OF,
        candidate_i.thresholds,
        scale_method=candidate_i.scale_method,
        scale_window_years=candidate_i.scale_window_years,
        scale_minimum_years=candidate_i.scale_minimum_years,
        momentum_weeks=candidate_i.momentum_weeks,
        margin=candidate_i.margin,
    )
    i_codes = run_i.stabilized.official

    common = official.index.intersection(h_codes.index).intersection(i_codes.index)
    actual = _official_recession_flags(source, pd.DatetimeIndex(common))
    codes = {
        "candidate_H": h_codes.reindex(common),
        "candidate_I": i_codes.reindex(common),
        "candidate_J": official.reindex(common),
    }

    recession = {name: V.recession_metrics(value, actual) for name, value in codes.items()}
    episodes = {name: V.episode_detection(value, actual) for name, value in codes.items()}
    state = {name: _state_metrics(value) for name, value in codes.items()}

    monotonic_frame = V.monotonicity(
        codes["candidate_J"],
        run.activity_level.reindex(common),
        run.activity_momentum.reindex(common),
        run.negative_level_domains.reindex(common),
        run.concentration.reindex(common),
    )
    monotonic_frame.to_csv(output / "subphase_monotonicity.csv", index=False)
    monotonic = V.monotonicity_checks(monotonic_frame)

    major_matrix = F.transition_matrix(4, config.lambda_major, config.epsilon)
    sub_matrix = F.transition_matrix(3, config.lambda_subphase, config.epsilon, cyclic=False)
    convergence = F.convergence(
        run.major_scores.reindex(common), config.lambda_major, config.epsilon, (4, 13, 26, 52)
    )
    disagreement = run.raw_major_phase.reindex(common).ne(run.official_major_phase.reindex(common))
    longest_disagreement = streak = 0
    for value in disagreement:
        streak = streak + 1 if value else 0
        longest_disagreement = max(longest_disagreement, streak)

    level_summary = A.summary(run.level_aggregate, actual)
    momentum_summary = A.summary(run.momentum_aggregate, actual)

    reachable = len(set(codes["candidate_J"].unique()))
    minimum_score = float(run.major_scores.reindex(common).min().min())
    failures = evaluate_gates(
        config.gates,
        recession["candidate_J"],
        state["candidate_J"],
        episodes["candidate_J"],
        monotonic,
        reachable,
        minimum_score,
    )

    week = AS_OF if AS_OF in official.index else official.index[-1]
    scores = {name: float(str(run.major_scores.at[week, name])) for name in H.MAJORS}
    ordered = sorted(scores.values(), reverse=True)
    evidence = H.contraction_evidence(
        float(str(run.activity_level.loc[week])),
        float(str(run.activity_momentum.loc[week])),
        int(str(run.negative_level_domains.loc[week])),
        int(str(run.negative_momentum_domains.loc[week])),
        float(str(run.level_scaled.loc[week, "labor_stress"])),
        config.thresholds,
    )
    legacy = str(h_codes.loc[week]) if week in h_codes.index else ""
    current: dict[str, Any] = {
        "as_of": str(pd.Timestamp(str(week)).date()),
        "raw_major_phase": str(run.raw_major_phase.loc[week]),
        "official_major_phase": str(run.official_major_phase.loc[week]),
        "raw_subphase": str(run.raw_subphase.loc[week]),
        "official_subphase": str(run.official_subphase.loc[week]),
        "official_current_phase": str(official.loc[week]),
        "phase_status": "official",
        "activity_level": round(float(str(run.activity_level.loc[week])), 6),
        "activity_momentum": round(float(str(run.activity_momentum.loc[week])), 6),
        "negative_level_domains": int(str(run.negative_level_domains.loc[week])),
        "negative_momentum_domains": int(str(run.negative_momentum_domains.loc[week])),
        "contribution_concentration": round(float(str(run.concentration.loc[week])), 6),
        "phase_separation": round(float(ordered[0] - ordered[1]), 6),
        "major_scores": {name: round(value, 6) for name, value in scores.items()},
        "contraction_evidence": {k: round(float(v), 6) for k, v in evidence.items()},
        "domains": [
            {
                "domain": domain,
                "level": round(float(str(run.level_scaled.loc[week, domain])), 6),
                "momentum": round(float(str(run.momentum_scaled.loc[week, domain])), 6),
                "weeks_since_release": round(
                    float(str(run.weeks_since_release.loc[week, domain])), 1
                ),
            }
            for domain in run.level_scaled.columns
        ],
        "coordinates_for_visualisation": {
            key: round(float(value), 6)
            for key, value in run.coordinates.loc[week].to_dict().items()
        },
        "filter_effect": {
            "raw_major_differs": bool(
                str(run.raw_major_phase.loc[week]) != str(run.official_major_phase.loc[week])
            ),
            "raw_subphase_differs": bool(
                str(run.raw_subphase.loc[week]) != str(run.official_subphase.loc[week])
            ),
        },
        "legacy_recession_alert": legacy,
    }
    _write_json(output / "current_diagnosis.json", current)

    summary: dict[str, Any] = {
        "candidate": config.document["candidate"],
        "frozen_config_sha256": digest,
        "comparison_window": [str(common[0].date()), str(common[-1].date())],
        "weeks": int(len(common)),
        "acceptance_gates": config.gates,
        "recession_metrics": recession,
        "episode_detection": episodes,
        "state_validity": state,
        "bounded_aggregate": {"level": level_summary, "momentum": momentum_summary},
        "release_carry": {
            "domain_weeks_without_new_observation": round(
                float((~run.arrived.reindex(common)).to_numpy().mean()), 6
            ),
            "domain_weeks_with_exact_zero_momentum": int(
                (run.momentum_scaled.reindex(common).abs() < 1e-12).to_numpy().sum()
            ),
        },
        "soft_filter": {
            "lambda_major": config.lambda_major,
            "lambda_subphase": config.lambda_subphase,
            "epsilon": config.epsilon,
            "major_matrix_ergodic": F.is_ergodic(major_matrix),
            "subphase_matrix_ergodic": F.is_ergodic(sub_matrix),
            "major_matrix_minimum": round(float(major_matrix.min()), 8),
            "subphase_matrix_minimum": round(float(sub_matrix.min()), 8),
        },
        "finite_memory_convergence": convergence,
        "raw_official_major_disagreement_rate": round(float(disagreement.mean()), 6),
        "longest_major_disagreement_run": int(longest_disagreement),
        "subphase_monotonicity": monotonic,
        "phases_reachable": reachable,
        "minimum_major_score": minimum_score,
        "current": current,
        "strict_alfred": {"run": False, "reason": ""},
        "adopted": not failures,
        "failed_gates": failures,
    }
    if failures:
        summary["strict_alfred"]["reason"] = (
            "§14-C에 따라 최신 수정치 필수 게이트가 실패하면 엄격 ALFRED를 돌리지 않는다."
        )
    _write_json(output / "validation_summary.json", summary)

    pd.DataFrame(
        {
            "official_current_phase": codes["candidate_J"],
            "official_major_phase": run.official_major_phase.reindex(common),
            "raw_major_phase": run.raw_major_phase.reindex(common),
            "official_subphase": run.official_subphase.reindex(common),
            "activity_level": run.activity_level.reindex(common),
            "activity_momentum": run.activity_momentum.reindex(common),
            "negative_level_domains": run.negative_level_domains.reindex(common),
            "negative_momentum_domains": run.negative_momentum_domains.reindex(common),
            "concentration": run.concentration.reindex(common),
            "candidate_h_phase": codes["candidate_H"],
            "candidate_i_phase": codes["candidate_I"],
            "usrec": actual.astype(int),
        }
    ).to_csv(output / "weekly_state.csv")

    (output / "validation_report.md").write_text(_markdown(summary), encoding="utf-8", newline="\n")
    return CandidateJResult(output, not failures, failures, current["official_current_phase"])


def main() -> int:
    result = build_report()
    print(f"산출물: {result.output_dir}")
    print(f"2026-08-14 공식 국면: {result.official_phase}")
    print(f"채택: {result.adopted}")
    for gate in result.failed_gates:
        print(f"  게이트 실패: {gate}")
    return 0 if result.adopted else 3


if __name__ == "__main__":
    raise SystemExit(main())
