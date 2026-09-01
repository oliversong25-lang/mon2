"""해석 확인 실행기.

    python -m business_cycle.boundary_reading

앞 단계와 같은 확장 역사, 같은 격자를 쓴다. 새로 재는 것은 셋뿐이다 — 격자를 쓸었다는
사실을 반영한 p, 1992년 양쪽 반쪽, 후퇴기 블록 목록.

동결 v1.1은 건드리지 않는다.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from ..boundary_verification import checks as C
from ..boundary_verification import extended as X
from ..config import load_settings
from ..slowdown_boundary import scoring as SC
from ..slowdown_boundary import variants as V
from . import episodes as E
from . import multiplicity as MU
from . import regimes as R

OUTPUT_NAME = "boundary_reading"

#: 앞 단계가 권고한 값. 이 단계는 그것을 다시 고르지 않는다.
CHOSEN: SC.SlowdownGate = SC.SlowdownGate(persistence_weeks=17)

#: 앞 단계가 권고 전에 쓸었던 값.
PREVIOUS: SC.SlowdownGate = SC.SlowdownGate(persistence_weeks=13)


def _grid() -> list[SC.SlowdownGate]:
    """B가 실제로 쓴 격자를 그대로 다시 만든다. 여기서 늘리면 보정이 거짓말이 된다."""

    gates = [SC.SlowdownGate(persistence_weeks=n) for n in C.PERSISTENCE_LENGTHS]
    gates += [
        SC.SlowdownGate(persistence_weeks=CHOSEN.persistence_weeks, persistence_band=band)
        for band in C.BANDS
        if band != 1.0
    ]
    return gates


def build_payload(settings: Any) -> dict[str, Any]:
    root = settings.root
    prepared, config = X.build(settings)
    baseline = V.path(prepared, config, V.Variant("baseline", SC.SlowdownGate(), False))
    weeks = list(baseline.index)
    panel = C.panels(weeks, str(root / "data" / "cache" / "famafrench"))[
        C.HORIZONS[C.HORIZONS.index(13)]
    ]

    # 1 — 격자 전체를 확장 역사 위에서 다시 돌린다. 보정은 "쓴 격자"에 대해서만 뜻이 있다.
    entries: dict[str, dict[str, Any]] = {}
    for gate in _grid():
        frame = V.path(prepared, config, V.Variant("grid", gate, False))
        entries[gate.name] = MU.shift_draws(frame["official_phase"], panel)

    chosen = entries[CHOSEN.name]
    family = MU.max_statistic(entries)
    bonf = MU.bonferroni(float(chosen["nominal_p"]), len(entries))
    corrected = MU.read(chosen, family, bonf, CHOSEN.name)

    # 2 — 1992년 양쪽. 권고한 설정 하나만 가른다.
    chosen_frame = V.path(prepared, config, V.Variant("chosen", CHOSEN, False))
    chosen_phase = chosen_frame["official_phase"]
    halves = R.split(chosen_phase, panel)

    # 3 — 블록 목록. 확장 역사와 동결 창 둘 다 낸다.
    long_rows = E.listing(chosen_phase)
    frozen_prepared, frozen_config = V.build(settings)
    frozen_phase = V.path(frozen_prepared, frozen_config, V.Variant("chosen", CHOSEN, False))[
        "official_phase"
    ]
    frozen_rows = E.listing(frozen_phase)

    long_summary = E.summarise(long_rows)
    long_coverage = E.recession_coverage(long_rows, str(weeks[0]), str(weeks[-1]))
    frozen_summary = E.summarise(frozen_rows)
    frozen_coverage = E.recession_coverage(
        frozen_rows, str(frozen_phase.index[0]), str(frozen_phase.index[-1])
    )

    return {
        "stage": "boundary_reading",
        "frozen_model_modified": False,
        "frozen_config_sha256": config.sha256,
        "executed_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "chosen_gate": CHOSEN.name,
        "previous_gate": PREVIOUS.name,
        "grid": sorted(entries),
        "corrected_significance": corrected,
        "grid_table": [
            {
                "gate": name,
                "discrimination": entries[name]["ratio_to_chance"],
                "nominal_p": entries[name]["nominal_p"],
            }
            for name in sorted(entries, key=lambda key: float(entries[key]["nominal_p"]))
        ],
        "consumption_split": halves,
        "extended_episodes": long_rows,
        "extended_episode_summary": long_summary,
        "extended_recession_coverage": long_coverage,
        "extended_definition": E.define(long_summary, long_coverage),
        "frozen_window_episodes": frozen_rows,
        "frozen_window_episode_summary": frozen_summary,
        "frozen_window_recession_coverage": frozen_coverage,
        "frozen_window_definition": E.define(frozen_summary, frozen_coverage),
        "extended_weeks": len(weeks),
        "frozen_weeks": int(len(frozen_phase)),
    }


def _episode_table(rows: list[dict[str, Any]]) -> list[str]:
    lines = [
        "| # | 시작 | 종료 | 주 | 직전 | 간 곳 | 다음 NBER 침체 | 간격 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    label = {"progressed": "**침체**", "reverted": "확장 복귀", "open": "진행 중"}
    for row in rows:
        gap = row["weeks_until_recession"]
        mark = "" if gap is None else (f"{gap}주" + (" ◂" if row["near"] else ""))
        lines.append(
            f"| {row['index']} | {row['start']} | {row['end']} | {row['weeks']} | "
            f"{row['came_from'] or '—'} | {label[row['outcome']]} | "
            f"{row['next_nber_recession'] or '—'} | {mark or '—'} |"
        )
    return lines


def build_report(payload: dict[str, Any]) -> str:
    corrected = payload["corrected_significance"]
    halves = payload["consumption_split"]
    summary = payload["extended_episode_summary"]
    frozen_summary = payload["frozen_window_episode_summary"]

    lines = [
        "# 해석 확인 — 결론이 아니라 결론의 표현",
        "",
        "앞 단계가 persist13w에서 **persist17w**로 권고를 조정했다. 이 단계는 그 권고를 "
        "다시 묻지 않는다. 권고를 적는 방식이 정직한지를 세 곳에서 확인하고, 그 결과를 "
        "보고서와 한계 문구에 남긴다.",
        "",
        "## 1 — p=0.0458을 선택 절차와 함께 적는다",
        "",
        corrected["statement"],
        "",
        "| 보정 | p | 5% 아래인가 |",
        "|---|---|---|",
        f"| 명목(선택된 최댓값) | {corrected['nominal']['nominal_p']} | "
        f"{'예' if float(corrected['nominal']['nominal_p']) <= 0.05 else '아니오'} |",
        f"| min-P 이동 보정(격자 {corrected['family_wise']['grid_size']}개) | "
        f"{corrected['family_wise']['family_wise_p']} | "
        f"{'예' if corrected['survives_correction'] else '**아니오**'} |",
        f"| 본페로니 상한 | {corrected['bonferroni']['bonferroni_p']} | "
        f"{'예' if corrected['bonferroni']['survives_at_five_percent'] else '**아니오**'} |",
        "",
        "완전한 p-해킹은 아니다. 방어를 그대로 적는다.",
        "",
    ]
    lines += [f"- {item}" for item in corrected["defences"]]
    lines += [
        "",
        f"그래도 방어가 보정을 대신하지 않는다. {corrected['family_wise']['note']}",
        "",
        "### 확장 역사 위의 격자 전체",
        "",
        "| 설정 | 판별력 | 명목 p |",
        "|---|---|---|",
    ]
    for row in payload["grid_table"]:
        mark = "**" if row["gate"] == payload["chosen_gate"] else ""
        lines.append(
            f"| {mark}{row['gate']}{mark} | {row['discrimination']} | {row['nominal_p']} |"
        )

    lines += [
        "",
        "## 2 — 1992년 소비 도메인 단절 양쪽",
        "",
        f"{halves['why_here']} NBER 침체 6회가 그 선 양쪽으로 갈린다.",
        "",
        "| 반쪽 | 구간 | 주 | 소비 도메인 | NBER | 후퇴기 블록 | 비중 | 판별력 | p |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for entry in halves["halves"]:
        thin = "" if entry["enough_blocks_to_call_it_a_state"] else " ⚠"
        lines.append(
            f"| {entry['half']} | {entry['first_week']}~{entry['last_week']} | "
            f"{entry['weeks']} | {entry['consumption_domain']} | "
            f"{len(entry['nber_recessions'])}회 | {entry['slowdown_blocks']}{thin} | "
            f"{entry['slowdown_share']:.1%} | {entry['discrimination']} | {entry['p_value']} |"
        )
    lines += [
        f"| 전체 | — | {halves['whole']['weeks']} | 혼합 | 6회 | — | — | "
        f"{halves['whole']['discrimination']} | {halves['whole']['p_value']} |",
        "",
        halves["reading"],
        "",
        "⚠ 표시는 그 반쪽의 후퇴기 블록이 "
        f"{R.MINIMUM_BLOCKS}개 아래라는 뜻이다. 그 숫자는 상태의 성질이 아니라 몇몇 "
        "구간의 성질로 읽어야 한다.",
        "",
        "## 3 — 후퇴기 블록이 실제로 무엇이었는가",
        "",
        f"확장 역사(1976~2026)에서 {summary['blocks']}개다. 닫힌 블록 "
        f"{summary['closed_blocks']}개 중 {summary['progressed_to_contraction']}개가 침체로 "
        f"나아갔고, {summary['reverted_to_expansion']}개가 확장기로 되돌아갔다. 되돌아간 것 중 "
        f"{summary['reverted_but_within_six_months_of_a_recession']}개는 NBER 침체를 "
        f"{summary['near_recession_weeks']}주 안에 두고 있었다(◂ 표시).",
        "",
    ]
    lines += _episode_table(payload["extended_episodes"])
    coverage = payload["extended_recession_coverage"]
    definition = payload["extended_definition"]
    lines += [
        "",
        f"길이는 중앙값 {summary['median_weeks']:g}주, 가장 짧은 것이 "
        f"{summary['shortest_weeks']}주, 가장 긴 것이 {summary['longest_weeks']}주다. "
        f"전조로 읽을 수 있는 블록은 {summary['forewarning_blocks']}/"
        f"{summary['closed_blocks']}({summary['forewarning_share']:.0%})다.",
        "",
        "### 반대 방향 — 침체가 후퇴기를 앞세웠는가",
        "",
        '블록 쪽에서만 보면 "후퇴기 뒤에 무엇이 왔는가"밖에 알 수 없다. 정의를 하려면 '
        "반대쪽도 있어야 한다.",
        "",
        "| NBER 침체 | 앞선 후퇴기 | 선행 |",
        "|---|---|---|",
    ]
    for entry in coverage["detail"]:
        lead = entry["lead_weeks"]
        lines.append(
            f"| {entry['recession']} | {'예' if entry['preceded_by_slowdown'] else '**아니오**'} | "
            f"{f'{lead}주' if lead is not None else '—'} |"
        )
    lines += [
        "",
        f"{coverage['recessions_in_window']}회 중 "
        f"{coverage['preceded_by_a_slowdown_block']}회가 "
        f"{coverage['lead_weeks_window']}주 안에 후퇴기를 앞에 두었다"
        f"({coverage['coverage']:.0%}).",
        "",
        definition["statement"],
        "",
        "### 동결 창(1994~2026)에서는",
        "",
        f"같은 설정으로 {frozen_summary['blocks']}개다. 닫힌 블록 "
        f"{frozen_summary['closed_blocks']}개 중 "
        f"{frozen_summary['progressed_to_contraction']}개가 침체로 나아갔고 "
        f"{frozen_summary['reverted_but_within_six_months_of_a_recession']}개가 되돌아갔지만 "
        f"침체를 앞두고 있었다. 침체 "
        f"{payload['frozen_window_recession_coverage']['recessions_in_window']}회 중 "
        f"{payload['frozen_window_recession_coverage']['preceded_by_a_slowdown_block']}회가 "
        "후퇴기를 앞에 두었다.",
        "",
    ]
    lines += _episode_table(payload["frozen_window_episodes"])
    lines += [
        "",
        "확장 역사의 블록 수와 동결 창의 블록 수가 다르다. 같은 설정이라도 **입력 길이가 "
        "다르면 표준화가 달라지기 때문**이고, 앞 단계가 적어 둔 96.5% 불일치와 같은 "
        "원인이다.",
        "",
        "## 한계에 더할 문구",
        "",
    ]
    for item in limitations(payload):
        lines.append(f"- {item}")
    lines += [
        "",
        "## 바뀌지 않은 것과, 근거가 달라진 것",
        "",
        "권고는 그대로 **persist17w**다. 이 단계는 무엇을 고를지를 다시 묻지 않았고, "
        "고른 것을 어떻게 적을지만 정했다.",
        "",
        "다만 **근거가 두 갈래로 선다는 말은 더 이상 쓸 수 없다.**",
        "",
        "- **B(평탄역)는 그대로 선다.** 판별력이 길이에 따라 단조롭게 오르다 15~21주에서 "
        "평탄해지고, 그 안에서 블록 수가 넉넉한 쪽이 17주라는 사실은 이 단계가 건드리지 "
        "않았다.",
        "- **C(유의성)는 서지 않는다.** 격자를 쓸었다는 사실을 반영하면 5% 아래가 아니고, "
        "1992년 양쪽을 가르면 유의성이 post_1992에만 있다 — 확장 역사가 벌어 준 것이 "
        "아니라 동결 창과 거의 같은 시대가 갖고 있던 것이다.",
        "",
        "그래서 권고의 근거는 **B 하나**이고, C는 그 방향을 거스르지 않았다는 정도로만 "
        "적는다. 13주 대신 17주를 쓰는 이유로는 그것으로 충분하다 — 두 값 중 무엇을 고를지가 "
        "문제였지 후퇴기 판별력이 통계적으로 확립됐는지가 문제였던 것이 아니다.",
        "",
        "이 단계는 투자 판단·섹터·비중·종목·매매 지시를 만들지 않는다.",
        "",
    ]
    return "\n".join(lines)


def limitations(payload: dict[str, Any]) -> list[str]:
    """모델 한계에 그대로 실을 문구. 세 확인에서 직접 만든다."""

    corrected = payload["corrected_significance"]
    halves = payload["consumption_split"]
    summary = payload["extended_episode_summary"]
    coverage = payload["extended_recession_coverage"]
    definition = payload["extended_definition"]
    family = corrected["family_wise"]
    out = [
        f"확장 역사의 p={corrected['nominal']['nominal_p']}는 지속 길이 8개와 중립대 배수 "
        f"{family['grid_size'] - 8}개를 쓸어 고른 **최댓값의 명목 p**다. 같은 격자를 이동 "
        f"귀무분포에서도 쓸면 족보 전체 p는 {family['family_wise_p']}이고 본페로니 상한은 "
        f"{corrected['bonferroni']['bonferroni_p']}다."
        + (
            " 보정 뒤에도 5% 아래에 남는다."
            if corrected["survives_correction"]
            else " **보정하면 5% 문턱을 넘지 못하므로 '50년 역사에서 5% 유의'라고 "
            "단정하지 않는다.**"
        ),
        "확장 역사는 1992년에 소비 도메인 구성이 바뀐다(CMRMTSPL 단독 → +RRSFS)."
        + " 양쪽 반쪽의 후퇴기 판별력은 "
        + ", ".join(
            f"{entry['half']} {entry['discrimination']}(p={entry['p_value']}, 블록 "
            f"{entry['slowdown_blocks']}개)"
            for entry in halves["halves"]
        )
        + f"다. {halves['reading']}",
        f"후퇴기는 확장 역사에서 {summary['blocks']}개 블록이고 중앙값 "
        f"{summary['median_weeks']:g}주"
        f"({summary['shortest_weeks']}~{summary['longest_weeks']}주)다. 닫힌 블록의 "
        f"{summary['forewarning_share']:.0%}만 침체로 나아갔거나 침체를 "
        f"{summary['near_recession_weeks']}주 안에 두고 있었고, 반대로 NBER 침체 "
        f"{coverage['recessions_in_window']}회 중 "
        f"{coverage['preceded_by_a_slowdown_block']}회가 후퇴기를 앞에 두었다"
        f"({coverage['coverage']:.0%}). {definition['statement']}",
        f"A(확장기 판별력)를 통과시킨 라벨은 동결 창 {payload['frozen_weeks']}주에서 "
        f"만들었고, C의 p는 확장 역사 {payload['extended_weeks']}주에서 계산했다. 두 라벨은 "
        "겹치는 구간에서 96.5%(1617/1675주)만 일치하므로 **완전히 같은 대상이 아니다.**",
    ]
    return out


def write(output: Path, payload: dict[str, Any], report: str) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "boundary_reading_report.md").write_text(report, encoding="utf-8", newline="\n")
    (output / "validation_summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )
    pd.DataFrame(payload["grid_table"]).to_csv(output / "corrected_grid.csv", index=False)
    pd.DataFrame(payload["consumption_split"]["halves"]).to_csv(
        output / "consumption_split.csv", index=False
    )
    pd.DataFrame(payload["extended_episodes"]).to_csv(
        output / "slowdown_episodes_extended.csv", index=False
    )
    pd.DataFrame(payload["frozen_window_episodes"]).to_csv(
        output / "slowdown_episodes_frozen_window.csv", index=False
    )
    pd.DataFrame(payload["extended_recession_coverage"]["detail"]).to_csv(
        output / "recession_coverage.csv", index=False
    )


def main() -> int:
    settings = load_settings()
    payload = build_payload(settings)
    write(settings.root / "outputs" / OUTPUT_NAME, payload, build_report(payload))
    print(json.dumps(payload["corrected_significance"]["statement"], ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
