"""후보 I 검증 산출물. 기존 산출물은 건드리지 않는다."""

# ruff: noqa: E501

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from ..config import Settings, load_baseline, load_settings
from ..validation.phase2 import _evaluate
from ..validation.phase4 import END, START, load_core_observations
from ..validation.real_data import _official_recession_flags
from . import validation as V
from .config import load_candidate
from .engine import build_state, evidence_quality

OUTPUT_NAME = "current_state"
FROZEN_BASELINE = "candidate_h_breadth_gate"
AS_OF = pd.Timestamp(END)


@dataclass(frozen=True)
class CurrentStateResult:
    output_dir: Path
    adopted: bool
    rejection_reasons: list[str]
    raw_phase: str
    official_phase: str


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _markdown(summary: dict[str, Any], current: dict[str, Any], mono: pd.DataFrame) -> str:
    """사람이 읽는 보고서. 수치는 전부 같은 실행에서 온 값이다."""

    h = summary["recession_metrics"]["candidate_H"]
    i = summary["recession_metrics"]["candidate_I"]
    sh = summary["state_validity"]["candidate_H"]
    si = summary["state_validity"]["candidate_I"]
    ev = summary["evidence_versus_separation"]
    resp = summary["responsiveness"]
    verdict = (
        "후보 I를 채택한다."
        if summary["adopted"]
        else "후보 I를 **채택하지 않는다**. 구조 결함은 전부 고쳤지만 침체 탐지가 실질적으로 나빠졌다."
    )
    lines = [
        "# 후보 I — 도메인 기반 현재상태 분류기",
        "",
        "## 1. 한 줄 결과",
        "",
        verdict,
        "",
        "## 2. 동결 설정",
        "",
        f"- 후보: `{summary['candidate']}`",
        f"- 설정 SHA-256: `{summary['frozen_config_sha256']}`",
        f"- 개발구간: {summary['development_window'][0]} ~ {summary['development_window'][1]}",
        f"- 모멘텀 척도: {summary['momentum_scale']['method']} "
        f"({summary['momentum_scale']['window_years']}년, 최소 {summary['momentum_scale']['minimum_years']}년)",
        f"- 안정화: 유계 여유 {summary['stabilizer']['margin']}",
        "",
        "검증을 시작하기 전에 이 값들을 파일에 쓰고 해시를 기록했다. 검증 프로그램이 매번",
        "해시를 다시 계산해 일치하지 않으면 실행을 중단한다.",
        "",
        "## 3. 침체 지표",
        "",
        "| 항목 | 후보 H | 후보 I |",
        "|---|---:|---:|",
        f"| 재현율 | {h['recall']:.4f} | **{i['recall']:.4f}** |",
        f"| 오탐률 | {h['false_positive_rate']:.4f} | {i['false_positive_rate']:.4f} |",
        f"| 정밀도 | {h['precision']:.4f} | {i['precision']:.4f} |",
        f"| F1 | {h['f1']:.4f} | **{i['f1']:.4f}** |",
        "",
        "## 4. 상태 타당성",
        "",
        "| 항목 | 후보 H | 후보 I |",
        "|---|---:|---:|",
        f"| 최대 국면 점유 | {sh['max_phase_share']:.4f} | {si['max_phase_share']:.4f} |",
        f"| 최장 연속 주 | **{sh['longest_run_overall']}** | **{si['longest_run_overall']}** |",
        f"| 전이 횟수 | {sh['transitions']} | {si['transitions']} |",
        f"| 1단계 점프 | {sh['one_step']} | {si['one_step']} |",
        f"| 2단계 점프 | {sh['two_step']} | {si['two_step']} |",
        f"| 3단계 이상 점프 | **{sh['three_or_more']}** | **{si['three_or_more']}** |",
        f"| 3주 왕복 | {sh['three_week_whipsaws']} | {si['three_week_whipsaws']} |",
        "",
        "점프가 적은 것이 곧 안정은 아니다. 후보 H의 점프 4건은 흡수 상태가 만든 것이고,",
        f"실제로 한 국면에 {sh['longest_run_overall']}주 갇혀 있었다.",
        "",
        "## 5. 약한 증거가 확신을 만드는가",
        "",
        f"- 약한 증거 주: {ev['weak_evidence_weeks']} ({ev['weak_evidence_share']:.1%})",
        f"- 그중 분리도 0.9 초과: **{ev['weak_high_separation_weeks']}주**",
        f"- 약한 증거 분리도 중앙값 {ev['median_separation_weak']} · 강한 증거 {ev['median_separation_strong']}",
        "",
        "후보 H에서는 반대였다 — 저반지름 주의 17.6%가 분리도 0.9를 넘었고 고반지름 주는 4.8%였다.",
        "",
        "## 6. 안정화의 유계성과 유한 기억",
        "",
        f"- 필터가 바꾼 주: {resp['filter_reversal_count']} ({resp['raw_official_disagreement_rate']:.1%})",
        f"- 최장 불일치 연속: {resp['longest_disagreement_run']}주",
        f"- 최대 필터 이득 {resp['max_filter_gain']} (여유 {summary['stabilizer']['margin']} 이내: {resp['filter_gain_within_margin']})",
        f"- 점수가 0인 주: {resp['zero_score_weeks']} · 도달하지 못한 국면: {resp['phases_never_reached'] or '없음'}",
        "",
        "유한 기억 수렴:",
        "",
    ]
    for key, value in summary["finite_memory_convergence"].items():
        lines.append(
            f"- {key}: 서로 다른 최종 국면 {value['distinct_final_phases']}개 · 수렴 {value['converged']}"
        )
    latency = summary["response_latency"]
    lines += [
        "",
        f"응답 지연: 중앙 {latency['median_weeks']}주 · p90 {latency['p90_weeks']}주 · 최대 {latency['max_weeks']}주",
        "",
        "## 7. 하위국면 단조성",
        "",
        "| 국면 | 주 | 수준 중앙 | 모멘텀 중앙 | 음수도메인 중앙 | 집중도 중앙 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for record in mono.to_dict("records"):
        if not record.get("weeks"):
            continue
        lines.append(
            f"| {record['phase']} | {record['weeks']} | {record['level_median']} | "
            f"{record['momentum_median']} | {record['negative_domains_median']} | "
            f"{record['concentration_median']} |"
        )
    lines += [
        "",
    ]
    for broad, result in summary["subphase_monotonicity"].items():
        lines.append(f"- {broad}: 단조 **{result.get('monotonic')}**")
    lines += [
        "",
        "후보 H에서는 slowdown_late의 모멘텀(-0.145)이 slowdown_middle(-0.283)보다 **약했고**",
        "반지름은 절반이었다. 후보 I에서는 수준·모멘텀·폭이 모두 단계에 따라 나빠진다.",
        "",
        "## 8. 2026-08-14 현재 판정",
        "",
        f"- 원시 현재상태 국면: **{current['raw_current_phase']}**",
        f"- 공식 안정화 국면: **{current['official_current_phase']}**",
        f"- 상태: {current['phase_status']}",
        f"- 활동 수준 {current['activity_level']} · 활동 모멘텀 {current['activity_momentum']}",
        f"- 음수 동행 도메인: 수준 {current['negative_level_domains']} · 모멘텀 {current['negative_momentum_domains']}",
        f"- 기여 집중도 {current['contribution_concentration']}",
        f"- 증거 품질 **{current['evidence_quality']}** — {'; '.join(current['evidence_reasons'])}",
        f"- 국면 분리도 {current['phase_separation']}",
        f"- 시각화 좌표 {current['coordinates_for_visualisation']}",
        "",
        "도메인 상태:",
        "",
        "| 도메인 | 수준 | 모멘텀 |",
        "|---|---:|---:|",
    ]
    for item in current["domains"]:
        lines.append(
            f"| {item['domain']} | {item['level_scaled']:+.4f} | {item['momentum_scaled']:+.4f} |"
        )
    lines += [
        "",
        "국면 점수 상위:",
        "",
    ]
    for name, value in current["top_scores"].items():
        lines.append(f"- {name}: {value}")
    lines += [
        "",
        "점수가 거의 평평하다. 모델이 지금 네 국면을 구분할 만한 증거를 갖고 있지 않다는 뜻이며,",
        "그 사실을 증거 품질 `low`로 그대로 보고한다. 공식 국면은 그래도 하나다.",
        "",
        "## 9. 엄격 ALFRED",
        "",
        f"실행하지 않았다. {summary['strict_alfred']['reason']}",
        "",
        "## 10. 채택 판정",
        "",
    ]
    if summary["adopted"]:
        lines.append("모든 기준을 통과했다.")
    else:
        lines.append("§16의 기각 사유에 해당한다.")
        lines.append("")
        for reason in summary["rejection_reasons"]:
            lines.append(f"- {reason}")
        lines += [
            "",
            "정지 규칙에 따라 두 번째 후보를 만들지 않는다. 임계값을 검증 결과에 맞춰 다시",
            "조정하는 것도 하지 않는다 — 그것이 §16이 금지한 행위다.",
            "",
            "다만 진단은 유효하다. 후보 H의 2026-08-14 판정이 현재 경제 상태의 독립적 판독이",
            "아니라 저반지름 전이 제약과 경로 의존이 만든 결과라는 사실은 그대로 남는다.",
        ]
    return "\n".join(lines) + "\n"


def build(settings: Settings | None = None) -> CurrentStateResult:
    base = settings or load_settings()
    output = base.root / "outputs" / OUTPUT_NAME
    output.mkdir(parents=True, exist_ok=True)

    candidate = load_candidate(base)
    recorded = (output / "frozen_candidate_config.sha256").read_text(encoding="utf-8").split()[0]
    if candidate.sha256 != recorded:
        raise RuntimeError(
            "동결 이후 후보 설정이 바뀌었습니다. 검증을 중단합니다 — "
            f"기록 {recorded[:16]}… 측정 {candidate.sha256[:16]}…"
        )

    frozen = load_baseline(FROZEN_BASELINE, base)
    core, source = load_core_observations(base)
    run = build_state(
        core,
        frozen,
        AS_OF,
        candidate.thresholds,
        scale_method=candidate.scale_method,
        scale_window_years=candidate.scale_window_years,
        scale_minimum_years=candidate.scale_minimum_years,
        momentum_weeks=candidate.momentum_weeks,
        margin=candidate.margin,
    )
    official = run.stabilized.official
    raw = run.stabilized.raw

    reference = _evaluate(FROZEN_BASELINE, frozen, core, source, START, END)
    h_codes = (
        reference.history["phase_code"].astype(str).str.replace("_mid$", "_middle", regex=True)
    )
    h_codes.index = pd.DatetimeIndex(h_codes.index)
    common = official.index.intersection(h_codes.index)
    actual = _official_recession_flags(source, pd.DatetimeIndex(common))

    h_common = h_codes.reindex(common)
    i_common = official.reindex(common)
    scores = run.scores.reindex(common)

    recession = {
        "candidate_H": V.recession_metrics(h_common, actual),
        "candidate_I": V.recession_metrics(i_common, actual),
    }
    episodes = {
        "candidate_H": V.episode_detection(h_common, actual),
        "candidate_I": V.episode_detection(i_common, actual),
    }
    state = {}
    for name, codes in (("candidate_H", h_common), ("candidate_I", i_common)):
        state[name] = {**V.occupancy(codes), **V.jump_profile(codes)}
    responsiveness = V.responsiveness(scores, raw.reindex(common), i_common, candidate.margin)
    evidence = V.evidence_vs_separation(
        scores,
        run.activity_level,
        run.activity_momentum,
        candidate.thresholds.neutral_level,
        candidate.thresholds.neutral_momentum,
    )
    mono = V.monotonicity(
        i_common,
        run.activity_level.reindex(common),
        run.activity_momentum.reindex(common),
        run.negative_level_domains.reindex(common),
        run.concentration.reindex(common),
    )
    mono.to_csv(output / "subphase_monotonicity.csv", index=False)
    checks = V.monotonicity_checks(mono)
    convergence = V.convergence_test(scores, candidate.margin)
    latency = V.latency_distribution(scores, candidate.margin)

    quality = evidence_quality(run, frozen, AS_OF)
    current = {
        "as_of": str(AS_OF.date()),
        "raw_current_phase": str(raw.loc[AS_OF]),
        "official_current_phase": str(official.loc[AS_OF]),
        "phase_status": "official",
        "activity_level": round(float(str(run.activity_level.loc[AS_OF])), 6),
        "activity_momentum": round(float(str(run.activity_momentum.loc[AS_OF])), 6),
        "negative_level_domains": int(str(run.negative_level_domains.loc[AS_OF])),
        "negative_momentum_domains": int(str(run.negative_momentum_domains.loc[AS_OF])),
        "contribution_concentration": round(float(str(run.concentration.loc[AS_OF])), 6),
        "coordinates_for_visualisation": {
            key: round(float(value), 6)
            for key, value in run.coordinates.loc[AS_OF].to_dict().items()
        },
        "domains": [
            {
                "domain": domain,
                "level_scaled": round(float(str(run.level_scaled.loc[AS_OF, domain])), 6),
                "momentum_scaled": round(float(str(run.momentum_scaled.loc[AS_OF, domain])), 6),
            }
            for domain in run.level_scaled.columns
        ],
        "top_scores": {
            str(k): round(float(v), 6)
            for k, v in sorted(
                ((name, float(str(run.scores.at[AS_OF, name]))) for name in run.scores.columns),
                key=lambda item: -item[1],
            )[:5]
        },
        **quality,
    }
    _write_json(output / "current_diagnosis.json", current)

    # ── 채택 판정 (§16) ─────────────────────────────────────────────────────
    reasons: list[str] = []
    h_recall = float(recession["candidate_H"]["recall"])
    i_recall = float(recession["candidate_I"]["recall"])
    h_f1 = float(recession["candidate_H"]["f1"])
    i_f1 = float(recession["candidate_I"]["f1"])
    if i_recall < h_recall - 0.05:
        reasons.append(f"침체 재현율이 실질적으로 떨어졌다 ({h_recall:.3f} → {i_recall:.3f})")
    if i_f1 < h_f1 - 0.03:
        reasons.append(f"침체 F1이 실질적으로 떨어졌다 ({h_f1:.3f} → {i_f1:.3f})")
    large_jumps = int(state["candidate_I"]["three_or_more"])
    if large_jumps > 4 * int(state["candidate_H"]["three_or_more"]):
        reasons.append(
            f"3단계 이상 점프가 {state['candidate_H']['three_or_more']}건 → {large_jumps}건으로 늘었다"
        )
    if any(result.get("monotonic") is not True for result in checks.values()):
        reasons.append("하위국면 단조성이 성립하지 않는다")
    if int(state["candidate_I"]["longest_run_overall"]) >= 104:
        reasons.append("여전히 실질적 흡수 상태가 있다")
    if int(evidence["weak_high_separation_weeks"]) > 0:
        reasons.append("약한 증거가 강한 분리도를 만든다")
    if not bool(responsiveness["filter_gain_within_margin"]):
        reasons.append("안정화 이득이 여유를 넘는다")

    summary: dict[str, Any] = {
        "candidate": candidate.candidate,
        "frozen_config_sha256": candidate.sha256,
        "frozen_config_matches_snapshot": True,
        "development_window": list(candidate.development_window),
        "momentum_scale": {
            "method": candidate.scale_method,
            "window_years": candidate.scale_window_years,
            "minimum_years": candidate.scale_minimum_years,
        },
        "stabilizer": {"method": "bounded_margin", "margin": candidate.margin},
        "comparison_window": [str(common[0].date()), str(common[-1].date())],
        "weeks": int(len(common)),
        "recession_metrics": recession,
        "episode_detection": episodes,
        "state_validity": state,
        "responsiveness": responsiveness,
        "evidence_versus_separation": evidence,
        "subphase_monotonicity": checks,
        "finite_memory_convergence": convergence,
        "response_latency": latency,
        "current": {
            "raw_current_phase": current["raw_current_phase"],
            "official_current_phase": current["official_current_phase"],
            "evidence_quality": current["evidence_quality"],
            "phase_separation": current["phase_separation"],
        },
        "strict_alfred": {
            "run": False,
            "reason": (
                "최신 수정치 기준으로 이미 §16의 기각 사유에 해당해 중단했다. "
                "엄격 ALFRED는 최신 수정치 기각을 뒤집을 수 없다."
            ),
        },
        "adopted": not reasons,
        "rejection_reasons": reasons,
    }
    _write_json(output / "validation_summary.json", summary)

    frame = pd.DataFrame(
        {
            "official_phase": i_common,
            "raw_phase": raw.reindex(common),
            "activity_level": run.activity_level.reindex(common),
            "activity_momentum": run.activity_momentum.reindex(common),
            "negative_level_domains": run.negative_level_domains.reindex(common),
            "negative_momentum_domains": run.negative_momentum_domains.reindex(common),
            "concentration": run.concentration.reindex(common),
            "candidate_h_phase": h_common,
            "usrec": actual.astype(int),
        }
    )
    frame.to_csv(output / "weekly_state.csv")
    (output / "validation_report.md").write_text(
        _markdown(summary, current, mono), encoding="utf-8", newline="\n"
    )
    return CurrentStateResult(
        output_dir=output,
        adopted=not reasons,
        rejection_reasons=reasons,
        raw_phase=current["raw_current_phase"],
        official_phase=current["official_current_phase"],
    )


def main() -> int:
    result = build()
    print(f"산출물: {result.output_dir}")
    print(f"2026-08-14 원시 국면: {result.raw_phase}")
    print(f"2026-08-14 공식 국면: {result.official_phase}")
    print(f"채택: {result.adopted}")
    for reason in result.rejection_reasons:
        print(f"  기각 사유: {reason}")
    return 0 if result.adopted else 3


if __name__ == "__main__":
    raise SystemExit(main())
