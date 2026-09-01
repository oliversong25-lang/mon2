"""후퇴기 경계 실행기.

    python -m business_cycle.slowdown_boundary

동결 v1.1을 하나도 건드리지 않는다. `four_phase` 아래 파일은 읽기만 하고, 관측 층만
변형본으로 갈아 끼운다. 경계를 끄면 v1.1이 그대로 재현되는지 먼저 확인한다.

실시간(ALFRED) 2x2는 빈티지 688개를 다시 돌려야 해서 약 14분 걸린다. 이미 만들어 둔
산출물이 있으면 그것을 쓰고, 없으면 그 자리에서 만든다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from ..config import load_settings
from . import metrics as M
from . import scoring as SC

OUTPUT_NAME = "slowdown_boundary"
FROZEN_PATH = "outputs/four_phase_v1_1/weekly_state.csv"
FROZEN_ALFRED = "outputs/four_phase_v1_1/alfred_audit/weekly_path.csv"
FRENCH_CACHE = "data/cache/famafrench"

PHASE_LABEL = {
    "recovery": "회복기",
    "expansion": "확장기",
    "slowdown": "후퇴기",
    "contraction": "침체기",
}

#: 스윕할 경계 후보. 자연 실험이 지속을 1순위로, 폭을 보강으로 가리켰고 데드밴드는
#: 지속의 값싼 근사라 함께 시험한다.
CANDIDATES: tuple[SC.SlowdownGate, ...] = (
    SC.SlowdownGate(deadband=0.5),
    SC.SlowdownGate(deadband=1.0),
    SC.SlowdownGate(persistence_weeks=4),
    SC.SlowdownGate(persistence_weeks=6),
    SC.SlowdownGate(persistence_weeks=9),
    SC.SlowdownGate(persistence_weeks=13),
    SC.SlowdownGate(breadth_domains=2),
    SC.SlowdownGate(breadth_domains=3),
    SC.SlowdownGate(persistence_weeks=6, breadth_domains=2),
    SC.SlowdownGate(persistence_weeks=9, breadth_domains=2),
    SC.SlowdownGate(persistence_weeks=9, breadth_domains=3),
    SC.SlowdownGate(deadband=0.5, persistence_weeks=9),
)

#: 권고 설정. 사전 규칙의 기계적 1위와 같다 — 판별력 2.421로 1위이고, 결정적 지표 셋을
#: 모두 개선하며(비중 40.4%→6.4%, 진행률 0.091→0.167), 깨지면 안 되는 넷을 다 지킨다.
#: 자연 실험이 스윕 전에 가리킨 "지속이 1순위"와도 같은 모양이다.
RECOMMENDED: SC.SlowdownGate = SC.SlowdownGate(persistence_weeks=13)


def _evaluate(
    frame: pd.DataFrame, panel: pd.DataFrame, baseline_phase: pd.Series, label: str
) -> dict[str, Any]:
    phase = frame["official_phase"]
    return {
        "gate": label,
        "shape": M.shape(phase),
        "discrimination": M.discrimination(phase, panel),
        "progression": M.progression(phase),
        "recognition": M.recognition(phase, baseline_phase),
        "nber": M.nber(phase),
        "breadth_gate_holds": M.breadth_gate_holds(frame),
        "negative_level_expansion_weeks": M.negative_level_expansion(frame),
        "current_call": str(phase.iloc[-1]),
    }


def _row(entry: dict[str, Any]) -> str:
    shape = entry["shape"]
    slowdown = entry["discrimination"]["slowdown"]
    progression = entry["progression"]
    recognition = entry["recognition"]
    return (
        f"| `{entry['gate']}` | {shape['transitions']} | "
        f"{shape['phases_shorter_than_four_weeks']} | "
        f"{slowdown['ratio_to_chance']} | "
        f"{shape['phase_shares']['slowdown']:.1%} | "
        f"{progression['progressed_to_contraction']}/"
        f"{progression['closed_slowdown_blocks']} | "
        f"{recognition['contraction']['max_delay_weeks']}주 | "
        f"{recognition['recovery']['max_delay_weeks']}주 | "
        f"{entry['nber']['false_positive_episodes']} |"
    )


def _matrix_rows(cells: dict[str, dict[str, Any]]) -> list[str]:
    lines = [
        "| 칸 | 설정 | 전이 | 4주 미만 | 회복 | 확장 | 후퇴 | 침체 | 보류 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    korean = {
        "baseline": "v1.1 기준선",
        "gate_only": "게이트만",
        "boundary_only": "경계만",
        "boundary_and_gate": "경계+게이트",
    }
    for key, entry in cells.items():
        shape = entry["shape"]
        weeks = shape["phase_weeks"]
        lines.append(
            f"| {korean[key]} | `{entry['gate']}` | {shape['transitions']} | "
            f"{shape['phases_shorter_than_four_weeks']} | {weeks['recovery']} | "
            f"{weeks['expansion']} | **{weeks['slowdown']}** | {weeks['contraction']} | "
            f"{shape['withheld_weeks']} |"
        )
    return lines


def _decisive_rows(cells: dict[str, dict[str, Any]]) -> list[str]:
    lines = [
        "| 칸 | 후퇴기 판별력 | p | 후퇴기 비중 | 진행 | 침체 지연 | 회복 지연 | NBER 오탐 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    korean = {
        "baseline": "v1.1 기준선",
        "gate_only": "게이트만",
        "boundary_only": "경계만",
        "boundary_and_gate": "경계+게이트",
    }
    for key, entry in cells.items():
        slowdown = entry["discrimination"]["slowdown"]
        progression = entry["progression"]
        mark = "**" if slowdown["ratio_to_chance"] and slowdown["ratio_to_chance"] >= 1.0 else ""
        lines.append(
            f"| {korean[key]} | {mark}{slowdown['ratio_to_chance']}{mark} | "
            f"{slowdown['p_value']} | "
            f"{entry['shape']['phase_shares']['slowdown']:.1%} | "
            f"{progression['progressed_to_contraction']}/"
            f"{progression['closed_slowdown_blocks']} "
            f"({progression['progression_rate']}) | "
            f"{entry['recognition']['contraction']['max_delay_weeks']}주 | "
            f"{entry['recognition']['recovery']['max_delay_weeks']}주 | "
            f"{entry['nber']['false_positive_episodes']} |"
        )
    return lines


def _flat(entry: dict[str, Any], key: str) -> dict[str, Any]:
    """표 한 줄. CSV와 보고서가 같은 값을 보게 하려고 한 곳에서 만든다."""

    shape = entry["shape"]
    slowdown = entry.get("discrimination", {}).get("slowdown", {})
    progression = entry["progression"]
    return {
        "cell": key,
        "gate": entry["gate"],
        "transitions": shape["transitions"],
        "phases_shorter_than_four_weeks": shape["phases_shorter_than_four_weeks"],
        "slowdown_weeks": shape["phase_weeks"]["slowdown"],
        "slowdown_share": shape["phase_shares"]["slowdown"],
        "expansion_weeks": shape["phase_weeks"]["expansion"],
        "recovery_weeks": shape["phase_weeks"]["recovery"],
        "contraction_weeks": shape["phase_weeks"]["contraction"],
        "withheld_weeks": shape["withheld_weeks"],
        "slowdown_discrimination": slowdown.get("ratio_to_chance"),
        "slowdown_discrimination_p": slowdown.get("p_value"),
        "progression_rate": progression["progression_rate"],
        "progressed_to_contraction": progression["progressed_to_contraction"],
        "closed_slowdown_blocks": progression["closed_slowdown_blocks"],
        "contraction_delay_weeks": entry["recognition"]["contraction"]["max_delay_weeks"],
        "recovery_delay_weeks": entry["recognition"]["recovery"]["max_delay_weeks"],
        "nber_false_positive_episodes": entry["nber"]["false_positive_episodes"],
        "breadth_gate_holds": entry["breadth_gate_holds"],
        "current_call": entry.get("current_call"),
    }


def write(output: Path, payload: dict[str, Any], report: str) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "slowdown_boundary_report.md").write_text(report, encoding="utf-8", newline="\n")
    (output / "validation_summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )
    pd.DataFrame([_flat(entry, entry["gate"]) for entry in payload["sweep"]]).to_csv(
        output / "sweep.csv", index=False
    )
    pd.DataFrame([_flat(entry, key) for key, entry in payload["matrix"].items()]).to_csv(
        output / "matrix.csv", index=False
    )
    realtime = {key: entry for key, entry in payload["realtime"].items() if key != "_reproduction"}
    if realtime:
        pd.DataFrame([_flat(entry, key) for key, entry in realtime.items()]).to_csv(
            output / "realtime_matrix.csv", index=False
        )


def main() -> int:
    from .report import build_payload, build_report

    settings = load_settings()
    payload = build_payload(settings)
    write(settings.root / "outputs" / OUTPUT_NAME, payload, build_report(payload))
    # 콘솔 인코딩이 UTF-8이 아닐 수 있다. 산출물은 이미 UTF-8로 썼으므로 여기서
    # 실패해 단계 전체가 죽는 일은 없어야 한다.
    print(json.dumps(payload["verdict"]["statement"], ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
