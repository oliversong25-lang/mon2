"""상태 의미론 감사 실행기.

    python -m business_cycle.state_semantics

산출물은 격리 경로 ``outputs/state_semantics/``에만 쓴다.
"""

from __future__ import annotations

import json
from typing import Any

import pandas as pd

from ..config import load_settings
from .classify import CLASSES, REASON_CODES
from .decide import CLASSIFICATIONS, FORBIDDEN_CLASSIFICATION
from .review import ALFRED, LATEST, OUTPUT_NAME, run, write_json


def _report(payload: dict[str, Any]) -> str:
    decision = payload["decision"]
    contract = payload["semantic_contract"]
    path = payload["path_2001"]
    now = payload["current_state_semantics"]
    lines = [
        "# 4국면 v1.1 상태 의미론 감사",
        "",
        f"**결정: `{decision['classification']}`**",
        "",
        f"사유: {decision['reason']}",
        "",
        "## 먼저 적는 것",
        "",
        f"- 높은 증거 의미 충돌 **{decision['high_evidence_semantic_conflicts']}건** (두 표본 합).",
        f"- §5 표에서 문자 그대로 어긋난 항목: "
        f"{decision['hard_rules_literally_failing'] or '없음'}. "
        f"§10의 잠금 조건 목록에는 들어 있지 않지만 숨기지 않는다. 모든 §5 항목을 "
        f"게이트로 걸었다면 결론은 "
        f"`{decision['counterfactual_if_every_section_five_rule_gated']}`가 된다.",
        f"- 현재 공식 국면 **{now['official_current_phase']}**, 증거 품질 "
        f"**{now['evidence_quality']}**. 품질을 올리지 않았다.",
        "",
        "## 국면 순서는 게이트가 아니다",
        "",
        contract["phase_order_note"],
        "",
        "묻는 질문은 '모델이 항상 recovery → expansion → slowdown → contraction 순으로",
        "돌았는가'가 아니라 '그 주의 증거가 그 라벨을 뒷받침했는가'다. 순서 강제는 앞서",
        "진단한 경로 의존과 끈적한 상태 결함을 규칙으로 되살린다.",
        "",
        "## 동결 의미 계약",
        "",
        contract["common"]["score_construction"],
        "",
        "| 국면 | 수준 | 모멘텀 | 폭 | 심각도 |",
        "|---|---|---|---|---|",
    ]
    for name, entry in contract["phases"].items():
        lines.append(
            f"| {name} | {entry['level_condition']} | {entry['momentum_condition']} | "
            f"{entry['breadth_requirement']} | {entry['severity_requirement']} |"
        )
    lines += [
        "",
        f"**{contract['sub_normal_expansion_mechanism']}**",
        "",
        "## 표본별 의미 분류",
        "",
        "| 표본 | 적격 주 | 지지 | 중립대 유지 | 확인 지연 | 낮은 증거 | 충돌 | 높은 증거 충돌 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for name in (LATEST, ALFRED):
        entry = payload["samples"][name]
        counts = entry["class_counts"]
        lines.append(
            f"| {name} | {entry['eligible_weeks']} | {counts['semantically_supported']} | "
            f"{counts['neutral_band_retention']} | {counts['bounded_confirmation_lag']} | "
            f"{counts['low_evidence_ambiguous']} | {counts['semantic_conflict']} | "
            f"**{entry['high_evidence_semantic_conflicts']}** |"
        )
    lines += [
        "",
        "| 표본 | 직전 상태 의존 | 최장 원시-공식 불일치 | 최장 확인 대기 | "
        "필터 흡수 | 정상 아래 확장기 |",
        "|---|---|---|---|---|---|",
    ]
    for name in (LATEST, ALFRED):
        entry = payload["samples"][name]
        lines.append(
            f"| {name} | {entry['previous_state_retention_weeks']}주 "
            f"({entry['previous_state_retention_share']:.1%}) | "
            f"{entry['longest_raw_versus_official_disagreement_weeks']}주 | "
            f"{entry['longest_confirmation_pending_weeks']}주 | "
            f"{entry['filter_absorbed_raw_flip_weeks']}주 | "
            f"{entry['sub_normal_expansion_weeks']}주 |"
        )
    lines += [
        "",
        "국면별 충돌률",
        "",
        "| 표본 | 회복 | 확장 | 후퇴 | 침체 |",
        "|---|---|---|---|---|",
    ]
    for name in (LATEST, ALFRED):
        per = payload["samples"][name]["phase_specific"]
        lines.append(
            f"| {name} | "
            + " | ".join(
                f"{per[phase]['semantic_conflicts']}/{per[phase]['weeks']}"
                for phase in ("recovery", "expansion", "slowdown", "contraction")
            )
            + " |"
        )
    lines += [
        "",
        f"2주 이상 이어진 충돌 구간: {payload['conflict_episodes'] or '없음'}",
        "",
        "## 2001년 경로 감사",
        "",
        f"- 창 {path['window'][0]} ~ {path['window'][1]} · {path['weeks']}주",
        f"- 분류: {path['class_counts']}",
        f"- 의미 충돌 {path['semantic_conflict_weeks']}건 · 높은 증거 충돌 "
        f"{path['high_evidence_semantic_conflict_weeks']}건",
        f"- 수준·모멘텀이 **둘 다** 중립대 밖에서 어긋난 높은 증거 주: "
        f"**{path['high_evidence_both_signs_contradicted_weeks']}건**",
        f"- 공식 전환: {path['official_phase_transitions']}",
        "",
    ]
    for key, value in path["answers"].items():
        if isinstance(value, dict):
            answer = value.get("answer", json.dumps(value, ensure_ascii=False))
            lines.append(f"**{key}** — {answer}")
        else:
            lines.append(f"**{key}** — {value}")
        lines.append("")
    lines += [
        path["vintage_limitation"],
        "",
        "## 에피소드",
        "",
        "| 에피소드 | 표본 | 주 | 지지 | 확인 지연 | 낮은 증거 | 충돌 | 성격 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for episode in payload["episodes"]:
        if episode.get("weeks", 0) == 0:
            continue
        lines.append(
            f"| {episode['episode']} | {episode['sample']} | {episode['weeks']} | "
            f"{episode['semantically_supported_weeks']} | "
            f"{episode['bounded_confirmation_lag_weeks']} | "
            f"{episode['low_evidence_ambiguous_weeks']} | "
            f"{episode['semantic_conflict_weeks']} | `{episode['kind']}` |"
        )
    lines += [
        "",
        "## 현재 2026년 출력",
        "",
        "```",
        now["headline"],
        "```",
        "",
        f"- {now['why_this_phase_wins']}",
        f"- 원시 점수 {now['raw_scores']}",
        f"- 필터 점수 {now['filtered_scores']}",
        f"- 2위와의 차이 {now['separation_from_second']}",
        f"- 수준 {now['activity_level']} · 모멘텀 {now['activity_momentum']} · "
        f"부호 사분면 `{now['sign_quadrant']}` (공식과 일치: "
        f"{now['sign_quadrant_matches_official']})",
        f"- 확증 동행 도메인 {now['breadth']['confirming_coincident_domains']} · "
        f"양수 모멘텀 도메인 {now['breadth']['positive_momentum_domains']}",
        f"- 집중도 {now['concentration']} (경계 {now['concentration_flag']}, 과밀 "
        f"{now['concentration_is_crowded']})",
        f"- 도메인 신선도(주) {now['domain_freshness_weeks']}",
        f"- 의미 분류 `{now['semantic_class']}` → **{now['semantically_supported_or_retained']}**",
        f"- 직전 국면 `{now['previous_official_phase']}`가 결과를 실질적으로 정하는가: "
        f"**{now['previous_state_materially_determines_the_result']}**",
        "",
        "국면이 바뀌려면",
        "",
        *[f"- {value}" for value in now["what_would_change_the_official_phase"].values()],
        "",
        "## 분류 어휘",
        "",
        *[f"- `{name}`" for name in CLASSES],
        "",
        "사유 코드",
        "",
        *[f"- `{name}` — {text}" for name, text in REASON_CODES.items()],
        "",
        "## 의미 지문",
        "",
        f"- `semantic_digest` {payload['semantic_digest']}",
        f"- 덮는 것: {', '.join(payload['semantic_digest_covers'])}",
        f"- 빼는 것: {', '.join(payload['semantic_digest_excludes'])}",
        "",
        f"`{FORBIDDEN_CLASSIFICATION}`는 이 단계가 낼 수 있는 값이 아니다. "
        f"허용 분류는 {', '.join(CLASSIFICATIONS)}뿐이다.",
        "",
        "이 단계는 투자 판단·섹터·비중·종목·매매 지시를 만들지 않는다.",
        "",
    ]
    return "\n".join(lines)


def _finalise(settings: Any, output: Any, payload: dict[str, Any]) -> None:
    """§12. 잠금일 때만 만드는 산출물. 새 로직이 아니라 앞 단계 계약을 그대로 동결한다."""

    from ..operational_review.review import load_alfred_path
    from ..recovery_semantics import manifest as MF
    from ..recovery_semantics import monitoring as MON

    previous = json.loads(
        (settings.root / "outputs/recovery_semantics/validation_summary.json").read_text(
            encoding="utf-8"
        )
    )
    decomposition = previous["delay_decomposition"]
    state = MF.current_state(load_alfred_path(settings), decomposition, payload["provenance"])
    MF.validate_contract(state)
    record = MF.operational_manifest(payload["provenance"], previous["decision"], decomposition)
    record["state_semantics_decision"] = payload["decision"]["classification"]
    record["state_semantics_semantic_digest"] = payload["semantic_digest"]
    record["us_four_phase_model_development"] = "stopped"
    record["live_monitoring"] = "activated"
    record["current_phase_semantic_class"] = payload["current_state_semantics"]["semantic_class"]
    record["current_evidence_quality"] = payload["current_state_semantics"]["evidence_quality"]
    record["disclosed_limitations"] = {
        "high_evidence_both_sign_contradiction_weeks": payload["path_2001"][
            "high_evidence_both_signs_contradicted_weeks"
        ],
        "hard_rules_literally_failing": payload["decision"]["hard_rules_literally_failing"],
        "recovery_latency_band": decomposition["calendar_band"],
    }

    write_json(output / "operational_manifest.json", record)
    write_json(output / "current_state_output.json", state)
    (output / "current_state_report.md").write_text(
        MF.report(state), encoding="utf-8", newline="\n"
    )
    (output / "live_monitoring_spec.md").write_text(
        MON.specification(record), encoding="utf-8", newline="\n"
    )

    baseline = MON.snapshot(state, payload["provenance"]["hashes"])
    baseline["role"] = "thirteen_week_monitoring_baseline"
    baseline["state_semantics_semantic_digest"] = payload["semantic_digest"]
    baseline["model_development"] = "stopped"
    write_json(output / "monitoring_baseline_snapshot.json", baseline)


def main() -> int:
    settings = load_settings()
    payload = run(settings)
    audited = payload.pop("_audited")
    output = settings.root / "outputs" / OUTPUT_NAME
    output.mkdir(parents=True, exist_ok=True)

    weekly = pd.concat([table.reset_index() for table in audited.values()], ignore_index=True)
    weekly.to_csv(output / "weekly_semantic_audit.csv", index=False)
    pd.DataFrame(payload["episodes"]).to_csv(output / "episode_semantic_audit.csv", index=False)

    rows = [
        table[table["semantic_class"].eq("semantic_conflict")].reset_index()
        for table in audited.values()
    ]
    conflicts = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    conflicts.to_csv(output / "semantic_conflicts.csv", index=False)

    write_json(output / "provenance.json", payload["provenance"])
    write_json(output / "current_state_semantic_audit.json", payload["current_state_semantics"])
    write_json(
        output / "state_semantics_decision.json",
        {
            **payload["decision"],
            "semantic_digest": payload["semantic_digest"],
            "semantic_digest_covers": payload["semantic_digest_covers"],
            "semantic_digest_excludes": payload["semantic_digest_excludes"],
            "source_commit": payload["provenance"]["expected_source_commit"],
            "executed_at_utc": payload["provenance"]["executed_at_utc"],
            "hashes": payload["provenance"]["hashes"],
        },
    )
    write_json(output / "validation_summary.json", payload)
    (output / "state_semantics_report.md").write_text(
        _report(payload), encoding="utf-8", newline="\n"
    )

    if payload["decision"]["classification"] == "provisional_model_locked":
        _finalise(settings, output, payload)

    print(json.dumps(payload["decision"], ensure_ascii=False, indent=2))
    print(f"산출물: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
