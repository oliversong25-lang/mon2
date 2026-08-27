"""회복 의미론 심사 실행기.

    python -m business_cycle.recovery_semantics

산출물은 격리 경로 ``outputs/recovery_semantics/``에만 쓴다. 앞 단계의 산출물을
덮어쓰지 않는다.
"""

from __future__ import annotations

import json
from typing import Any

import pandas as pd

from ..config import load_settings
from . import manifest as MF
from . import monitoring as MON
from . import turning
from .decide import CLASSIFICATIONS, FORBIDDEN_CLASSIFICATION
from .latency import LIMITATION_LABELS
from .review import OUTPUT_NAME, run, write_json


def _gate_rows(payload: dict[str, Any]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for group, entries in payload["rechecked_gates"]["groups"].items():
        for name, detail in entries.items():
            rows.append(
                {
                    "group": group,
                    "gate": name,
                    "previously_passed": name in payload["rechecked_gates"]["checked"],
                    "value": json.dumps(detail.get("value"), ensure_ascii=False, default=str),
                    "limit": json.dumps(detail.get("limit"), ensure_ascii=False, default=str),
                    "passes": detail["passes"],
                    "regressed": name in payload["rechecked_gates"]["regressed_gates"],
                }
            )
    for name, detail in payload["amber_conditions"].items():
        rows.append(
            {
                "group": "amber_conditions",
                "gate": name,
                "previously_passed": False,
                "value": json.dumps(detail.get("value"), ensure_ascii=False, default=str),
                "limit": json.dumps(detail.get("limit"), ensure_ascii=False, default=str),
                "passes": detail["passes"],
                "regressed": False,
            }
        )
    return pd.DataFrame(rows)


def _latency_rows(payload: dict[str, Any]) -> pd.DataFrame:
    decomposition = payload["delay_decomposition"]
    rows = [{"component": name, "weeks": value} for name, value in decomposition["layers"].items()]
    rows += [
        {"component": "sequential_layer_sum", "weeks": decomposition["sequential_layer_sum_weeks"]},
        {
            "component": "calendar_recovery_latency",
            "weeks": decomposition["calendar_recovery_latency_weeks"],
        },
        {
            "component": "evidence_availability_adjusted_latency",
            "weeks": decomposition["evidence_availability_adjusted_latency_weeks"],
        },
        {"component": "state_machine_delay", "weeks": decomposition["state_machine_delay_weeks"]},
    ]
    for name, value in decomposition["dates"].items():
        rows.append({"component": f"date::{name}", "weeks": value})
    return pd.DataFrame(rows)


def _report(payload: dict[str, Any]) -> str:
    decision = payload["decision"]
    decomposition = payload["delay_decomposition"]
    detail = payload["episode_2009"]
    disclosed = payload["amber_conditions"]["pre_trough_recovery_in_the_raw_or_filtered_layer"]
    reproduction = payload["reproduction"]
    adjusted = decomposition["evidence_availability_adjusted_latency_weeks"]
    red = payload["red_scope_audit"]
    gap = payload["post_trough_gap"]
    concurrency = decomposition["concurrency"]
    invariants = decomposition["invariants"]
    lines = [
        "# 4국면 v1.1 회복 인식 의미론 심사",
        "",
        f"**분류: `{decision['classification']}`**",
        "",
        f"사유: {decision['reason']}",
        "",
        f"`{FORBIDDEN_CLASSIFICATION}`는 이 단계가 낼 수 있는 값이 아니다. "
        "동결 모델을 읽기만 했고 모수·변환·국면 점수·전이·확인·폭 규칙을 하나도 바꾸지 않았다.",
        "",
        "## 첫 장에 두는 주요 한계",
        "",
        f"- **2001년 침체는 같은 구간대에서 `red`다({gap['calendar_recovery_latency_weeks']}주).** "
        f"채택 게이트는 §9-A·§9-B가 `{red['gate_episode']}` 하나로 한정했으므로 이 결과는 "
        "게이트가 아니라 진단이다. 범위를 모든 에피소드로 넓히면 결론은 "
        f"`{red['counterfactual_if_the_red_gate_applied_to_every_episode']}`로 바뀐다.",
        "- **2009년 5월 22일에 원시·필터 층이 이미 4주 회복 열을 만들었다** "
        "(`begins_pre_trough_and_overlaps_turning_month`). 공식 국면이 막았다고 해서 "
        "무해하다고 적지 않는다. 안정성 진단으로 공시한다.",
        f"- **2020년 회복 지연은 `{decomposition['calendar_band']}`"
        f"({decomposition['calendar_recovery_latency_weeks']}주)다.** 조정 지연 {adjusted}주는 "
        f"허용 {decomposition['confirmation_allowance_weeks']}주와 정확히 같아 여유가 없다.",
        "",
        "## §2. 2001년 red의 범위",
        "",
        f"- 구간대 어휘는 모든 에피소드에 적용된다: "
        f"{red['band_vocabulary_applies_to_every_episode']}",
        f"- 채택 게이트의 범위: `{red['scope']}` (에피소드 `{red['gate_episode']}`)",
        f"- 결과를 계산하기 전에 선언됐는가: {red['declared_before_results']} — {red['citation']}",
        f"- red 구간대 에피소드: {red['episodes_in_the_red_band']} · 게이트 대상: "
        f"{red['episodes_in_the_red_band_are_gated']}",
        f"- 결과를 본 뒤 면제했는가: {not red['not_exempted_after_seeing_the_result']}",
        f"- 게이트가 아닌 이유: {red['why_not_a_gate']}",
        "",
        "### 31주는 무엇을 잰 수인가",
        "",
        f"- 저점 월말 이후 공백 {gap['gap_weeks_after_the_trough_month']}주 중 공식 침체는 "
        f"**{gap['official_contraction_weeks_in_the_gap']}주**뿐이다.",
        f"- 침체를 벗어난 주 {gap['first_week_out_of_official_contraction']} "
        f"(+{gap['contraction_exit_latency_weeks']}주, `{gap['contraction_exit_band']}`)",
        f"- 국면별 주 수: {gap['weeks_by_official_phase']}",
        f"- 분류: **`{gap['gap_kind']}`** — {gap['what_the_latency_measures']}",
        "",
        "연속 침체가 아니다. 회복 라벨을 거치지 않고 후퇴기·확장기를 지나 시계를 돈 것이다.",
        "그렇다고 red를 지우지 않는다. 공식 `recovery` 라벨까지 31주가 걸린 것은 사실이다.",
        "",
        "## §3. 2009년 층별 회복 타임라인",
        "",
        "| 층 | 첫 회복 | 4주 열 | 6월 이전 | 6월 안 | 위치 | 역할 |",
        "|---|---|---|---|---|---|---|",
        *[
            f"| {row['layer']} | {row['first_recovery_week']} | "
            f"{row['first_four_week_sequence_start']}~{row['first_four_week_sequence_end']} | "
            f"{row['weeks_of_the_sequence_before_the_trough_month']}주 | "
            f"{row['weeks_of_the_sequence_inside_the_turning_month']}주 | "
            f"`{row['sequence_position']}` | {row['role']} |"
            for row in payload["layer_recovery_timelines"]
        ],
        "",
        "네 어휘는 시작만이 아니라 끝까지 보고 정한다. 5월 22일에 시작해 6월 12일에 끝나는",
        "열은 저점 월과 절반이 겹치므로 `entirely_pre_trough_month`가 아니다. 채택 게이트가",
        "보는 '진짜 저점 이전'은 그 하나뿐이고, 게이트 층은 §9-B의 '**confirmed** recovery'",
        "그대로 공식 국면이다. 새 규칙을 만든 것이 아니라 원래 규칙을 정확히 적은 것이다.",
        "",
        "세 층 모두 4·8·13주 안 침체 복귀가 0이다.",
        "",
        "## 주간 대 월간 전환점 규약",
        "",
        "NBER 정점·저점은 **월** 날짜다. 그 달 안 어느 주가 전환점인지 말해 주지 않는다.",
        "1차 규약은 구간 검열이다. 저점 월 전체가 하나의 구간이고, 그 안에서 모델에 유리한",
        "날을 고르지 않는다. USREC 주간 매핑은 2차 비교로만 적는다.",
        "",
        f"- `{turning.POSITIONS[0]}` 저점 월 첫날보다 앞선 회복. 이것만이 진짜 조기 이탈이다.",
        f"- `{turning.POSITIONS[1]}` 저점 월 안. 조기라고도 늦었다고도 단정하지 않는다.",
        f"- `{turning.POSITIONS[2]}` 저점 월 마지막 날보다 뒤진 회복. 지연은 월말부터 잰다.",
        "",
        "## 사전 선언 지연 구간대",
        "",
        "| 구간대 | 저점 월말 이후 | 뜻 |",
        "|---|---|---|",
        f"| green | ≤ {turning.GREEN_MAXIMUM_WEEKS}주 | {turning.BAND_MEANING['green']} |",
        f"| amber | {turning.GREEN_MAXIMUM_WEEKS + 1}~{turning.AMBER_MAXIMUM_WEEKS}주 | "
        f"{turning.BAND_MEANING['amber']} |",
        f"| red | > {turning.AMBER_MAXIMUM_WEEKS}주 | {turning.BAND_MEANING['red']} |",
        "",
        "통계적으로 추정한 문턱이 아니라 월간 자료를 쓰는 투자 사이클 모델의 운영 정책이다.",
        "결과를 본 뒤에 바꾸지 않았다.",
        "",
        "## 전환 월 감사",
        "",
        "| 에피소드 | 표본 역할 | 저점 월 | 첫 공식 회복 | 위치 | 달력 지연 | 구간대 | 게이트 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in payload["turning_month_audit"]:
        lines.append(
            f"| {row['episode']} | {row['sample_role']} | {row['nber_trough_month']} | "
            f"{row['first_official_recovery']} | {row['position']} | "
            f"{row['calendar_recovery_latency_weeks']}주 | {row['calendar_band']} | "
            f"{'예' if row['gated'] else '보고만'} |"
        )
    lines += [
        "",
        "## 2009년 회복 재분류",
        "",
        f"- 첫 원시 회복 {detail['first_raw_recovery']} "
        f"({detail['position_of_first_raw_recovery']})",
        f"- 첫 공식 회복 {detail['first_official_recovery']} "
        f"({detail['position_of_first_official_recovery']})",
        f"- 첫 4주 확인 회복 {detail['first_four_week_confirmed_recovery']} "
        f"({detail['position_of_first_confirmed_recovery']})",
        f"- 재분류: **{detail['reclassification']}**",
        f"- 명백한 조기 이탈인가: {'예' if detail['definitively_premature'] else '아니오'}",
        f"- 4·8·13주 안 침체 복귀: {detail.get('return_to_contraction_within_4_weeks')} · "
        f"{detail.get('return_to_contraction_within_8_weeks')} · "
        f"{detail.get('return_to_contraction_within_13_weeks')}",
        f"- 실제 진동: {'있음' if detail.get('exhibited_whipsaw') else '없음'}",
        "",
        f"앞 단계의 기록은 그대로 남는다 — {detail['original_operational_review_finding']}",
        f"{detail['note']}",
        "",
        "### 원시·필터 층에서 나온 앞선 회복 (공시)",
        "",
        "게이트는 공식 국면에 건다. 운영이 내보내는 것이 공식 국면이고, 앞 단계의 게이트도",
        "같은 층에 걸려 있었다. 층을 바꾸면 재확인이 아니라 다른 시험이 된다. 다만 아래를",
        "숨기지 않는다.",
        "",
        f"- {disclosed['value']}",
        "",
        "즉 원시 증거와 필터 승자는 저점 월보다 앞서 회복을 가리켰고, 공식 국면을 그 자리에",
        "붙잡은 것은 확인 규칙이었다.",
        "",
        "## 2020년 회복 지연 분해",
        "",
        "| 층 | 주 |",
        "|---|---|",
    ]
    for name, value in decomposition["layers"].items():
        lines.append(f"| {name} | {value} |")
    lines += [
        "",
        "### 순차 구간 — 겹침 없음, 빈틈 없음",
        "",
        "| 구간 | 시작 | 끝 | 주 | 일 | 진입 조건 | 종료 조건 |",
        "|---|---|---|---|---|---|---|",
        *[
            f"| {row['segment']} | {row['start_date']} | {row['end_date']} | "
            f"{row['duration_weeks']} | {row['duration_days']} | {row['entry_condition']} | "
            f"{row['exit_condition']} |"
            for row in decomposition["segments"]
        ],
        "",
        f"불변식: 경계 단조 {invariants['boundaries_are_monotonic']} · "
        f"빈틈 없음 {invariants['segments_are_contiguous_with_no_gaps']} · "
        f"겹침 {invariants['overlapping_segments'] or '없음'} · "
        f"주 합계 {invariants['segment_week_sum']} = 달력 "
        f"{invariants['calendar_recovery_latency_weeks']} · "
        f"일 합계 {invariants['segment_day_sum']} = 달력 "
        f"{invariants['calendar_recovery_latency_days']}.",
        "",
        "### 변환 지연 4주는 어느 변환에서 오는가",
        "",
        f"- 귀속: **`{concurrency['attributed_to']}`**",
        f"- 구간 {concurrency['weeks_examined']}주 중 다섯 도메인 모멘텀이 모두 상한 "
        f"{concurrency['momentum_cap']}에 닿은 주: "
        f"**{concurrency['weeks_with_every_domain_at_the_momentum_cap']}주**",
        f"- 총량이 상한 부호 투표와 정확히 같았던 주: "
        f"**{concurrency['weeks_where_the_aggregate_equals_the_capped_sign_vote']}주**",
        "",
        "상한을 건 등가중 평균이 포화하면 총량 모멘텀은 사실상 도메인 **부호 투표**가 된다.",
        "그래서 총량이 돌아서려면 도메인 과반이 부호를 바꿔야 했다. 일방 추세 추정이나",
        "중립대 문턱이 아니라 **도메인 총량화**가 이 4주를 만들었다.",
        "",
        "이 구간 안에서 동시에 작동한 원인 (더하지 않는다):",
        "",
        *[
            f"- `{item['cause']}` — {item['evidence']}"
            for item in concurrency["concurrent_contributors"]
        ],
        "",
        f"11주 전부를 발표 지연이라고 적지 않는다. 발표에 귀속되는 것은 "
        f"{decomposition['segments'][0]['duration_weeks']}주뿐이다.",
        "",
        "| 날짜 | 값 |",
        "|---|---|",
    ]
    for name, value in decomposition["dates"].items():
        lines.append(f"| {name} | {value} |")
    lines += [
        "",
        f"- 달력 회복 지연 **{decomposition['calendar_recovery_latency_weeks']}주** "
        f"(`{decomposition['calendar_band']}`)",
        f"- 자료가용성 조정 지연 **{adjusted}주** "
        f"(기준 {decomposition['adjusted_latency_anchor']}, 허용 "
        f"{decomposition['confirmation_allowance_weeks']}주)",
        f"- 상태 기계 지연 {decomposition['state_machine_delay_weeks']}주",
        f"- 한계 어휘: **`{decomposition['limitation_label']}`** "
        f"(허용 어휘 {', '.join(LIMITATION_LABELS)})",
        "",
        "조정 지연이 달력 지연을 지우지 않는다. 둘을 나란히 적는다.",
        "",
        "## 사전 통과 게이트 재확인",
        "",
        f"- 재확인한 게이트 {len(payload['rechecked_gates']['checked'])}개",
        f"- 퇴행: {payload['rechecked_gates']['regressed_gates'] or '없음'}",
        f"- 실시간 경로 재현: "
        f"{'일치' if reproduction['reproduces_the_recorded_real_time_path'] else '불일치'}"
        f" ({reproduction['as_of_weeks']}주)",
        "",
        "## §6 amber 조건",
        "",
        "| 조건 | 값 | 한도 | 결과 |",
        "|---|---|---|---|",
    ]
    for name, entry in payload["amber_conditions"].items():
        mark = "통과" if entry["passes"] else "**실패**"
        lines.append(f"| {name} | {entry.get('value')} | {entry.get('limit', '-')} | {mark} |")
    lines += [
        "",
        "## 남은 한계",
        "",
        "- 엄격 실시간 침체 에피소드가 **하나뿐**이다. 실시간 침체 성능을 일반화할 수 없다.",
        "- 2020년은 이미 들여다봤으므로 손대지 않은 홀드아웃이 아니다.",
        "- 2013-06-14 이전에는 진짜 빈티지가 없다. 2001년과 금융위기는 최신 수정치에서만 봤다.",
        "- 개발 에피소드가 둘뿐이고 회복 거동이 크게 갈린다.",
        "- v1.1은 최신 수정치 규약 아래에서 기각된 상태로 남아 있다.",
        "",
        "이 단계는 투자 판단·섹터·비중·종목·매매 지시를 만들지 않는다.",
        "",
        "## 의미 지문",
        "",
        f"- `semantic_digest` {payload['semantic_digest']}",
        f"- 덮는 것: {', '.join(payload['semantic_digest_covers'])}",
        f"- 빼는 것: {', '.join(payload['semantic_digest_excludes'])} — 이 셋뿐이다",
        "",
        "실행 시각만 바뀌면 지문은 같다. 분류·게이트 결과·보호 지문·지연 값·현재 국면 중",
        "하나라도 바뀌면 달라진다. 원본 산출물은 감사 기록으로 그대로 남긴다.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    settings = load_settings()
    payload = run(settings)
    output = settings.root / "outputs" / OUTPUT_NAME
    output.mkdir(parents=True, exist_ok=True)

    pd.DataFrame(payload["turning_month_audit"]).to_csv(
        output / "turning_month_audit.csv", index=False
    )
    _latency_rows(payload).to_csv(output / "recovery_latency_decomposition.csv", index=False)
    pd.DataFrame(payload["domain_recovery_timeline"]).to_csv(
        output / "domain_recovery_timeline.csv", index=False
    )
    _gate_rows(payload).to_csv(output / "rechecked_operational_gates.csv", index=False)
    pd.DataFrame(payload["delay_decomposition"]["segments"]).to_csv(
        output / "recovery_latency_segments.csv", index=False
    )
    pd.DataFrame(payload["layer_recovery_timelines"]).to_csv(
        output / "layer_recovery_timelines.csv", index=False
    )
    pd.DataFrame(payload["post_trough_phase_path"]).to_csv(
        output / "post_trough_phase_path_2001.csv", index=False
    )

    write_json(output / "provenance.json", payload["provenance"])
    write_json(
        output / "recovery_semantics_decision.json",
        {
            **payload["decision"],
            "source_commit": payload["provenance"]["expected_source_commit"],
            "executed_at_utc": payload["provenance"]["executed_at_utc"],
            "hashes": payload["provenance"]["hashes"],
            "allowed_classifications": list(CLASSIFICATIONS),
            "run_digest": payload["run_digest"],
            "semantic_digest": payload["semantic_digest"],
            "semantic_digest_covers": payload["semantic_digest_covers"],
            "semantic_digest_excludes": payload["semantic_digest_excludes"],
            "model_status": payload["model_status"],
            "red_gate_scope": payload["red_scope_audit"]["scope"],
            "red_band_episodes_not_gated": payload["red_scope_audit"]["episodes_in_the_red_band"],
        },
    )
    write_json(output / "validation_summary.json", payload)
    (output / "recovery_semantics_report.md").write_text(
        _report(payload), encoding="utf-8", newline="\n"
    )

    if payload["decision"]["classification"] == "provisional_operational_adoption":
        state = payload["current_state"]
        record = payload["operational_manifest"]
        MF.validate_contract(state)
        write_json(output / "current_state_output.json", state)
        write_json(output / "operational_manifest.json", record)
        (output / "current_state_report.md").write_text(
            MF.report(state), encoding="utf-8", newline="\n"
        )
        (output / "live_monitoring_spec.md").write_text(
            MON.specification(record), encoding="utf-8", newline="\n"
        )

    print(json.dumps(payload["decision"], ensure_ascii=False, indent=2))
    print(f"산출물: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
