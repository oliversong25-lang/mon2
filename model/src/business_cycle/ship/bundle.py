"""현재상태·성숙도·분산 분포를 앱이 읽을 모양으로 모은다.

## 성숙도는 **검증 범위를 데이터로** 달고 나간다

트랙 18은 후반부 신호가 네 국면에 대칭적으로 작동하지 않는다는 것을 찾았다. 경과 기간
대조군을 이기고 블록 안 위치 검정도 통과하는 국면은 **확장기뿐**이다. 회복기와 후퇴기는
대조군에 지고, 침체기는 에피소드가 다섯뿐이라 확인 자체가 안 된다.

그 제약이 보고서에만 있으면 화면은 그것을 모른 채 네 국면 모두에 신호를 붙인다. 그래서
신호와 **같은 자리**에 `validated`와 왜 그런지를 넣는다 — 소비자가 문서를 읽지 않고도
확장기만 검증됐다는 것을 볼 수 있어야 한다.

## 분산 분포는 **두 묶음**으로 낸다

네 숫자를 나란히 놓으면 자료가 받쳐 주지 않는 순위가 보인다. 회복기 17%와 확장기 29%는
에피소드 4개와 17개에서 나온 값이라 서로 구분되지 않고, 후퇴기와 침체기는 둘 다 52%다.
그래서 기본은 두 묶음이고, 국면별 숫자는 에피소드 수를 달아 상세로만 낸다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

import pandas as pd

from ..phase_returns.labels import PHASES

#: 분산 분포의 두 묶음. 순서는 순환 순서를 따른다.
VARIANCE_GROUPS: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    ("lower", ("recovery", "expansion")),
    ("higher", ("slowdown", "contraction")),
)

GROUP_LABEL: Final[dict[str, str]] = {
    "lower": "회복기·확장기",
    "higher": "후퇴기·침체기",
}

#: 상세를 기본으로 펼쳐 둘 국면. 에피소드가 가장 많아 국면별 숫자를 그나마 읽을 수 있다.
DETAIL_PHASE: Final[str] = "expansion"

#: 분산 분포가 나온 경로. 실시간이 아니라 **최종 수정치**이며, 섞어 읽으면 안 된다.
VARIANCE_PATH: Final[str] = "revised_latest_vintage"


def maturity(summary: dict[str, Any]) -> dict[str, Any]:
    """성숙도 신호와 그 검증 범위. 범위는 산문이 아니라 데이터다."""

    verdict = summary["verdict"]
    table = verdict["with_gap_transforms"]
    validated = set(verdict["phases_where_the_late_signal_beats_the_duration_control"])

    scope: list[dict[str, Any]] = []
    for name in PHASES:
        entry = table[name]
        beats = bool(entry["beats_duration_on_successor"])
        episodes = int(entry["episodes"])
        if name in validated:
            reason = (
                f"경과 기간 대조군 {entry['successor_rate']['duration_only']}를 "
                f"{entry['successor_rate']['late_signal']}로 이기고, 블록 안 위치 검정도 "
                f"p={entry['within_run_shift_p']}로 통과한다."
            )
        elif not beats:
            reason = (
                f"경과 기간 대조군 {entry['successor_rate']['duration_only']}를 이기지 "
                f"못한다({entry['successor_rate']['late_signal']}). 신호가 아니라 "
                "'오래됐다'가 설명한다."
            )
        elif episodes <= 5:
            reason = (
                f"에피소드가 {episodes}개뿐이라 확인할 수 없다. 대조군을 이기는 것처럼 "
                f"보이지만 블록 안 위치 검정이 p={entry['within_run_shift_p']}로 "
                "우연과 구분되지 않는다."
            )
        else:
            reason = f"블록 안 위치 검정이 p={entry['within_run_shift_p']}로 통과하지 못한다."
        scope.append(
            {
                "phase": name,
                "validated": name in validated,
                "episodes": episodes,
                "beats_duration_control": beats,
                "within_run_shift_p": entry["within_run_shift_p"],
                "successor_rate": entry["successor_rate"],
                "why": reason,
            }
        )

    current = dict(summary["current"])
    current["validated"] = current.get("phase") in validated
    if not current["validated"]:
        current["wording"] = ""
    return {
        "current": current,
        "validation_scope": scope,
        "validated_phases": sorted(validated),
        "symmetric_across_the_four_phases": bool(verdict["symmetric_across_the_four_phases"]),
        "statement": verdict["statement"],
        "form": "state_description",
        "note": (
            "이 신호는 **확장기에서만** 검증됐다. 다른 국면에서는 화면에 문구를 만들지 "
            "않는다 — 검증되지 않은 국면에 같은 문장을 붙이면 검증된 것처럼 보인다."
        ),
    }


def variance_distribution(rows: list[dict[str, Any]], horizon: int) -> dict[str, Any]:
    """전방 h주 음수 주 비율. 기본은 두 묶음, 상세는 에피소드 수를 달고."""

    by_phase = {row["phase"]: row for row in rows}
    detail = [
        {
            "phase": name,
            "share_negative": by_phase[name]["share_negative"],
            "observations": by_phase[name]["observations"],
            "episodes": by_phase[name]["episodes"],
        }
        for name in PHASES
        if name in by_phase
    ]

    groups: list[dict[str, Any]] = []
    for key, members in VARIANCE_GROUPS:
        present = [by_phase[name] for name in members if name in by_phase]
        weeks = sum(int(row["observations"]) for row in present)
        negative = sum(float(row["share_negative"]) * int(row["observations"]) for row in present)
        groups.append(
            {
                "group": key,
                "label": GROUP_LABEL[key],
                "phases": list(members),
                "share_negative": round(negative / weeks, 4) if weeks else None,
                "observations": weeks,
                "episodes": sum(int(row["episodes"]) for row in present),
            }
        )

    return {
        "horizon_weeks": horizon,
        "measure": "forward_negative_week_share",
        "path": VARIANCE_PATH,
        "groups": groups,
        "detail_by_phase": detail,
        "detail_expanded_by_default": DETAIL_PHASE,
        "why_two_groups": (
            "네 숫자를 나란히 놓으면 자료가 받쳐 주지 않는 순위가 보인다. 회복기와 "
            "확장기는 에피소드 4개와 17개에서 나온 값이라 서로 구분되지 않고, 후퇴기와 "
            "침체기는 사실상 같은 값이다. 그래서 기본은 두 묶음이고 국면별 숫자는 "
            "에피소드 수를 달아 상세로만 낸다."
        ),
        "path_note": (
            "최종 수정치 경로에서 잰 **역사적 분포**다. 실시간 경로와 섞어 읽으면 안 되고, "
            "앞날에 대한 확률이 아니다."
        ),
    }


def read_json(path: Path) -> dict[str, Any]:
    return dict(json.loads(path.read_text(encoding="utf-8")))


def read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path)
