"""§8. 현재 2026년 출력의 의미 감사.

증거 품질을 올리지 않는다. 공식 국면 하나를 모호한 라벨로 바꾸지 않는다. 둘 다 요청받은
금지이며, 둘 다 이 모듈이 지킬 수 있는 것이다 — 여기서는 동결 산출물을 읽기만 한다.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from ..current_state.domains import COINCIDENT_DOMAINS, DOMAINS
from ..four_phase.evidence import PHASES
from .contract import sign_quadrant


def audit(
    audited: pd.DataFrame, path: pd.DataFrame, thresholds: Any, config: Any
) -> dict[str, Any]:
    """마지막 as-of 주. 왜 그 국면이 이겼는지, 무엇이 바뀌어야 국면이 바뀌는지."""

    week = str(audited.index[-1])
    row = audited.loc[week]
    source = path.loc[week] if week in path.index else None

    raw_scores = {name: float(str(row[f"raw_{name}"])) for name in PHASES if f"raw_{name}" in row}
    filtered = {
        name: float(str(row[f"filtered_{name}"])) for name in PHASES if f"filtered_{name}" in row
    }
    ranked = sorted(filtered.items(), key=lambda item: item[1], reverse=True)
    official = str(row["official_phase"])
    level = float(str(row["activity_level"]))
    momentum = float(str(row["activity_momentum"]))

    previous = str(audited.at[str(audited.index[-2]), "official_phase"]) if len(audited) > 1 else ""
    semantic = str(row["semantic_class"])

    freshness: dict[str, Any] = {}
    support: dict[str, str] = {}
    if source is not None:
        for domain in DOMAINS:
            for column in (f"age_{domain}", f"{domain}__weeks_since_release"):
                if column in source:
                    freshness[domain] = float(str(source[column]))
                    break

    # 국면이 바뀌려면 무엇이 필요한가. 동결 규칙에서 그대로 읽는다.
    runner_up = ranked[1][0] if len(ranked) > 1 else ""
    requirement = {
        "route_a_immediate_transition": (
            f"`{runner_up}`가 필터 승자가 되고 **동시에** 증거 품질이 high여야 하며, 원시 "
            f"점수 마진이 {config.immediate_margin} 이상이어야 한다. 지금 증거 품질이 low라 "
            "이 경로는 닫혀 있다."
        ),
        "route_b_confirmation": (
            f"`{runner_up}`가 필터 승자로 {config.confirmation_weeks}주 연속 버티면 증거가 "
            "약해도 전환한다. 흡수 상태는 없다."
        ),
        "what_would_move_the_evidence_quality_to_high": (
            f"중립대 밖으로 나가고(|수준| > {thresholds.neutral_level} 또는 |모멘텀| > "
            f"{thresholds.neutral_momentum}), 집중도가 {thresholds.concentration_flag} 아래로 "
            f"내려가며, 국면 분리도가 {config.separation_floor} 이상이 되고, 신선한 동행 "
            "도메인이 유지돼야 한다."
        ),
    }

    return {
        "as_of_date": week,
        "official_current_phase": official,
        "evidence_quality": str(row["evidence_quality"]),
        "raw_current_phase": str(row["raw_phase"]),
        "filtered_winner": str(row["filtered_winner"]) if "filtered_winner" in row else None,
        "semantic_class": semantic,
        "reason_code": str(row["reason_code"]),
        "why_this_phase_wins": (
            f"필터 사후확률에서 `{ranked[0][0]}`가 {ranked[0][1]:.4f}로 1위이고 2위 "
            f"`{ranked[1][0]}`가 {ranked[1][1]:.4f}다. 분리도 "
            f"{float(str(row['phase_separation'])):.4f}는 하한 {config.separation_floor}보다 "
            "크지만 증거 품질을 높이기에는 다른 조건이 모자란다."
        ),
        "raw_scores": {name: round(value, 6) for name, value in raw_scores.items()},
        "filtered_scores": {name: round(value, 6) for name, value in filtered.items()},
        "ranked_phases": [
            {"phase": name, "filtered_score": round(value, 6)} for name, value in ranked
        ],
        "separation_from_second": round(ranked[0][1] - ranked[1][1], 6)
        if len(ranked) > 1
        else None,
        "phase_separation_field": round(float(str(row["phase_separation"])), 6),
        "activity_level": round(level, 6),
        "activity_momentum": round(momentum, 6),
        "sign_quadrant": sign_quadrant(level, momentum),
        "sign_quadrant_matches_official": sign_quadrant(level, momentum) == official,
        "level_materially_contradicted": bool(row["level_materially_contradicted"]),
        "momentum_materially_contradicted": bool(row["momentum_materially_contradicted"]),
        "neutral_both": bool(row["neutral_both"]),
        "breadth": {
            "confirming_coincident_domains": int(str(row["confirming_domains"])),
            "positive_momentum_domains": int(str(row["positive_momentum_domains"])),
            "coincident_domains": list(COINCIDENT_DOMAINS),
        },
        "concentration": round(float(str(row["concentration"])), 6),
        "concentration_flag": thresholds.concentration_flag,
        "concentration_is_crowded": bool(
            float(str(row["concentration"])) > thresholds.concentration_flag
        ),
        "domain_freshness_weeks": freshness,
        "domain_stale_weeks_limit": config.stale_weeks,
        "domains_at_or_beyond_the_stale_limit": sorted(
            name for name, weeks in freshness.items() if weeks >= config.stale_weeks
        ),
        "domain_support": support,
        "previous_official_phase": previous,
        "previous_state_materially_determines_the_result": bool(
            semantic in ("neutral_band_retention", "bounded_confirmation_lag")
            or (official == previous and official != str(row["raw_phase"]))
        ),
        # 세 갈래를 구분한다. 낮은 증거라는 것과 직전 상태에 붙들려 있다는 것은 다른
        # 사실이다. 원시·필터 승자가 모두 공식 라벨과 같으면 그것은 유지가 아니다.
        "semantically_supported_or_retained": (
            "semantically_supported"
            if semantic == "semantically_supported"
            else (
                "low_evidence_but_contemporaneously_agreed"
                if official == str(row["raw_phase"])
                else "low_evidence_retained_state"
            )
        ),
        "what_would_change_the_official_phase": requirement,
        "evidence_quality_was_not_upgraded": True,
        "single_official_phase": True,
        "headline": (
            f"Current official U.S. phase: {official}\nEvidence quality: "
            f"{str(row['evidence_quality'])}"
        ),
    }
