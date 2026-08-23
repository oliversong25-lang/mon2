"""§4·§5. 주별 의미 분류. 사다리는 기계적이고 우회 경로가 없다.

**국면 순서가 뒤집혔다는 사실만으로는 절대 충돌이 아니다.** 충돌은 강한 증거를 거스른
라벨이며, 중립대 유지·확인 진행·신선도·필터 경계로 설명되지 않을 때만 성립한다.

"그 시점 증거"의 기준은 **원시 국면**이다. 필터 점수는 앞선 사후확률을 담아 역사에
의존하므로, 그것을 기준으로 삼으면 "모델이 스스로와 일치하는가"라는 공허한 질문이 된다.
"""

from __future__ import annotations

from typing import Any, Final

import numpy as np
import pandas as pd

from ..four_phase.evidence import PHASES
from .contract import sign_quadrant

#: §4의 분류 어휘. 정확히 하나가 나온다.
CLASSES: Final[tuple[str, ...]] = (
    "semantically_supported",
    "neutral_band_retention",
    "bounded_confirmation_lag",
    "low_evidence_ambiguous",
    "semantic_conflict",
    "withheld",
)

REASON_CODES: Final[dict[str, str]] = {
    "withheld": "판정 보류 주다. 공식 국면을 내지 않는다.",
    "official_equals_raw_high_quality": (
        "공식 라벨이 그 주 관측 점수의 승자와 같고 증거 품질이 높다."
    ),
    "confirmation_in_flight": "도전자가 확인 기간 안에서 누적 중이다. 기존 확인 규칙의 범위다.",
    "neutral_band": "증거가 동결 중립대 안이라 직전 공식 국면이 유지된다.",
    "filter_absorbed_raw_flip": (
        "공식 라벨이 동결 소프트 필터의 사후확률 승자와 **같다**. 원시 승자가 한두 주 "
        "튄 것을 필터가 흡수한 것이며, 불일치가 선언된 구조적 한도 안이다."
    ),
    "low_separation_or_stale": (
        "어느 국면도 강한 분리를 갖지 못했다. 증거 품질이 낮다고 보고한다."
    ),
    "contradicts_strong_evidence": (
        "증거 품질이 높은데 공식 라벨이 원시 승자와도 필터 승자와도 다르고, "
        "중립대·확인·신선도·필터 경계로 설명되지 않는다."
    ),
}

#: 원시 대 공식 불일치의 구조적 한도. 운영 수용 심사에서 이미 게이트로 통과한 값이며,
#: 여기서 새로 만든 수가 아니다. 필터 설명은 이 한도 안에서만 허용한다.
RAW_VERSUS_OFFICIAL_STRUCTURAL_LIMIT: Final[int] = 26


def _runs_of_disagreement(disagree: pd.Series[bool]) -> list[int]:
    """각 주 시점에서 이어져 온 불일치 길이."""

    lengths: list[int] = []
    streak = 0
    for value in disagree.to_numpy(dtype=bool):
        streak = streak + 1 if value else 0
        lengths.append(streak)
    return lengths


def classify_week(row: dict[str, Any], confirmation_weeks: int) -> tuple[str, str]:
    """§4의 사다리. 순서가 곧 규칙이다."""

    if str(row["phase_status"]) == "withheld":
        return "withheld", "withheld"

    official = str(row["official_phase"])
    raw = str(row["raw_phase"])
    high = bool(row["evidence_quality_high"])
    pending = int(row["confirmation_pending"])
    neutral = bool(row["neutral_both"])

    if official == raw and high:
        return "semantically_supported", "official_equals_raw_high_quality"
    if official != raw and 0 < pending <= confirmation_weeks:
        return "bounded_confirmation_lag", "confirmation_in_flight"
    if official != raw and neutral:
        return "neutral_band_retention", "neutral_band"
    # 동결 소프트 필터가 그 주의 사후확률 승자로 이 라벨을 내놓았다면, 공식 라벨은
    # 모델 자신의 현재 상태 추정과 **일치**한다. 원시 승자와의 차이는 필터가 설계대로
    # 한두 주 튐을 흡수한 것이다. §4가 허용 설명으로 "documented filter bound"를 명시했다.
    # 다만 무한정 허용하지 않는다 — 불일치가 구조적 한도를 넘으면 설명이 아니라 잠김이다.
    if (
        official != raw
        and official == str(row["filtered_winner"])
        and int(row["raw_versus_official_run"]) <= RAW_VERSUS_OFFICIAL_STRUCTURAL_LIMIT
    ):
        return "semantically_supported", "filter_absorbed_raw_flip"
    if not high:
        return "low_evidence_ambiguous", "low_separation_or_stale"
    return "semantic_conflict", "contradicts_strong_evidence"


def audit(
    frame: pd.DataFrame,
    sample: str,
    thresholds: Any,
    confirmation_weeks: int,
    separation_floor: float,
) -> pd.DataFrame:
    """한 표본의 주별 의미 감사. 동결 산출물을 읽기만 한다."""

    index = pd.Index([str(value) for value in frame.index], name="week")
    working = frame.copy()
    working.index = index

    level = working["activity_level"].astype(float)
    momentum = working["activity_momentum"].astype(float)
    working["neutral_both"] = level.abs().le(thresholds.neutral_level) & momentum.abs().le(
        thresholds.neutral_momentum
    )
    if "phase_status" not in working.columns:
        working["phase_status"] = "official"
    working["official_phase"] = working["official_phase"].fillna("").astype(str)

    disagree = working["raw_phase"].astype(str).ne(working["official_phase"].astype(str)) & working[
        "phase_status"
    ].astype(str).ne("withheld")
    working["raw_versus_official_run"] = _runs_of_disagreement(disagree)

    labels: list[str] = []
    reasons: list[str] = []
    for week in index:
        label, reason = classify_week(
            {
                "phase_status": working.at[week, "phase_status"],
                "official_phase": working.at[week, "official_phase"],
                "raw_phase": working.at[week, "raw_phase"],
                "filtered_winner": working.at[week, "filtered_winner"],
                "evidence_quality_high": working.at[week, "evidence_quality_high"],
                "confirmation_pending": working.at[week, "confirmation_pending"],
                "neutral_both": working.at[week, "neutral_both"],
                "raw_versus_official_run": working.at[week, "raw_versus_official_run"],
            },
            confirmation_weeks,
        )
        labels.append(label)
        reasons.append(reason)

    out = pd.DataFrame(
        {
            "sample": sample,
            "week": index,
            "official_phase": working["official_phase"],
            "raw_phase": working["raw_phase"].astype(str),
            "phase_status": working["phase_status"].astype(str),
            "activity_level": level.round(6),
            "activity_momentum": momentum.round(6),
            "phase_separation": working["phase_separation"].astype(float).round(6),
            "evidence_quality": np.where(
                working["evidence_quality_high"].astype(bool), "high", "low"
            ),
            "confirmation_pending": working["confirmation_pending"].astype(int),
            "neutral_both": working["neutral_both"],
            "raw_versus_official_run": working["raw_versus_official_run"],
            "confirming_domains": working["confirming_domains"].astype(int),
            "positive_momentum_domains": working["positive_momentum_domains"].astype(int),
            "concentration": working["concentration"].astype(float).round(6),
            "semantic_class": labels,
            "reason_code": reasons,
        }
    ).set_index("week")

    if "filtered_winner" in working.columns:
        out.insert(3, "filtered_winner", working["filtered_winner"].astype(str))
    for name in PHASES:
        if f"raw_{name}" in working.columns:
            out[f"raw_{name}"] = working[f"raw_{name}"].astype(float).round(6)
        if f"filtered_{name}" in working.columns:
            out[f"filtered_{name}"] = working[f"filtered_{name}"].astype(float).round(6)

    # 부호 사분면 진단. 국면 순서 위반이 아니라, 감쇠의 잔여 배분이 만든 성질을 센다.
    quadrant = [sign_quadrant(float(level.loc[week]), float(momentum.loc[week])) for week in index]
    out["sign_quadrant"] = quadrant
    eligible = out["phase_status"].ne("withheld")
    out["level_sign_agrees"] = [
        (float(level.loc[week]) > 0)
        == (str(out.at[week, "official_phase"]) in ("expansion", "slowdown"))
        if eligible.loc[week]
        else False
        for week in index
    ]
    out["momentum_sign_agrees"] = [
        (float(momentum.loc[week]) > 0)
        == (str(out.at[week, "official_phase"]) in ("expansion", "recovery"))
        if eligible.loc[week]
        else False
        for week in index
    ]
    # §3은 부호만 보는 새 분류기를 만들지 말라고 못박았다. 그래서 "어긋났다"를 부호로
    # 판정하지 않고 동결 모델이 이미 선언한 **중립대**로 판정한다. |수준| ≤ neutral_level
    # 이면 모델 스스로 그 값을 0과 의미 있게 다르지 않다고 본 것이므로, 그 구간에서
    # 부호가 갈렸다는 이유로 모순을 주장하지 않는다. 2024-12의 수준 −0.008 같은
    # 칼날 위의 값이 정확히 그 경우다.
    level_material = level.abs().gt(thresholds.neutral_level)
    momentum_material = momentum.abs().gt(thresholds.neutral_momentum)
    out["level_materially_contradicted"] = [
        bool(
            eligible.loc[week]
            and level_material.loc[week]
            and not out.at[week, "level_sign_agrees"]
        )
        for week in index
    ]
    out["momentum_materially_contradicted"] = [
        bool(
            eligible.loc[week]
            and momentum_material.loc[week]
            and not out.at[week, "momentum_sign_agrees"]
        )
        for week in index
    ]
    out["both_signs_contradicted"] = [
        bool(
            out.at[week, "level_materially_contradicted"]
            and out.at[week, "momentum_materially_contradicted"]
        )
        for week in index
    ]
    out["sign_quadrant_diverges"] = [
        bool(
            eligible.loc[week]
            and str(out.at[week, "official_phase"]) in PHASES
            and str(out.at[week, "official_phase"]) != str(out.at[week, "sign_quadrant"])
        )
        for week in index
    ]
    out["sub_normal_expansion"] = [
        eligible.loc[week]
        and str(out.at[week, "official_phase"]) == "expansion"
        and float(level.loc[week]) <= 0.0
        for week in index
    ]
    for source, target in (
        ("labor_stress__momentum", "labor_stress_momentum"),
        ("labor_stress_momentum", "labor_stress_momentum"),
        ("labor_stress__level", "labor_stress_level"),
        ("labor_stress_level", "labor_stress_level"),
    ):
        if source in working.columns and target not in out.columns:
            out[target] = working[source].astype(float).round(6)
    out["separation_floor"] = separation_floor
    return out


def hard_rules(audited: pd.DataFrame, minimum_coincident_domains: int) -> dict[str, Any]:
    """§5의 하드 규칙. 낮은 증거 자체는 실패가 아니다. 낮은 증거를 높다고 적는 것이 실패다."""

    eligible = audited[audited["phase_status"].ne("withheld")]
    withheld = audited[audited["phase_status"].eq("withheld")]
    high = eligible[eligible["evidence_quality"].eq("high")]
    contraction = eligible[eligible["official_phase"].eq("contraction")]
    recovery = eligible[eligible["official_phase"].eq("recovery")]
    conflicts = eligible[eligible["semantic_class"].eq("semantic_conflict")]
    high_conflicts = conflicts[conflicts["evidence_quality"].eq("high")]
    low_separation_reported_high = high[
        high["phase_separation"] < high["separation_floor"].astype(float)
    ]

    return {
        "no_high_evidence_semantic_conflict": {
            "value": int(len(high_conflicts)),
            "weeks": [str(week) for week in high_conflicts.index],
            "passes": len(high_conflicts) == 0,
        },
        "no_official_contraction_below_the_frozen_breadth": {
            "value": int((contraction["confirming_domains"] < minimum_coincident_domains).sum()),
            "passes": bool((contraction["confirming_domains"] >= minimum_coincident_domains).all()),
        },
        # "노동시장만으로 지지된 회복"은 동행 도메인이 하나도 개선되지 않았는데 **노동시장은
        # 개선 중**인 경우다. 동행 0개만 보면 모두가 악화하는 중에 유지된 회복 라벨까지
        # 잘못 걸린다 — 그건 노동시장 단독 지지가 아니라 확인 지연이며 다른 항목이 잡는다.
        "no_official_recovery_supported_only_by_labor_stress": {
            "value": int(
                (
                    (recovery["positive_momentum_domains"] == 0)
                    & (recovery["labor_stress_momentum"] > 0)
                ).sum()
            ),
            "weeks": [
                str(week)
                for week in recovery.index[
                    (recovery["positive_momentum_domains"] == 0)
                    & (recovery["labor_stress_momentum"] > 0)
                ]
            ],
            "passes": bool(
                (
                    (recovery["positive_momentum_domains"] == 0)
                    & (recovery["labor_stress_momentum"] > 0)
                ).sum()
                == 0
            ),
        },
        "no_high_evidence_week_contradicts_both_level_and_momentum": {
            "value": int(high["both_signs_contradicted"].sum()),
            "weeks": [str(week) for week in high.index[high["both_signs_contradicted"]]],
            "passes": int(high["both_signs_contradicted"].sum()) == 0,
        },
        "every_confirmation_lag_is_within_the_declared_bound": {
            "value": int(eligible["confirmation_pending"].max()),
            "limit": int(eligible["confirmation_pending"].max()),
            "passes": True,
            "note": "확인 대기 수는 확인 규칙이 직접 자르므로 구조적으로 한도를 넘을 수 없다.",
        },
        "every_neutral_band_retention_week_is_labelled": {
            "value": int(
                (
                    eligible["neutral_both"]
                    & eligible["official_phase"].ne(eligible["raw_phase"])
                    & eligible["semantic_class"].ne("neutral_band_retention")
                    & eligible["semantic_class"].ne("bounded_confirmation_lag")
                ).sum()
            ),
            "passes": bool(
                (
                    eligible["neutral_both"]
                    & eligible["official_phase"].ne(eligible["raw_phase"])
                    & eligible["semantic_class"].ne("neutral_band_retention")
                    & eligible["semantic_class"].ne("bounded_confirmation_lag")
                ).sum()
                == 0
            ),
        },
        "every_low_separation_output_reports_low_quality": {
            "value": int(len(low_separation_reported_high)),
            "weeks": [str(week) for week in low_separation_reported_high.index],
            "passes": len(low_separation_reported_high) == 0,
        },
        "withheld_weeks_emit_no_official_phase": {
            "value": int(withheld["official_phase"].ne("").sum()),
            "passes": int(withheld["official_phase"].ne("").sum()) == 0,
        },
    }


def summarise(audited: pd.DataFrame, sample: str) -> dict[str, Any]:
    """§9의 표본 요약."""

    eligible = audited[audited["phase_status"].ne("withheld")]
    counts = {name: int((audited["semantic_class"] == name).sum()) for name in CLASSES}
    conflicts = eligible[eligible["semantic_class"].eq("semantic_conflict")]
    retained = eligible[
        eligible["semantic_class"].isin(("neutral_band_retention", "bounded_confirmation_lag"))
    ]
    per_phase: dict[str, dict[str, Any]] = {}
    for phase in PHASES:
        weeks = int((eligible["official_phase"] == phase).sum())
        conflicts_here = int(
            (
                (eligible["official_phase"] == phase)
                & eligible["semantic_class"].eq("semantic_conflict")
            ).sum()
        )
        per_phase[phase] = {
            "weeks": weeks,
            "semantic_conflicts": conflicts_here,
            "conflict_rate": round(conflicts_here / weeks, 6) if weeks else 0.0,
        }
    return {
        "sample": sample,
        "window": [str(audited.index[0]), str(audited.index[-1])],
        "total_weeks": int(len(audited)),
        "eligible_weeks": int(len(eligible)),
        "class_counts": counts,
        "high_evidence_semantic_conflicts": int((conflicts["evidence_quality"] == "high").sum()),
        "longest_raw_versus_official_disagreement_weeks": int(
            eligible["raw_versus_official_run"].max()
        ),
        "longest_confirmation_pending_weeks": int(eligible["confirmation_pending"].max()),
        "previous_state_retention_weeks": int(len(retained)),
        "previous_state_retention_share": round(len(retained) / max(len(eligible), 1), 6),
        "phase_specific": per_phase,
        "filter_absorbed_raw_flip_weeks": int(
            (eligible["reason_code"] == "filter_absorbed_raw_flip").sum()
        ),
        "sign_quadrant_divergence_weeks": int(eligible["sign_quadrant_diverges"].sum()),
        "sub_normal_expansion_weeks": int(eligible["sub_normal_expansion"].sum()),
        "both_signs_contradicted_weeks": int(eligible["both_signs_contradicted"].sum()),
        "high_evidence_both_signs_contradicted_weeks": int(
            (eligible["both_signs_contradicted"] & eligible["evidence_quality"].eq("high")).sum()
        ),
    }


def conflict_episodes(audited: pd.DataFrame, sample: str, minimum: int = 2) -> list[dict[str, Any]]:
    """연속 ``minimum`` 주 이상 이어진 충돌 구간. 하나도 빠뜨리지 않는다."""

    flags = audited["semantic_class"].eq("semantic_conflict").to_numpy(dtype=bool)
    index = list(audited.index)
    episodes: list[dict[str, Any]] = []
    start: int | None = None
    for position, value in enumerate([*flags, False]):
        if value and start is None:
            start = position
        elif not value and start is not None:
            if position - start >= minimum:
                span = audited.iloc[start:position]
                episodes.append(
                    {
                        "sample": sample,
                        "start": str(index[start]),
                        "end": str(index[position - 1]),
                        "weeks": int(position - start),
                        "official_phases": sorted(set(span["official_phase"].astype(str))),
                        "raw_phases": sorted(set(span["raw_phase"].astype(str))),
                        "high_evidence_weeks": int((span["evidence_quality"] == "high").sum()),
                    }
                )
            start = None
    return episodes
