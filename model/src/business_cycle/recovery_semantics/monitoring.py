"""§11. 13주 전방 모니터링의 **기제와 명세**. 13주를 여기서 기다리지 않는다.

이 모니터링이 검증하는 것과 못 하는 것을 먼저 못박는다.

검증한다   주간 실행 신뢰성, 자료 신선도, 판정 보류 거동, 국면 churn, 원시-공식 불일치,
           점수 불연속, 개정으로 인한 변화, 출력 계약 안정성.
검증하지 못한다   침체 탐지 정확도. 새 침체가 실제로 오지 않는 한 그것은 시험되지 않는다.
           13주 동안 침체가 없었다는 사실은 모델이 침체를 잡는다는 증거가 아니다.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Final

import pandas as pd

WEEKLY_SCHEDULE: Final[str] = "매주 금요일 as-of. 모델의 주간 격자(W-FRI)와 같다."
HORIZON_WEEKS: Final[int] = 13

#: 매 스냅샷마다 보존하는 지문. 하나라도 바뀌면 그 주는 비교 대상이 아니다.
PRESERVED_HASHES: Final[tuple[str, ...]] = (
    "v1_1_config",
    "selection_rule",
    "frontier_csv",
    "candidate_h",
    "candidate_i",
    "candidate_j",
)

#: 운영 사용을 **중단**시키는 조건. 경보가 아니라 정지다.
SUSPENSION_CONDITIONS: Final[tuple[str, ...]] = (
    "동결 설정 지문이 바뀌었다",
    "보호 경로가 수정됐다",
    "미래 정보 위반이 한 건이라도 나왔다",
    "판정 보류 주에 공식 국면이 나갔다",
    "연속 4주 이상 판정 보류가 이어졌다",
    "출력 계약에서 필수 필드가 빠지거나 모호한 국면 라벨이 나왔다",
    "네트워크·API 키·최신값 대체·역방향 채움 중 하나라도 쓰였다",
)

#: 경보 조건. 중단은 아니지만 사람이 봐야 한다.
ALERT_CONDITIONS: Final[tuple[str, ...]] = (
    "공식 국면이 4주 안에 두 번 이상 바뀌었다(churn)",
    "원시 국면과 공식 국면의 불일치가 8주를 넘겨 이어진다",
    "총량 수준 또는 모멘텀이 한 주에 1.0을 넘게 움직였다(점수 불연속)",
    "동행 도메인 중 신선한 것이 2개 미만이다",
    "침체 경보가 `high`로 올라갔다",
    "개정으로 지난주 공식 국면이 소급 변경됐다",
)

#: 자료가 없을 때 허용되는 거동. 최신값으로 메우는 것은 허용되지 않는다.
MISSING_DATA_BEHAVIOUR: Final[tuple[str, ...]] = (
    "발표 사이 주의 이월은 정상이다. 월간 자료 위의 주간 격자에서 당연하다.",
    "도메인 나이가 domain_stale_weeks(8.0주)를 넘으면 그 도메인을 stale로 적는다.",
    "패널 전체가 panel_silent_grace_weeks(1주)를 넘겨 조용하면 상태를 preliminary로 낮춘다.",
    "panel_silent_withhold_weeks(4주)를 넘기면 공식 국면을 내지 않고 withheld로 둔다.",
    "신선한 동행 도메인이 minimum_fresh_coincident_domains(2) 미만이면 withheld다.",
    "보류 주에도 원시 측정값은 그대로 남긴다. 상태 판정이 측정 자체를 바꾸지 않는다.",
    "빈티지가 없는 주를 최신 수정치로 메우지 않는다. 메우는 순간 실시간이 아니다.",
)


def snapshot(current_state: dict[str, Any], hashes: dict[str, str]) -> dict[str, Any]:
    """한 주의 불변 스냅샷. 기록한 뒤 고치지 않는다."""

    body = json.dumps(current_state, ensure_ascii=False, sort_keys=True, default=str)
    return {
        "as_of_date": current_state["as_of_date"],
        "recorded_at_utc": None,
        "official_current_phase": current_state["official_current_phase"],
        "raw_current_phase": current_state["raw_current_phase"],
        "phase_status": current_state["phase_status"],
        "evidence_quality": current_state["evidence_quality"],
        "phase_separation": current_state["phase_separation"],
        "recession_alert": current_state["recession_alert"],
        "hashes": {name: hashes[name] for name in PRESERVED_HASHES if name in hashes},
        "payload_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        "immutable": True,
    }


def churn(snapshots: list[dict[str, Any]], window: int = 4) -> dict[str, Any]:
    """국면 churn 보고. 창 안 전환 횟수를 센다."""

    phases = [str(item["official_current_phase"]) for item in snapshots]
    changes = [
        index
        for index in range(1, len(phases))
        if phases[index] and phases[index - 1] and phases[index] != phases[index - 1]
    ]
    worst = 0
    for index in range(len(phases)):
        inside = [c for c in changes if index - window < c <= index]
        worst = max(worst, len(inside))
    return {
        "weeks": len(phases),
        "phase_changes": len(changes),
        "worst_changes_in_a_rolling_window": worst,
        "window_weeks": window,
        "alerts": worst >= 2,
    }


def archive_path(root: Path, as_of: str) -> Path:
    """as-of 별 불변 보관 경로. 덮어쓰지 않는다."""

    moment = pd.Timestamp(as_of)
    return root / "outputs" / "recovery_semantics" / "monitoring" / f"{moment.date()}.json"


def specification(manifest: dict[str, Any]) -> str:
    """§11의 명세 문서. 13주를 기다리지 않고 규칙만 먼저 못박는다."""

    lines = [
        "# 13주 전방 운영 모니터링 명세",
        "",
        f"대상: 동결 4국면 v1.1 (`{manifest['frozen_config_sha256'][:16]}…`), "
        f"상태 `{manifest['model_status']}`.",
        "",
        "## 이 모니터링이 검증하지 **못하는** 것",
        "",
        "침체 탐지 정확도. 13주 안에 새 침체가 실제로 오지 않는 한 그것은 시험되지 않는다.",
        "침체 없이 13주가 지났다는 사실은 모델이 침체를 잡는다는 증거가 아니다.",
        "엄격 실시간 침체 에피소드는 여전히 **하나**뿐이다.",
        "",
        "## 검증하는 것",
        "",
        "주간 실행 신뢰성 · 자료 신선도 · 판정 보류 거동 · 국면 churn · 원시 대 공식 불일치 ·",
        "점수 불연속 · 개정으로 인한 변화 · 출력 계약 안정성.",
        "",
        "## 주간 일정",
        "",
        f"- {WEEKLY_SCHEDULE}",
        f"- 지평 {HORIZON_WEEKS}주.",
        "- 실행 실패는 결측이 아니라 사건이다. 그 주를 조용히 건너뛰지 않는다.",
        "",
        "## 불변 as-of 보관",
        "",
        "- `outputs/recovery_semantics/monitoring/<as-of>.json` 하나에 한 주.",
        "- 기록된 스냅샷은 고치지 않는다. 개정으로 값이 달라지면 **새 주** 기록에 적는다.",
        "- 각 스냅샷은 payload SHA-256을 함께 담는다.",
        "",
        "## 보존 지문",
        "",
        *[f"- `{name}`" for name in PRESERVED_HASHES],
        "",
        "## 허용되는 결측 거동",
        "",
        *[f"- {item}" for item in MISSING_DATA_BEHAVIOUR],
        "",
        "## 경보 조건",
        "",
        *[f"- {item}" for item in ALERT_CONDITIONS],
        "",
        "## 국면 churn 보고",
        "",
        "- 4주 이동 창 안 공식 국면 전환 횟수를 매주 보고한다.",
        "- 창 안 전환이 2회 이상이면 경보다.",
        "- 원시 국면 전환은 따로 센다. 필터가 흡수한 것과 실제로 나간 것을 섞지 않는다.",
        "",
        "## 운영 사용을 중단시키는 조건",
        "",
        *[f"- {item}" for item in SUSPENSION_CONDITIONS],
        "",
        "## 13주 뒤",
        "",
        "통과해도 `final_validated`가 되지 않는다. 잠정 상태가 유지될 뿐이다.",
        "실패하면 운영 사용을 멈추고 기록을 남긴다. 모수를 고쳐 다시 통과시키지 않는다.",
        "",
        "이 문서는 투자 판단·섹터·비중·종목·매매 지시를 담지 않는다.",
        "",
    ]
    return "\n".join(lines)
