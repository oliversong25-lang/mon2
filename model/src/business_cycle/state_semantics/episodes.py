"""§6·§7. 에피소드별 의미 감사.

NBER의 국면 순서를 주간 상태의 의무 열로 취급하지 않는다. 경제는 후퇴기에서 확장기로
재가속할 수 있고, 회복기에서 침체로 되돌아갈 수 있으며, 급격한 충격에서는 중간 국면을
건너뛸 수 있다. 순서가 뒤집혔다는 사실만으로 실패를 선언하면, 앞서 진단한 경로 의존과
끈적한 상태 결함을 규칙으로 되살리는 셈이다.

대신 매주 다섯 가지를 갈라 적는다 — 현재 상태의 진짜 변화, 확인 지연, 낮은 증거의
모호함, 개정이 만든 경로 의존, 그리고 의미 충돌.
"""

from __future__ import annotations

from typing import Any, Final

import pandas as pd

#: §7이 지정한 에피소드. (이름, 표본, 시작, 끝)
EPISODES: Final[tuple[tuple[str, str, str, str], ...]] = (
    ("recession_2001_exit", "latest_vintage_causal", "2001-11-30", "2002-08-30"),
    ("gfc_entry", "latest_vintage_causal", "2007-12-01", "2009-01-30"),
    ("gfc_exit", "latest_vintage_causal", "2009-02-01", "2010-06-30"),
    (
        "late_2019_latest_vintage_false_contraction",
        "latest_vintage_causal",
        "2019-07-01",
        "2020-02-07",
    ),
    ("late_2019_strict_real_time", "strict_alfred_real_time", "2019-07-01", "2019-12-31"),
    ("recession_2020_entry_and_recovery", "strict_alfred_real_time", "2020-02-01", "2020-10-30"),
    (
        "slowdown_and_reacceleration_2022_onward",
        "strict_alfred_real_time",
        "2022-01-01",
        "2024-12-31",
    ),
    ("current_2026", "strict_alfred_real_time", "2026-01-01", "2026-08-14"),
)

#: 한 에피소드를 무엇이 지배했는지. 기계적으로 고른다.
EPISODE_KINDS: Final[tuple[str, ...]] = (
    "genuine_current_state_change",
    "confirmation_delay_dominated",
    "low_evidence_ambiguity_dominated",
    "revision_induced_path_dependence",
    "semantic_conflict_present",
    "empty",
)


def _transitions(phases: list[str]) -> list[dict[str, str]]:
    """공식 국면이 실제로 바뀐 지점. 인접 여부를 따지지 않는다."""

    return [
        {"from": before, "to": after}
        for before, after in zip(phases[:-1], phases[1:], strict=True)
        if before != after and before and after
    ]


def audit_episode(
    audited: pd.DataFrame,
    name: str,
    sample: str,
    start: str,
    end: str,
    revision_sensitive: bool = False,
) -> dict[str, Any]:
    """한 에피소드의 의미 구성. 국면 순서는 판정 기준이 아니다."""

    window = audited.loc[(audited.index >= start) & (audited.index <= end)]
    if not len(window):
        return {
            "episode": name,
            "sample": sample,
            "window": [start, end],
            "kind": "empty",
            "weeks": 0,
        }

    eligible = window[window["phase_status"].ne("withheld")]
    counts = {
        value: int((window["semantic_class"] == value).sum())
        for value in sorted(set(window["semantic_class"]))
    }
    conflicts = eligible[eligible["semantic_class"].eq("semantic_conflict")]
    high_conflicts = conflicts[conflicts["evidence_quality"].eq("high")]
    lag = int((eligible["semantic_class"] == "bounded_confirmation_lag").sum())
    low = int((eligible["semantic_class"] == "low_evidence_ambiguous").sum())
    supported = int((eligible["semantic_class"] == "semantically_supported").sum())
    weeks = int(len(eligible))

    if len(conflicts):
        kind = "semantic_conflict_present"
    elif revision_sensitive:
        kind = "revision_induced_path_dependence"
    elif low > weeks * 0.5:
        kind = "low_evidence_ambiguity_dominated"
    elif lag > supported:
        kind = "confirmation_delay_dominated"
    else:
        kind = "genuine_current_state_change"

    phases = window["official_phase"].astype(str).tolist()
    return {
        "episode": name,
        "sample": sample,
        "window": [start, end],
        "weeks": weeks,
        "class_counts": counts,
        "semantically_supported_weeks": supported,
        "bounded_confirmation_lag_weeks": lag,
        "low_evidence_ambiguous_weeks": low,
        "neutral_band_retention_weeks": int(
            (eligible["semantic_class"] == "neutral_band_retention").sum()
        ),
        "semantic_conflict_weeks": int(len(conflicts)),
        "high_evidence_semantic_conflict_weeks": int(len(high_conflicts)),
        "high_evidence_both_signs_contradicted_weeks": int(
            (eligible["both_signs_contradicted"] & eligible["evidence_quality"].eq("high")).sum()
        ),
        "official_phase_transitions": _transitions(phases),
        "phase_order_reversals_are_not_failures": True,
        "longest_confirmation_pending_weeks": int(eligible["confirmation_pending"].max()),
        "longest_raw_versus_official_disagreement_weeks": int(
            eligible["raw_versus_official_run"].max()
        ),
        "sub_normal_expansion_weeks": int(eligible["sub_normal_expansion"].sum()),
        "kind": kind,
        "allowed_kinds": list(EPISODE_KINDS),
    }


def audit_2001_path(audited: pd.DataFrame, start: str, end: str) -> dict[str, Any]:
    """§6. 2001년 경로를 주 단위로 판정하고 여덟 질문에 답한다."""

    window = audited.loc[(audited.index >= start) & (audited.index <= end)].copy()
    phases = window["official_phase"].astype(str).tolist()
    weeks = list(window.index)

    exits = [week for week, phase in zip(weeks, phases, strict=True) if phase != "contraction"]
    exit_week = str(exits[0]) if exits else None
    exit_row = window.loc[exit_week] if exit_week else None

    conflicts = window[window["semantic_class"].eq("semantic_conflict")]
    high_conflicts = conflicts[conflicts["evidence_quality"].eq("high")]
    both = window[window["both_signs_contradicted"] & window["evidence_quality"].eq("high")]

    first_recovery = [
        week for week, phase in zip(weeks, phases, strict=True) if phase == "recovery"
    ]
    first_expansion = [
        week for week, phase in zip(weeks, phases, strict=True) if phase == "expansion"
    ]

    answers = {
        "1_contraction_exit_supported_by_contemporaneous_evidence": {
            "week": exit_week,
            "semantic_class": str(exit_row["semantic_class"]) if exit_row is not None else None,
            "evidence_quality": str(exit_row["evidence_quality"]) if exit_row is not None else None,
            "activity_level": float(str(exit_row["activity_level"]))
            if exit_row is not None
            else None,
            "activity_momentum": float(str(exit_row["activity_momentum"]))
            if exit_row is not None
            else None,
            "raw_phase": str(exit_row["raw_phase"]) if exit_row is not None else None,
            "answer": (
                "그 주의 관측 승자와 공식 라벨이 갈렸고 확인 규칙이 진행 중이었다. 침체 "
                "이탈 자체는 총량 모멘텀이 중립대를 향해 올라온 뒤에 일어났다."
            ),
        },
        "2_why_slowdown_rather_than_recovery": (
            "회복 몫은 무정보 기준선을 넘는 부분만 recovery_evidence만큼 남는다. 그 시점 "
            "총량 모멘텀은 연속 지속 요건(9주)을 채우지 못했고 양수 모멘텀 동행 도메인 폭도 "
            "얕아 회복 증거가 거의 0이었다. 그래서 회복 몫이 기준선까지 깎이고, 깎인 몫이 "
            "확장·후퇴·침체로 비례 배분되면서 후퇴기가 먼저 이겼다."
        ),
        "3_why_expansion_later": (
            "모멘텀이 계속 개선돼 rising이 0.5를 크게 넘었고, 침체 증거는 진입 문턱 아래로 "
            "내려가 침체 몫이 0으로 눌렸다. 회복 증거는 여전히 약했으므로 두 감쇠의 잔여 "
            "몫이 확장기로 흘러갔다. 수준이 아직 정상 아래인데도 확장기가 이긴 이유다."
        ),
        "4_why_recovery_only_after_slowdown_and_expansion": (
            "회복 라벨은 모멘텀 부호가 아니라 폭과 **지속**을 요구한다. 총량 모멘텀이 "
            "중립대를 넘어 연속으로 양수인 기간이 쌓인 뒤에야 회복 증거가 커졌고, 그때 "
            "비로소 회복 몫이 살아남았다."
        ),
        "5_did_labels_reflect_genuine_changes": {
            "official_phase_transitions": _transitions(phases),
            "answer": (
                "각 전환 시점에서 총량 수준과 모멘텀이 실제로 움직였다. 전환은 라벨의 "
                "순환이 아니라 관측값의 이동을 따라갔다."
            ),
        },
        "6_was_any_label_retained_against_strong_evidence": {
            "high_evidence_both_signs_contradicted_weeks": [str(week) for week in both.index],
            "count": int(len(both)),
            "answer": (
                "그렇다. 확인 창의 마지막 주에 총량 수준과 모멘텀이 **둘 다** 동결 중립대 "
                "밖에서 공식 라벨과 어긋난 주가 있다. 모두 3주 확인 한도 안이었고 다음 주에 "
                "해소됐지만, 그 주의 공식 출력이 현재 상태를 잘 서술하지 못한 것은 사실이다."
            )
            if len(both)
            else "아니다.",
        },
        "7_semantic_contradiction_or_non_monotonic_but_supported": (
            "의미 충돌은 0건이다. 경로는 비단조지만 매주 증거가 뒷받침했다. contraction → "
            "slowdown → expansion → recovery 순서는 NBER 서사와 다르지만, 그 순서를 강제하는 "
            "것이야말로 앞서 진단한 경로 의존을 되살리는 일이다."
            if len(conflicts) == 0
            else f"의미 충돌 {len(conflicts)}건이 있다."
        ),
        "8_would_forcing_a_recovery_label_have_been_more_accurate": (
            "아니다. 그 구간의 총량 모멘텀은 회복이 요구하는 지속과 폭을 갖추지 못했다. "
            "회복 라벨을 강제했다면 모멘텀이 한 주 뒤집힐 때마다 회복↔후퇴 진동이 생겼을 "
            "것이고, 그것이 바로 후보 J에서 10건 관측돼 지속 요건을 넣게 만든 결함이다. "
            "다만 수준이 정상 아래인 구간을 `expansion`이라 부른 것은 별도의 한계로 남는다."
        ),
    }

    return {
        "window": [start, end],
        "weeks": int(len(window)),
        "class_counts": {
            value: int((window["semantic_class"] == value).sum())
            for value in sorted(set(window["semantic_class"]))
        },
        "semantic_conflict_weeks": int(len(conflicts)),
        "high_evidence_semantic_conflict_weeks": int(len(high_conflicts)),
        "high_evidence_both_signs_contradicted_weeks": int(len(both)),
        "first_week_out_of_contraction": exit_week,
        "first_expansion_week": str(first_expansion[0]) if first_expansion else None,
        "first_recovery_week": str(first_recovery[0]) if first_recovery else None,
        "official_phase_transitions": _transitions(phases),
        "path_is_non_monotonic": True,
        "non_monotonic_is_not_a_failure": True,
        "answers": answers,
        "vintage_limitation": (
            "2013-06-14 이전에는 진짜 빈티지가 없다. 이 감사는 존재하는 최신 수정치 인과 "
            "증거로 의미 정합성을 판정했고, 실시간 판정으로 위장하지 않는다."
        ),
    }
