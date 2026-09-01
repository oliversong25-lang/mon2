"""§4·§6·§8. 2009년 재분류, 사전 통과 게이트 재확인, 그리고 amber 조건.

사전 통과 게이트는 **다시 정의하지 않는다**. 앞 단계의 게이트 함수를 그대로 부른다.
같은 이름의 새 구현을 두면 "재확인"이 아니라 다른 시험이 된다.
"""

from __future__ import annotations

from typing import Any, Final

import pandas as pd

from ..current_state.domains import DOMAINS
from ..operational_review.review import contraction_entry_gates, integrity_gates
from .consistency import GENUINE_PRE_TROUGH_POSITION, sequence_position
from .turning import TurningMonth, peak_month_start

#: §8이 재확인을 요구한 게이트. 앞 단계에서 **통과**로 기록된 것만 들어간다.
PREVIOUSLY_PASSED: Final[tuple[str, ...]] = (
    "no_confirmed_2019_contraction_before_the_recession",
    "first_official_contraction_within_10_weeks",
    "first_persistent_four_week_sequence_within_10_weeks",
    "at_least_four_of_eight_recession_weeks_as_contraction",
    "no_official_contraction_below_two_confirming_domains",
    "no_concentrated_signal_decided_the_official_phase",
    "zero_future_information_violations",
    "cache_only_no_network_no_key",
    "exactly_688_as_of_weeks",
    "withheld_and_preliminary_weeks_reproduced",
    "no_official_phase_on_a_withheld_week",
    "raw_measurements_preserved_on_withheld_weeks",
    "raw_versus_official_disagreement_within_structural_limit",
)


def recheck(
    path: pd.DataFrame,
    recession: pd.Series,
    audit: dict[str, Any],
    cache: dict[str, Any],
) -> dict[str, Any]:
    """모델을 건드리지 않고 사전 통과 게이트를 다시 잰다. 하나라도 퇴행하면 기각이다."""

    groups = {
        "contraction_entry": contraction_entry_gates(path, recession),
        "operational_integrity": integrity_gates(path, audit, cache),
    }
    flat = {name: detail for entries in groups.values() for name, detail in entries.items()}
    missing = [name for name in PREVIOUSLY_PASSED if name not in flat]
    regressed = [name for name in PREVIOUSLY_PASSED if name in flat and not flat[name]["passes"]]
    return {
        "groups": groups,
        "checked": list(PREVIOUSLY_PASSED),
        "missing_gates": missing,
        "regressed_gates": regressed,
        "all_previously_passed_gates_still_pass": not regressed and not missing,
    }


def _first_run(mask: pd.Series[bool], index: pd.Index, length: int) -> str | None:
    values = mask.to_numpy(dtype=bool)
    run = 0
    for position in range(len(values)):
        run = run + 1 if values[position] else 0
        if run >= length:
            return str(index[position - length + 1])
    return None


def pre_trough_recovery_scan(
    frame: pd.DataFrame, episode: str, trough: TurningMonth, length: int = 4
) -> dict[str, Any]:
    """§3 규약으로 다시 본 조기 회복. **저점 월 첫날보다 앞선** 것만이 조기다.

    공식 국면과 원시 국면을 따로 적는다. 게이트는 앞 단계와 같이 **공식 국면**에 건다 —
    운영이 내보내는 것이 공식 국면이기 때문이다. 원시 층에서 나온 앞선 회복은 게이트를
    바꾸지 않지만 그대로 공시한다.
    """

    peak = peak_month_start(episode)
    index = pd.Index([str(value) for value in frame.index])
    moments = pd.to_datetime(index)
    inside = pd.Series((moments >= peak) & (moments <= trough.end), index=index)
    from_peak = pd.Series(moments >= peak, index=index)
    result: dict[str, Any] = {"episode": episode, **trough.as_dict()}
    for layer in ("official_phase", "raw_phase", "filtered_winner"):
        if layer not in frame.columns:
            continue
        recovery = pd.Series(frame[layer].astype(str).eq("recovery").to_numpy(bool), index=index)
        inside_run = _first_run(recovery & inside, index, length)
        result[f"{layer}__first_recovery_inside_the_recession"] = (
            str(index[(recovery & inside).to_numpy(bool)][0]) if (recovery & inside).any() else None
        )
        result[f"{layer}__first_four_week_recovery_inside_the_recession"] = inside_run
        result[f"{layer}__position"] = trough.position(inside_run)
        # 열의 위치는 시작만으로 정하지 않는다. 5월 22일에 시작해 6월 12일에 끝나는 열은
        # 저점 월과 절반이 겹치므로 "저점 이전"이 아니다. §3의 네 어휘로 정확히 적고,
        # 채택 게이트가 보는 "진짜 저점 이전"은 열 전체가 그 달 앞에 있는 경우뿐이다.
        started = _first_run(recovery & from_peak, index, length)
        finished = None
        if started is not None:
            position = list(index).index(started) + length - 1
            finished = str(index[position])
        where = sequence_position(started, finished, trough)
        result[f"{layer}__first_four_week_recovery_from_the_peak"] = started
        result[f"{layer}__first_four_week_recovery_end"] = finished
        result[f"{layer}__four_week_sequence_position"] = where
        result[f"{layer}__genuine_pre_trough_four_week_recovery"] = (
            started if where == GENUINE_PRE_TROUGH_POSITION else None
        )
    return result


def episode_2009_detail(frame: pd.DataFrame, trough: TurningMonth) -> dict[str, Any]:
    """§4가 요구한 항목. 2009-06-05 회복 열의 전체 기록."""

    index = pd.Index([str(value) for value in frame.index])
    working = frame.copy()
    working.index = index
    recovery_official = index[working["official_phase"].astype(str).eq("recovery").to_numpy(bool)]
    recovery_raw = index[working["raw_phase"].astype(str).eq("recovery").to_numpy(bool)]
    first_official = str(recovery_official[0]) if len(recovery_official) else None
    first_raw = str(recovery_raw[0]) if len(recovery_raw) else None
    confirmed = _first_run(
        pd.Series(working["official_phase"].astype(str).eq("recovery").to_numpy(bool), index=index),
        index,
        4,
    )

    detail: dict[str, Any] = {
        "episode": trough.episode,
        **trough.as_dict(),
        "first_raw_recovery": first_raw,
        "first_official_recovery": first_official,
        "first_four_week_confirmed_recovery": confirmed,
        "position_of_first_raw_recovery": trough.position(first_raw),
        "position_of_first_official_recovery": trough.position(first_official),
        "position_of_first_confirmed_recovery": trough.position(confirmed),
        "original_operational_review_finding": (
            "저점 월을 침체에 포함하는 규약 아래 2009-06-05 시작 4주 회복을 "
            "`no_premature_four_week_recovery_inside_a_recession` 실패로 기록했다."
        ),
        "original_finding_preserved": True,
    }

    if first_official is not None:
        row = working.loc[first_official]
        detail["at_first_official_recovery"] = {
            "raw_phase": str(row["raw_phase"]),
            "filtered_winner": str(row["filtered_winner"]),
            "official_phase": str(row["official_phase"]),
            "activity_level": float(str(row["activity_level"])),
            "activity_momentum": float(str(row["activity_momentum"])),
            "raw_scores": {
                name: float(str(row[f"raw_{name}"]))
                for name in ("recovery", "expansion", "slowdown", "contraction")
                if f"raw_{name}" in working.columns
            },
            "filtered_scores": {
                name: float(str(row[f"filtered_{name}"]))
                for name in ("recovery", "expansion", "slowdown", "contraction")
                if f"filtered_{name}" in working.columns
            },
            "phase_separation": float(str(row["phase_separation"])),
            "evidence_quality_high": bool(row["evidence_quality_high"]),
            "confirming_domains": int(str(row["confirming_domains"])),
            "negative_level_domains": int(str(row["negative_level_domains"])),
            "positive_momentum_domains": int(str(row["positive_momentum_domains"])),
            "concentration": float(str(row["concentration"])),
            "domains": {
                domain: {
                    "level": float(str(row[f"{domain}__level"])),
                    "momentum": float(str(row[f"{domain}__momentum"])),
                    "level_contribution": float(str(row[f"{domain}__level_contribution"])),
                    "momentum_contribution": float(str(row[f"{domain}__momentum_contribution"])),
                    "weeks_since_release": float(str(row[f"{domain}__weeks_since_release"])),
                }
                for domain in DOMAINS
                if f"{domain}__level" in working.columns
            },
        }

    if first_official is not None:
        begin = pd.Timestamp(first_official)
        moments = pd.to_datetime(index)
        contraction = working["official_phase"].astype(str).eq("contraction").to_numpy(bool)
        for weeks in (4, 8, 13):
            ahead = (moments > begin) & (moments <= begin + pd.Timedelta(weeks=weeks))
            detail[f"return_to_contraction_within_{weeks}_weeks"] = int((contraction & ahead).sum())
        detail["exhibited_whipsaw"] = bool(detail["return_to_contraction_within_13_weeks"] > 0)

    detail["reclassification"] = (
        "within_turning_month"
        if detail["position_of_first_confirmed_recovery"] == "within_turning_month"
        else detail["position_of_first_confirmed_recovery"]
    )
    detail["definitively_premature"] = detail["reclassification"] == "pre_trough_recovery"
    detail["independent_weekly_evidence_of_a_later_trough"] = None
    detail["note"] = (
        "NBER은 저점을 **월**로만 준다. 저점 월 안에서 시작한 회복을 조기라고 단정하려면 "
        "그 달 안 어느 주 뒤에 저점이 있었다는 독립적인 주간 증거가 필요하다. 그런 증거를 "
        "지어내지 않는다."
    )
    return detail


def amber_conditions(
    decomposition: dict[str, Any],
    scans: list[dict[str, Any]],
    recheck_result: dict[str, Any],
    path: pd.DataFrame,
    round_trips: int | None,
) -> dict[str, Any]:
    """§6. amber가 잠정 채택을 지지할 수 있는지. 여섯 조건 전부가 참이어야 한다."""

    layers = decomposition["layers"]
    state_machine = decomposition["state_machine_delay_weeks"]
    allowance = decomposition["confirmation_allowance_weeks"]
    adjusted = decomposition["evidence_availability_adjusted_latency_weeks"]
    official_minus_raw = (layers["transition_filter_delay_weeks"] or 0) + (
        layers["confirmation_delay_weeks"] or 0
    )
    genuine = [
        scan["episode"]
        for scan in scans
        if scan.get("official_phase__genuine_pre_trough_four_week_recovery") is not None
    ]
    status = path["phase_status"].astype(str)
    official = path["official_phase"].astype(str)
    ambiguous = int(
        (
            status.ne("withheld")
            & ~official.isin(("recovery", "expansion", "slowdown", "contraction"))
        ).sum()
    )
    return {
        "delay_not_extended_by_filter_or_confirmation": {
            "value": state_machine,
            "limit": allowance,
            "passes": state_machine <= allowance,
        },
        "official_follows_raw_within_confirmation_allowance": {
            "value": official_minus_raw,
            "limit": allowance,
            "passes": official_minus_raw <= allowance,
        },
        "adjusted_latency_within_allowance": {
            "value": adjusted,
            "limit": allowance,
            "passes": adjusted is not None and adjusted <= allowance,
        },
        "no_contraction_recovery_contraction_round_trip_within_13_weeks": {
            "value": round_trips,
            "passes": round_trips == 0,
        },
        "no_genuine_pre_trough_four_week_recovery": {
            "value": genuine,
            "passes": not genuine,
        },
        # 게이트는 공식 국면에 건다 — 운영이 내보내는 것이 공식 국면이고, 앞 단계의
        # 게이트도 같은 층에 걸려 있었다. 층을 바꾸면 재확인이 아니라 다른 시험이 된다.
        # 다만 원시·필터 층에서 나온 앞선 회복 열은 숨기지 않고 그대로 공시한다.
        "pre_trough_recovery_in_the_raw_or_filtered_layer": {
            "value": {
                scan["episode"]: {
                    "raw": scan.get("raw_phase__first_four_week_recovery_from_the_peak"),
                    "raw_position": scan.get("raw_phase__four_week_sequence_position"),
                    "filtered_winner": scan.get(
                        "filtered_winner__first_four_week_recovery_from_the_peak"
                    ),
                    "filtered_winner_position": scan.get(
                        "filtered_winner__four_week_sequence_position"
                    ),
                }
                for scan in scans
                if scan.get("raw_phase__four_week_sequence_position")
                in ("entirely_pre_trough_month", "begins_pre_trough_and_overlaps_turning_month")
                or scan.get("filtered_winner__four_week_sequence_position")
                in ("entirely_pre_trough_month", "begins_pre_trough_and_overlaps_turning_month")
            },
            "reported_only": True,
            "passes": True,
        },
        "previously_passed_gates_still_pass": {
            "value": recheck_result["regressed_gates"],
            "passes": recheck_result["all_previously_passed_gates_still_pass"],
        },
        "one_unambiguous_official_phase": {
            "value": ambiguous,
            "passes": ambiguous == 0,
        },
    }
