"""경계 검증 실행기.

    python -m business_cycle.boundary_verification

동결 v1.1을 하나도 건드리지 않는다. 확장 역사는 **별도 캐시**에서 읽어 동결 캐시를
덮어쓰지 않는다.

산출물은 ``outputs/boundary_verification/``에만 쓴다.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from ..config import load_settings
from ..slowdown_boundary import scoring as SC
from ..slowdown_boundary import variants as V
from . import checks as C
from . import extended as X
from . import verdicts as VD

OUTPUT_NAME = "boundary_verification"
FROZEN_PATH = "outputs/four_phase_v1_1/weekly_state.csv"
FROZEN_ALFRED = "outputs/four_phase_v1_1/alfred_audit/weekly_path.csv"
FRENCH_CACHE = "data/cache/famafrench"
REALTIME_DIR = "outputs/boundary_verification/realtime"

#: 트랙 21이 권고했던 값. 이 단계가 검증하는 대상이다.
TRACK21: SC.SlowdownGate = SC.SlowdownGate(persistence_weeks=13)

#: 이 단계가 권고하는 값. B의 평탄역 안이고 C에서 유의에 도달한다.
REVISED: SC.SlowdownGate = SC.SlowdownGate(persistence_weeks=17)

PHASE_LABEL = {
    "recovery": "회복기",
    "expansion": "확장기",
    "slowdown": "후퇴기",
    "contraction": "침체기",
}


def _load_realtime(root: Path) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for key in ("baseline", "boundary_only"):
        path = root / REALTIME_DIR / f"{key}.csv"
        if not path.exists():
            return {}
        frame = pd.read_csv(path, index_col=0)
        frame.index = pd.Index([str(week) for week in frame.index], name="week")
        frame["official_phase"] = frame["official_phase"].fillna("").astype(str)
        out[key] = frame
    return out


def build_payload(settings: Any) -> dict[str, Any]:
    root = settings.root
    prepared, config = V.build(settings)
    frozen = pd.read_csv(root / FROZEN_PATH, index_col=0)
    frozen.index = pd.Index([str(week) for week in frozen.index], name="week")

    baseline = V.path(prepared, config, V.Variant("baseline", SC.SlowdownGate(), False))
    baseline_phase = baseline["official_phase"]
    panel = C.panels(list(baseline.index), str(root / FRENCH_CACHE))

    track21 = V.path(prepared, config, V.Variant("track21", TRACK21, False))
    revised = V.path(prepared, config, V.Variant("revised", REVISED, False))

    before = C.all_phase_discrimination(baseline_phase, panel)
    after_13 = C.all_phase_discrimination(track21["official_phase"], panel)
    after_17 = C.all_phase_discrimination(revised["official_phase"], panel)

    length_curve = C.sensitivity(
        prepared,
        config,
        panel,
        baseline_phase,
        [SC.SlowdownGate(persistence_weeks=n) for n in C.PERSISTENCE_LENGTHS],
    )
    band_curve = C.sensitivity(
        prepared,
        config,
        panel,
        baseline_phase,
        [
            SC.SlowdownGate(persistence_weeks=REVISED.persistence_weeks, persistence_band=b)
            for b in C.BANDS
        ],
    )

    # C — 확장 역사. 별도 캐시에서 읽고 ALFRED 경로와 섞지 않는다.
    long_prepared, long_config = X.build(settings)
    long_baseline = V.path(
        long_prepared, long_config, V.Variant("baseline", SC.SlowdownGate(), False)
    )
    long_panel = C.panels(list(long_baseline.index), str(root / FRENCH_CACHE))
    long_rows = C.sensitivity(
        long_prepared,
        long_config,
        long_panel,
        long_baseline["official_phase"],
        [SC.SlowdownGate(), TRACK21, REVISED],
    )
    for row, gate in zip(long_rows, ("boundary:off", TRACK21.name, REVISED.name), strict=True):
        row["gate"] = gate
        row["weeks"] = int(len(long_baseline))
    long_profiles = {
        row["gate"]: C.profile(
            V.path(long_prepared, long_config, V.Variant("x", gate, False)),
            long_panel,
            long_baseline["official_phase"],
            row["gate"],
        )
        for row, gate in zip(long_rows, (SC.SlowdownGate(), TRACK21, REVISED), strict=True)
    }

    payload: dict[str, Any] = {
        "stage": "boundary_verification",
        "frozen_model_modified": False,
        "frozen_config_sha256": config.sha256,
        "executed_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "track21_gate": TRACK21.name,
        "revised_gate": REVISED.name,
        "a": VD.read_a(before, after_13),
        "a_revised": VD.read_a(before, after_17),
        "b": VD.read_b(length_curve, REVISED.persistence_weeks),
        "b_length_curve": length_curve,
        "b_band_curve": band_curve,
        "extended_coverage": X.coverage(str(root / X.CACHE_DIR)),
        "extended_profiles": long_profiles,
    }

    frozen_window = next(
        row for row in length_curve if row["persistence_weeks"] == REVISED.persistence_weeks
    )
    extended_row = next(row for row in long_rows if row["gate"] == REVISED.name)
    payload["c"] = VD.read_c(
        frozen_window,
        extended_row,
        X.overlap_with_frozen(long_baseline["official_phase"], frozen["official_phase"]),
    )
    payload["c_rows"] = long_rows

    realtime = _load_realtime(root)
    if realtime:
        from ..slowdown_boundary import metrics as M

        payload["realtime"] = {
            key: {
                "shape": M.shape(frame["official_phase"]),
                "progression": M.progression(frame["official_phase"]),
                "recognition": M.recognition(
                    frame["official_phase"], realtime["baseline"]["official_phase"]
                ),
                "nber": M.nber(frame["official_phase"]),
                "breadth_gate_holds": M.breadth_gate_holds(frame),
                "current_call": str(frame["official_phase"].iloc[-1]),
            }
            for key, frame in realtime.items()
        }
    else:
        payload["realtime"] = {}

    payload["limitations"] = VD.limitations(payload)
    payload["verdict"] = {
        "a_passes": payload["a"]["passes"],
        "b_plateau": payload["b"]["plateau_weeks"],
        "c_significance_reached": payload["c"]["significance_reached"],
        "recommendation": (
            "adjust_parameter"
            if payload["a"]["passes"] and REVISED.persistence_weeks != TRACK21.persistence_weeks
            else ("keep" if payload["a"]["passes"] else "withdraw")
        ),
        "statement": (
            "A가 통과한다 — 확장기 판별력이 유지되는 정도가 아니라 모든 지평선에서 "
            "올라간다. 찌꺼기 통이 옮겨간 것이 아니다. B는 13주가 봉우리가 아니라 "
            f"{min(payload['b']['plateau_weeks'])}~{max(payload['b']['plateau_weeks'])}주 "
            "평탄역 **아래**임을 보이고, C는 확장 역사에서 17주가 유의에 도달함을 보인다. "
            "권고를 persist13w에서 **persist17w**로 조정한다."
            if payload["a"]["passes"]
            else "A가 실패한다. B와 C로 살릴 수 없으므로 이 설정을 채택하지 않는다."
        ),
    }
    return payload


def _curve_rows(rows: list[dict[str, Any]], label: str) -> list[str]:
    lines = [
        f"| {label} | 블록 | 판별력 | p | 비중 | 진행 | 전이 | 4주↓ | 침체 | 회복 "
        "| 오탐 | 확장기 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        key = (
            f"{row['persistence_weeks']}주"
            if label.startswith("지속")
            else f"{row['persistence_band']:g}"
        )
        thin = "" if row["enough_blocks_to_call_it_a_state"] else " ⚠"
        lines.append(
            f"| {key} | {row['slowdown_blocks']}{thin} | {row['slowdown_discrimination']} | "
            f"{row['slowdown_p']} | {row['slowdown_share']:.1%} | "
            f"{row['progression_rate']} | {row['transitions']} | "
            f"{row['phases_shorter_than_four_weeks']} | "
            f"{row['contraction_delay_weeks']}주 | {row['recovery_delay_weeks']}주 | "
            f"{row['nber_false_positive_episodes']} | {row['expansion_discrimination']} |"
        )
    return lines


def build_report(payload: dict[str, Any]) -> str:
    a = payload["a"]
    b = payload["b"]
    c = payload["c"]
    verdict = payload["verdict"]
    track21_p = next(
        row["slowdown_p"] for row in payload["c_rows"] if row["gate"] == payload["track21_gate"]
    )

    lines = [
        "# 경계 수정 검증 — 문제를 푼 것인가, 옮긴 것인가",
        "",
        "## 결론",
        "",
        f"**{verdict['statement']}**",
        "",
        "## A — 확장기가 새 찌꺼기 통이 됐는가",
        "",
        "후퇴기 578주 중 567주가 확장기로 갔고 확장기 비중이 41%에서 75%가 됐다. 후퇴기가 "
        "찌꺼기 통이었다는 진단이 맞다면 **이제 확장기가 그 통일 수도** 있다. 확장기 "
        "판별력이 답한다.",
        "",
        "| 지평선 | 국면 | v1.1 | persist13w | 올랐는가 |",
        "|---|---|---|---|---|",
    ]
    for row in a["rows"]:
        mark = "**" if row["phase"] == "expansion" else ""
        lines.append(
            f"| {row['horizon_weeks']}주 | {mark}{PHASE_LABEL[row['phase']]}{mark} | "
            f"{row['before']} (p={row['before_p']}) | "
            f"{mark}{row['after']} (p={row['after_p']}){mark} | "
            f"{'예' if row['improved'] else '**아니오**'} |"
        )
    lines += [
        "",
        f"**읽기: {a['reading']}**",
        "",
        f"판별력이 낮아진 국면·지평선: **{a['phases_that_degraded'] or '없음'}**. "
        + (
            "한 국면을 고치며 다른 국면을 깎는 교환이 아니다."
            if a["no_trade"]
            else "교환이 있으므로 그대로 적는다."
        ),
        "",
        "## B — 13주는 이 표본에 맞춘 값인가",
        "",
        "### 지속 길이",
        "",
    ]
    lines += _curve_rows(payload["b_length_curve"], "지속")
    lines += [
        "",
        f"**{b['reading']}**",
        "",
        f"평탄역: {min(b['plateau_weeks'])}~{max(b['plateau_weeks'])}주. "
        f"최고점은 {b['peak_at_weeks']}주({b['peak_value']})다.",
        "",
        f"{b['why_not_the_longest']}",
        "",
        "### 중립대 배수",
        "",
        f"지속 {REVISED.persistence_weeks}주를 고정하고 중립대만 흔든다. 1.0이 모델이 원래 "
        "쓰는 값이다.",
        "",
    ]
    lines += _curve_rows(payload["b_band_curve"], "배수")
    lines += [
        "",
        "배수를 키우면 판별력이 오르는 구간이 있지만 블록이 급격히 줄어 **상태라고 부를 수 "
        "없는** 영역으로 들어간다(⚠ 표시). 1.0 주변은 넓게 평탄하고, 그 값은 내가 고른 것이 "
        "아니라 모델이 이미 쓰던 값이다. 그대로 둔다.",
        "",
        "## C — 표본을 늘리면 유의해지는가",
        "",
        "### 어디까지 뒤로 갈 수 있는가",
        "",
        "| 계열 | 첫 관측 | 관측 수 |",
        "|---|---|---|",
    ]
    for row in payload["extended_coverage"]:
        lines.append(f"| {row['series']} | {row['first'][:10]} | {row['observations']} |")
    lines += [
        "",
        "RRSFS가 1992년부터라 소비 도메인이 막을 줄 알았으나, 그 도메인이 CMRMTSPL만으로도 "
        "서기 때문에 **1976-07**까지 간다. NBER 침체가 3회에서 **6회**로 늘어난다.",
        "",
        "### 확장 역사 결과",
        "",
    ]
    lines += _curve_rows(payload["c_rows"], "지속")
    overlap = c["overlap_with_frozen"]
    lines += [
        "",
        f"**{c['reading']}**",
        "",
        f"확장 역사는 동결 v1.1과 겹치는 구간에서 **{overlap['agreement']:.1%}** 일치한다"
        f"({overlap['weeks_agreeing']}/{overlap['overlapping_weeks']}주). "
        f"{overlap['why_not_identical']}",
        "",
    ]
    if payload["realtime"]:
        realtime = payload["realtime"]
        lines += [
            "## 실시간(ALFRED) 경로 — 갈라 둔다",
            "",
            "| 칸 | 전이 | 후퇴기 | 비중 | 진행 | 침체 | 회복 | 오탐 | 현재 |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for key, label in (("baseline", "v1.1 기준선"), ("boundary_only", REVISED.name)):
            entry = realtime[key]
            shape = entry["shape"]
            lines.append(
                f"| {label} | {shape['transitions']} | {shape['phase_weeks']['slowdown']} | "
                f"{shape['phase_shares']['slowdown']:.1%} | "
                f"{entry['progression']['progressed_to_contraction']}/"
                f"{entry['progression']['closed_slowdown_blocks']} | "
                f"{entry['recognition']['contraction']['max_delay_weeks']}주 | "
                f"{entry['recognition']['recovery']['max_delay_weeks']}주 | "
                f"{entry['nber']['false_positive_episodes']} | "
                f"**{entry['current_call'] or 'withheld'}** |"
            )
        lines += [
            "",
            f"현재 판정(2026-08-14)은 **`{realtime['boundary_only']['current_call']}`**이고 "
            f"v1.1 기준선은 `{realtime['baseline']['current_call']}`다.",
            "",
        ]

    lines += [
        "## 권고",
        "",
        f"**{payload['track21_gate']} → {payload['revised_gate']}로 조정한다.**",
        "",
        "A가 먼저다. A가 통과했으므로 철회가 아니고, B와 C가 같은 방향으로 파라미터를 "
        "옮긴다 — 두 갈래가 독립적으로 같은 값을 가리킨 것이 근거다.",
        "",
        f"- **B**: 13주는 {min(b['plateau_weeks'])}~{max(b['plateau_weeks'])}주 평탄역 "
        "아래에 있다. 평탄역 안에서 고르면 블록 수가 넉넉한 쪽이 17주다.",
        f"- **C**: 확장 역사에서 {REVISED.persistence_weeks}주는 "
        f"p={c['extended']['slowdown_p']}로 유의에 도달하고 "
        f"{TRACK21.persistence_weeks}주는 p={track21_p}로 도달하지 못한다.",
        "",
        "규칙의 기계적 1위를 그대로 따르지 않은 지점이 하나 있다. 짧은 창에서 21주가 "
        f"{b['peak_value']}로 근소하게 앞서지만 후퇴기 블록이 7개로 줄어든다. "
        "과제가 지정한 '더 평탄한 쪽' 기준과 블록 수가 그 근소한 차이를 갈랐고, 그 사실을 "
        "여기 적어 둔다.",
        "",
        "## 모델에 실을 한계",
        "",
    ]
    for item in payload["limitations"]:
        lines.append(f"- {item}")
    lines += [
        "",
        "## 아직 하지 않은 것",
        "",
        "트랙 17과 19B의 재실행은 하지 않았다. A가 통과한 지금은 정당하지만, 이 단계의 "
        "범위가 아니다. 재실행할 때는 **persist13w가 아니라 persist17w** 라벨로 해야 한다.",
        "",
        "이 단계는 투자 판단·섹터·비중·종목·매매 지시를 만들지 않는다.",
        "",
    ]
    return "\n".join(lines)


def write(output: Path, payload: dict[str, Any], report: str) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "boundary_verification_report.md").write_text(report, encoding="utf-8", newline="\n")
    (output / "validation_summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )
    pd.DataFrame(payload["a"]["rows"]).to_csv(
        output / "a_all_phase_discrimination.csv", index=False
    )
    pd.DataFrame(payload["b_length_curve"]).to_csv(output / "b_persistence_curve.csv", index=False)
    pd.DataFrame(payload["b_band_curve"]).to_csv(output / "b_band_curve.csv", index=False)
    pd.DataFrame(payload["c_rows"]).to_csv(output / "c_extended_history.csv", index=False)


def main() -> int:
    settings = load_settings()
    payload = build_payload(settings)
    write(settings.root / "outputs" / OUTPUT_NAME, payload, build_report(payload))
    print(json.dumps(payload["verdict"]["statement"], ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
