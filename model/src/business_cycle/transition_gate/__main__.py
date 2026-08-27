"""전이 게이트 분석 실행기.

    python -m business_cycle.transition_gate

산출물은 `outputs/transition_gate/`에만 쓴다. v1.1 산출물을 하나도 건드리지 않는다.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pandas as pd

from ..config import load_settings
from ..four_phase.engine import load_config
from . import characterise as C
from . import nber as N
from .gate import DEFAULT_STALE_HOLD_WEEKS, GateConfig, apply, sweep

OUTPUT_NAME = "transition_gate"
PATH_CSV = "outputs/four_phase_v1_1/alfred_audit/weekly_path.csv"

#: 권고 게이트. 원시 동의만 요구하고 분리도 문턱은 걸지 않는다.
#:
#: 처음에는 `sep>=0.5`를 함께 걸려 했다. 게이트가 **무엇을 막았는지** 세어 보니 그 설정은
#: 왕복 11건을 잡으려고 4주 이상 지속한 전이 19건을 막았다 — 65주짜리 후퇴기까지 포함해서다.
#: 분리도만으로 자르면 긴 국면이 먼저 잘린다는 특징 분석(순위 상관 0.138, 최저 구간이 최장
#: 지속)과 정확히 같은 방향이다. 원시 동의 단독만이 왕복을 진짜보다 많이 잡는다(5 대 3).
RECOMMENDED = GateConfig(require_raw_agreement=True)


def _band(duration: dict[str, Any], name: str) -> int:
    """분리도 구간의 전이 건수. 없으면 0."""

    return next((b["transitions"] for b in duration["by_band"] if b["band"] == name), 0)


def _table(rows: list[dict[str, Any]]) -> str:
    head = (
        "| 게이트 | 전이 | 4주 미만 | 침체 지연 | 회복 지연 | 막은 왕복 | 막은 진짜 | "
        "왕복:진짜 | 보류 | 시효 | 폭 게이트 | 현재 판정 |"
    )
    line = "|---|---|---|---|---|---|---|---|---|---|---|---|---|"
    body = [
        f"| `{r['gate']}` | {r['transitions']} | {r['phases_shorter_than_four_weeks']} | "
        f"{r['contraction_delay_weeks']}주 | {r['recovery_delay_weeks']}주 | "
        f"{r['blocked_transitions_that_were_short']} | "
        f"{r['blocked_transitions_that_lasted_four_weeks_or_more']} | "
        f"**{r['whipsaw_to_real_ratio']}** | "
        f"{r['withheld_weeks']} | {r['degraded_to_withheld_weeks']} | "
        f"{'통과' if r['contraction_weeks_meet_the_two_domain_breadth_gate'] else '**실패**'} | "
        f"**{r['final_week_phase']}** |"
        for r in rows
    ]
    return "\n".join([head, line, *body])


def _report(payload: dict[str, Any]) -> str:
    ch = payload["characterisation"]
    dist = ch["separation_distribution"]
    dur = ch["separation_versus_duration"]
    dis = ch["raw_official_disagreement"]
    rec = payload["recommended"]
    same_as_v11 = "같다" if rec["final_week_phase"] == "expansion" else "다르다"
    agree = dis["at_transition"]["raw_agrees_with_the_new_phase"]
    differ = dis["at_transition"]["raw_does_not_agree"]

    lines = [
        "# 4국면 v1.1 전이 게이트 — 채터링 대 지연",
        "",
        "동결 v1.1 위에 얹는 **병렬 변형**이다. 점수를 다시 계산하지 않고 어느 전이를",
        "받아들일지만 정한다. v1.1은 그대로 재현된다.",
        "",
        "## 먼저: 가설이 전체 자료에서 살아남지 않았다",
        "",
        "손으로 고른 13주에서 보이던 0.419와 0.650 사이의 빈틈은 688주 전체에는 **없다**.",
        "",
        f"- 전이 주 분리도: 최소 {dist['transition_weeks']['min']}, 사분위 "
        f"{dist['transition_weeks']['p25']} / 중앙 {dist['transition_weeks']['median']} / "
        f"{dist['transition_weeks']['p75']}, 최대 {dist['transition_weeks']['max']}",
        f"- 0.4~0.5 구간에 전이 {_band(dur, '0.4–0.5')}건, 0.5~0.6에 {_band(dur, '0.5–0.6')}건, "
        f"0.6~0.7에 {_band(dur, '0.6–0.7')}건 — 연속적이다.",
        "",
        "빈틈은 극단만 골라 본 결과였다.",
        "",
        "## 분리도는 지속 기간을 거의 예측하지 못한다",
        "",
        f"순위 상관 **{dur['spearman_rank_correlation']}**. 그리고 관계가 단조가 아니다.",
        "",
        "| 분리도 구간 | 전이 | 중앙 지속 | 4주 미만 | 짧을 확률 |",
        "|---|---|---|---|---|",
    ]
    for band in dur["by_band"]:
        lines.append(
            f"| {band['band']} | {band['transitions']} | {band['median_duration_weeks']}주 | "
            f"{band['short_phases']} | {band['short_rate']} |"
        )
    lines += [
        "",
        "**가장 낮은 구간(0.0~0.2)이 중앙 지속 9주로 두 번째로 길고 짧을 확률도 0.111로 낮다.**",
        '"낮은 분리도가 짧은 국면을 예고한다"는 가설과 정면으로 어긋난다.',
        "",
        "관계가 깨지는 구체적 사례:",
        "",
    ]
    for entry in dur["where_it_fails"]["low_separation_but_lasted_13_weeks_or_more"]:
        lines.append(
            f"- {entry['week']} → `{entry['to']}` 분리도 {entry['separation']:.3f}인데 "
            f"**{entry['weeks']}주** 지속"
        )
    lines += [
        "",
        "분리도 문턱은 이런 길고 옳은 국면을 막는다.",
        "",
        "## 원시-공식 불일치가 진짜 신호다",
        "",
        f"688주 중 원시와 공식이 어긋난 주 {dis['weeks_where_raw_differs_from_official']}주 "
        f"({dis['disagreement_rate']:.1%}).",
        "",
        "| 전이 시점 | 건수 | 2주 내 되돌림 | 4주 미만 | 중앙 지속 |",
        "|---|---|---|---|---|",
        f"| 원시가 새 국면에 **동의** | {agree['transitions']} | "
        f"{agree['reversion_rate']} | {agree['short_rate']} | "
        f"{agree['median_duration_weeks']}주 |",
        f"| 원시가 **동의하지 않음** | {differ['transitions']} | "
        f"**{differ['reversion_rate']}** | **{differ['short_rate']}** | "
        f"{differ['median_duration_weeks']}주 |",
        "",
        "되돌림 확률이 **0.079 대 0.500**이다. 분리도보다 훨씬 선명하다.",
        "확인된 층이 걸러지지 않은 층보다 덜 안정적이었다는 관찰이 여기서 수치로 확인된다.",
        "",
        "## 자연 실험",
        "",
        "| 국면 | 폭·지속 게이트 | 에피소드 | 중앙 길이 | 4주 미만 |",
        "|---|---|---|---|---|",
    ]
    for phase, entry in ch["gated_versus_ungated_phases"].items():
        lines.append(
            f"| {phase} | {'있음' if entry['has_breadth_or_persistence_gate'] else '없음'} | "
            f"{entry['episodes']} | {entry['median_episode_weeks']}주 | "
            f"{entry['episodes_shorter_than_four_weeks']} |"
        )
    lines += [
        "",
        "회복기는 게이트가 있고 2건 모두 길다. 확장·후퇴는 게이트가 없고 짧은 에피소드가 17건이다.",
        "다만 **침체도 4주 미만이 2건** 있다 — 폭 게이트가 짧은 침체를 막지는 못했다.",
        '"게이트가 있으면 안정적"이라는 정리는 절반만 맞다.',
        "",
        "## 임계값 스윕",
        "",
        _table(payload["sweep"]),
        "",
        "침체 지연·회복 지연은 v1.1이 부른 주(2020-04-03, 2020-07-17)를 기준으로 잰다.",
        "",
        "## NBER 대조",
        "",
        "| 게이트 | 재현율 | 첫 침체 호출 | 오탐 주 | 오탐 구간 | 회복 | 저점 후 지연 |",
        "|---|---|---|---|---|---|---|",
    ]
    for entry in payload["nber"]:
        lines.append(
            f"| `{entry['gate']}` | {entry['recall']} | {entry['first_contraction_call']} | "
            f"{entry['false_positive_contraction_weeks']} | "
            f"{len(entry['false_positive_episodes'])} | "
            f"{entry['first_recovery_after_the_trough_month']} | "
            f"{entry['recovery_lag_weeks_from_trough_month_end']}주 |"
        )
    lines += [
        "",
        "### 2020년에 게이트가 실제로 한 일",
        "",
        "| 주 | v1.1 공식 | 원시 | 분리도 | 게이트 결과 |",
        "|---|---|---|---|---|",
        "| 2020-01-17 | contraction | slowdown | 0.419 | **차단** — 2주 뒤 되돌아간 가짜 출발 |",
        "| 2020-02-28 | contraction | slowdown | **0.754** | **차단** — 1주짜리 깜빡임 |",
        "| 2020-04-03 | contraction | contraction | 0.862 | **통과** — 진짜 호출, 지연 0 |",
        "",
        "2020-02-28의 분리도는 **0.754**로 모든 후보 임계값보다 높다.",
        "분리도만 보는 게이트는 이것을 통과시킨다. **원시 동의 조건만이 잡는다.**",
        "",
        "재현율이 0.417에서 0.333으로 내려간 것은 그 1주짜리 깜빡임이 NBER 침체 구간 안에",
        "있었기 때문이다. 다음 주에 되돌아간 신호라 쓸 수 있는 판정이 아니었지만, 재현율이",
        "떨어진 것은 사실이므로 그대로 적는다.",
        "",
        "저점 이후 11주 오탐(2020-05-01~07-10)은 게이트가 줄이지 못한다. 그것은 채터링이",
        "아니라 이미 공시된 회복 인식 지연이다.",
        "",
        "## 권고",
        "",
        f"**`{rec['gate']}`** — 원시 동의만 요구하고 분리도 문턱은 걸지 않는다.",
        "",
        f"- 전이 72 → **{rec['transitions']}**, "
        f"4주 미만 18 → **{rec['phases_shorter_than_four_weeks']}**",
        f"- 침체 인식 지연 **{rec['contraction_delay_weeks']}주**, "
        f"회복 인식 지연 **{rec['recovery_delay_weeks']}주**",
        f"- 막은 것: 왕복 {rec['blocked_transitions_that_were_short']}건 대 "
        f"진짜 {rec['blocked_transitions_that_lasted_four_weeks_or_more']}건 "
        f"(비 **{rec['whipsaw_to_real_ratio']}**)",
        f"- 시효 강등 {rec['degraded_to_withheld_weeks']}주, "
        f"현재 판정 **{rec['final_week_phase']}**",
        "",
        "### 왜 분리도 문턱을 빼는가",
        "",
        "전이 수만 세면 채터링을 지운 것과 옳은 전이를 지운 것이 구별되지 않는다.",
        "그래서 게이트가 실제로 막은 전이를 **지속 기간으로** 갈라 세면 이렇게 된다.",
        "",
        "| 게이트 | 막은 왕복 | 막은 진짜(4주+) | 비 |",
        "|---|---|---|---|",
        *[
            f"| `{r['gate']}` | {r['blocked_transitions_that_were_short']} | "
            f"{r['blocked_transitions_that_lasted_four_weeks_or_more']} | "
            f"{r['whipsaw_to_real_ratio']} |"
            for r in payload["sweep"]
            if r["blocked_transitions_that_lasted_four_weeks_or_more"]
        ],
        "",
        "**분리도를 켜는 순간 비가 1 아래로 내려간다.** 0.5에서는 왕복 11건을 잡으려고",
        "4주 이상 지속한 전이 19건을 막는다. 그중에는 2018-10-19 후퇴기 전이(분리도 0.241)가",
        "있는데 그것은 **65주** 지속했다. 2018-04-20 확장기 26주, 2024-03-15 후퇴기 18주도",
        "같은 이유로 막힌다.",
        "",
        "특징 분석이 이미 가리키던 방향이다 — 순위 상관 0.138에 최저 분리도 구간이 최장",
        "지속이었으므로, 분리도로 자르면 긴 국면이 먼저 잘린다.",
        "",
        "원시 동의 단독만이 왕복을 진짜보다 많이 잡는다.",
        "",
        "근거는 적합도가 아니라 교환이다. 채터링을 절반 가까이 줄이면서 지연을 한 주도",
        "사지 않고, 지운 것 중 왕복이 진짜보다 많다. 분리도 문턱은 채터링을 **더** 줄이지만",
        "그 대가로 옳은 전이를 더 많이 지운다 — 매끄럽지만 틀린 경로 쪽이다.",
        "",
        "### 남은 4주 미만 국면을 더 줄이려면",
        "",
        "분리도로 살 것이 아니다. 자연 실험이 가리키는 방향은 `expansion`·`slowdown`에도",
        "침체·회복처럼 **폭이나 지속 요건**을 주는 것이고, 그것은 후처리가 아니라 엔진",
        "변경이므로 이 단계의 범위 밖이다.",
        "",
        "## 막힌 전이는 무엇을 보여주는가",
        "",
        f"직전 국면을 유지하되 **{DEFAULT_STALE_HOLD_WEEKS}주**까지만이다. 연속으로 그보다 오래",
        "막히면 국면을 지어내지 않고 `withheld`로 내려간다.",
        "",
        "유지에 시효를 두는 이유는 하나다 — 오래 막힌 상태는 모델이 자기 증거가 더는 뒷받침하지",
        "않는 판정을 계속 내보내는 것이다. 그건 채터링보다 나쁘다. 사용자는 값이 바뀌지 않으니",
        "안정적이라고 읽는데, 실제로는 근거가 사라진 채 얼어 있는 것이다.",
        "",
        f"권고 게이트에서는 강등이 {rec['degraded_to_withheld_weeks']}주 발생한다.",
        f"가장 오래 막힌 구간은 {rec['longest_blocked_run_weeks']}주였다.",
        "",
        "## 현재 판정",
        "",
        "2026-08-14 분리도 0.557로 애매한 구간에 있다. 권고 게이트에서 현재 판정은",
        f"**`{rec['final_week_phase']}`**이며, v1.1과 {same_as_v11}.",
        "",
        "0.6 이상에서는 `slowdown`으로 바뀌고 3주째 막혀 있는 상태가 된다. 임계값 선택이",
        "앱 화면에 보이는 값을 직접 바꾼다는 뜻이므로, 이 표를 보고 정할 일이다.",
        "",
        "## 한계",
        "",
        "- ALFRED 창 안 NBER 침체가 **하나뿐**이다. 재현율·오탐을 에피소드 하나에서 쟀다.",
        "- 게이트는 채터링만 다룬다. 회복 인식 지연은 그대로 남는다 — 다른 다이얼이다.",
        "- 전이 72건 중 63건이 `expansion`↔`slowdown`이라, 이 분석은 사실상 그 경계에 관한 것이다.",
        "- 이 단계는 투자 판단·섹터·비중·종목·매매 지시를 만들지 않는다.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    settings = load_settings()
    config = load_config(settings)
    frame = C.load_path(str(settings.root / PATH_CSV))

    rows = sweep(frame)
    recommended = next(
        r
        for r in rows
        if r["separation_threshold"] == RECOMMENDED.separation_threshold
        and r["require_raw_agreement"] == RECOMMENDED.require_raw_agreement
    )
    nber_rows = [
        N.audit(frame, cfg)
        for cfg in (
            GateConfig(),
            GateConfig(require_raw_agreement=True),
            GateConfig(0.4, True),
            GateConfig(0.5, True),
            GateConfig(0.6, True),
            GateConfig(0.7, True),
        )
    ]

    payload: dict[str, Any] = {
        "variant": "parallel_gate_over_frozen_v1_1",
        "frozen_model_modified": False,
        "frozen_config_sha256": config.sha256,
        "executed_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "characterisation": C.run(frame),
        "sweep": rows,
        "nber": nber_rows,
        "recommended": recommended,
        "recommended_config": {
            "separation_threshold": RECOMMENDED.separation_threshold,
            "require_raw_agreement": RECOMMENDED.require_raw_agreement,
            "stale_hold_weeks": RECOMMENDED.stale_hold_weeks,
        },
    }

    output = settings.root / "outputs" / OUTPUT_NAME
    output.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(output / "threshold_sweep.csv", index=False)
    pd.DataFrame(nber_rows).to_csv(output / "nber_validation.csv", index=False)
    pd.DataFrame(payload["characterisation"]["transition_rows"]).to_csv(
        output / "transitions.csv", index=False
    )
    apply(frame, RECOMMENDED).to_csv(output / "recommended_weekly_path.csv")
    (output / "transition_gate_report.md").write_text(
        _report(payload), encoding="utf-8", newline="\n"
    )
    (output / "validation_summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    print(f"권고 게이트: {recommended['gate']}")
    print(
        f"  전이 72 → {recommended['transitions']} · 4주 미만 18 → "
        f"{recommended['phases_shorter_than_four_weeks']}"
    )
    print(
        f"  침체 지연 {recommended['contraction_delay_weeks']}주 · "
        f"회복 지연 {recommended['recovery_delay_weeks']}주 · "
        f"현재 판정 {recommended['final_week_phase']}"
    )
    print(f"산출물: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
