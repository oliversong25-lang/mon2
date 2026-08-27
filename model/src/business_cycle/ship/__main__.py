"""출하 번들 실행기.

    python -m business_cycle.ship

`outputs/ship/weekly_path.csv`(persist17w 실시간 경로)가 먼저 있어야 한다. 그 파일을
만드는 일이 688 빈티지를 다시 도는 일이라 여기서 매번 돌리지 않는다.

## 검증 숫자를 여기서 못박는다

출하 전에 세 가지를 확인한다 — 현재 판정, 실시간 전환 횟수, 후퇴기 주수. 하나라도
어긋나면 파일을 쓰지 않고 멈춘다. 기대값은 트랙 22가 persist17w에서 잰 값이다.

**정정 기록.** 앞선 출하 지시서는 전환 28회·후퇴기 49주를 기대값으로 적었는데, 그것은
persist13w의 값이었다(`outputs/slowdown_boundary/realtime_matrix.csv`의 `boundary_only`
행, 같은 실행의 `validation_summary.json`에 `/matrix/boundary_only/gate = persist13w`로
기록돼 있다). 트랙 22가 persist17w로 조정하면서 20회·33주가 됐다. 기대값을 그렇게
정정하고 그 사실을 여기 남긴다.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import pandas as pd

from ..config import load_settings
from ..recovery_semantics import manifest as MF
from ..rotation_rerun.labels17 import GATE
from . import bundle as B
from . import realtime as RT

OUTPUT_NAME: Final[str] = "ship"

#: 트랙 22가 persist17w 실시간 경로에서 잰 값. 하나라도 어긋나면 출하하지 않는다.
EXPECTED: Final[dict[str, Any]] = {
    "current_official_phase": "expansion",
    "transitions": 20,
    "slowdown_weeks": 33,
}

#: 정정 이전에 적혀 있던 값과 그 출처. 기록으로 남긴다.
SUPERSEDED_EXPECTATION: Final[dict[str, Any]] = {
    "transitions": 28,
    "slowdown_weeks": 49,
    "actually_from": "persist13w",
    "evidence": (
        "outputs/slowdown_boundary/realtime_matrix.csv의 boundary_only 행이 전환 28·"
        "후퇴기 49주이고, 같은 실행의 validation_summary.json이 "
        "/matrix/boundary_only/gate = persist13w로 적고 있다."
    ),
    "why_corrected": (
        "트랙 21이 persist13w를 권고했을 때의 실시간 수치다. 트랙 22가 평탄역(15~21주)과 "
        "블록 수를 근거로 persist17w로 조정하면서 20회·33주가 됐다."
    ),
}

#: 트랙 16 전이 게이트. 이 단계는 **적용하지 않는다.**
TRANSITION_GATE_APPLIED: Final[bool] = False


def _phase_series(frame: pd.DataFrame) -> pd.Series:
    phase = frame["official_phase"].fillna("").astype(str)
    return phase.mask(frame["phase_status"].astype(str).eq("withheld"), "")


def _shape(frame: pd.DataFrame) -> dict[str, Any]:
    phase = _phase_series(frame)
    values = [str(item) for item in phase.tolist()]
    transitions = sum(
        1
        for i in range(1, len(values))
        if values[i - 1] and values[i] and values[i - 1] != values[i]
    )
    counts = {
        name: values.count(name) for name in ("recovery", "expansion", "slowdown", "contraction")
    }
    return {
        "weeks": len(values),
        "current_official_phase": values[-1],
        "transitions": transitions,
        "phase_weeks": counts,
        "withheld_weeks": sum(1 for item in values if not item),
    }


def build_payload(settings: Any) -> dict[str, Any]:
    root = settings.root
    output = root / "outputs" / OUTPUT_NAME
    path_csv = output / "weekly_path.csv"
    if not path_csv.exists():
        raise FileNotFoundError(
            f"{path_csv}가 없습니다. persist17w 실시간 경로를 먼저 만들어야 합니다."
        )

    variant_path = B.read_csv(path_csv)
    frozen_path = B.read_csv(root / "outputs/four_phase_v1_1/alfred_audit/weekly_path.csv")
    agreement = RT.agrees_with_frozen(variant_path, frozen_path)
    if not agreement["agrees"]:
        raise ValueError(f"동결 경로와 임계값 무관 열이 어긋납니다: {agreement['disagreeing']}")

    shape = _shape(variant_path)
    mismatched = {
        key: {"expected": value, "measured": shape[key]}
        for key, value in EXPECTED.items()
        if key != "slowdown_weeks" and shape.get(key) != value
    }
    if shape["phase_weeks"]["slowdown"] != EXPECTED["slowdown_weeks"]:
        mismatched["slowdown_weeks"] = {
            "expected": EXPECTED["slowdown_weeks"],
            "measured": shape["phase_weeks"]["slowdown"],
        }
    if mismatched:
        raise ValueError(f"검증 숫자가 어긋납니다. 출하하지 않습니다: {mismatched}")

    # 현재상태는 persist17w 경로 위에서 다시 만든다. 회복 인식 지연만 v1.1 것을 이어받는다 —
    # 후퇴기 게이트는 회복 게이트를 건드리지 않고, 트랙 21·22가 침체·회복 인식 지연이
    # 0주 그대로임을 확인했다. 그 사실을 값 옆에 적어 둔다.
    v11_state = B.read_json(root / "outputs/state_semantics/current_state_output.json")
    warning = v11_state["recovery_latency_warning"]
    decomposition = {
        "calendar_band": warning["band"],
        "calendar_recovery_latency_weeks": warning["calendar_recovery_latency_weeks"],
        "evidence_availability_adjusted_latency_weeks": warning[
            "evidence_availability_adjusted_latency_weeks"
        ],
        "limitation_label": warning["limitation"],
    }
    indexed = variant_path.set_index("as_of")
    # `current_state`는 감사 실행이 넘겨 주던 provenance 모양을 기대한다. 앱이 읽는
    # 산출물에는 평평하게 저장돼 있으므로 그 모양으로 되돌려 넘긴다 — 값은 그대로다.
    provenance = {
        "hashes": {"v1_1_config": v11_state["provenance"]["frozen_config_sha256"]},
        "expected_source_commit": v11_state["provenance"]["source_commit"],
    }
    current = MF.current_state(indexed, decomposition, provenance)
    current["recovery_latency_warning"]["carried_from"] = "v1.1"
    current["recovery_latency_warning"]["why_carried"] = (
        "후퇴기 게이트는 회복 게이트를 건드리지 않는다. 트랙 21·22가 실시간 경로에서 "
        "침체·회복 인식 지연이 0주 그대로임을 확인했으므로 v1.1 값을 그대로 이어받는다."
    )

    maturity = B.maturity(B.read_json(root / "outputs/phase_maturity/validation_summary.json"))
    market = B.read_json(root / "outputs/market_risk/validation_summary.json")
    variance = B.variance_distribution(market["forward"]["13"], 13)

    return {
        "stage": OUTPUT_NAME,
        "executed_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "variant": {
            "id": GATE.name,
            "label": "확장·후퇴 경계 수정 (지속 17주)",
            "differs_from_v1_1": True,
            "what_changed": (
                "후퇴기가 확장기와 나뉘는 자리가 모멘텀 0이었다. 그 자리를 지속 요건 "
                "17주로 옮겨, 애매한 주가 후퇴기로 흘러들지 않게 했다."
            ),
            "frozen_config_sha256": current["provenance"]["frozen_config_sha256"],
            "transition_gate_applied": TRANSITION_GATE_APPLIED,
            "why_no_transition_gate": (
                "트랙 21의 2x2에서 그 게이트는 경계를 고친 뒤 장기 경로 전환을 97에서 "
                "96으로 바꿀 뿐이었고, 둘을 함께 걸면 판별력 2.421→2.302, 진행률 "
                "0.167→0.091로 나빠졌다. 증상 처방이었고 원인은 경계였다."
            ),
        },
        "verification": {
            "expected": dict(EXPECTED),
            "measured": {
                "current_official_phase": shape["current_official_phase"],
                "transitions": shape["transitions"],
                "slowdown_weeks": shape["phase_weeks"]["slowdown"],
            },
            "agrees": True,
            "superseded_expectation": dict(SUPERSEDED_EXPECTATION),
        },
        "frozen_path_agreement": agreement,
        "shape": shape,
        "current_state": current,
        "maturity": maturity,
        "variance_distribution": variance,
    }


def write(output: Path, payload: dict[str, Any]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "current_state_output.json").write_text(
        json.dumps(payload["current_state"], ensure_ascii=False, indent=2),
        encoding="utf-8",
        newline="\n",
    )
    for name in ("variant", "maturity", "variance_distribution", "verification"):
        (output / f"{name}.json").write_text(
            json.dumps(payload[name], ensure_ascii=False, indent=2),
            encoding="utf-8",
            newline="\n",
        )
    (output / "ship_summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )


def main() -> int:
    settings = load_settings()
    payload = build_payload(settings)
    write(settings.root / "outputs" / OUTPUT_NAME, payload)
    shape = payload["shape"]
    print(f"출하 번들 · 변형 {payload['variant']['id']}")
    print(
        f"  현재 {shape['current_official_phase']} · 전환 {shape['transitions']}회 · "
        f"후퇴기 {shape['phase_weeks']['slowdown']}주 · 보류 {shape['withheld_weeks']}주"
    )
    print(f"  동결 경로 대조 {payload['frozen_path_agreement']['weeks_compared']}주 일치")
    print(f"  성숙도 검증 국면 {payload['maturity']['validated_phases']}")
    print(f"  트랙 16 전이 게이트 적용 {payload['variant']['transition_gate_applied']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
