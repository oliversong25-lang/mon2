"""§9. 분류 셋 중 정확히 하나. 기계적으로 적용하고 우회 경로를 두지 않는다.

`final_validated`는 이 단계가 낼 수 있는 값이 아니다. 목록에 넣지도 않는다.
"""

from __future__ import annotations

from typing import Any, Final

CLASSIFICATIONS: Final[tuple[str, ...]] = (
    "provisional_operational_adoption",
    "operational_rejection_confirmed",
    "measurement_definition_failure",
)

FORBIDDEN_CLASSIFICATION: Final[str] = "final_validated"


class ForbiddenClassification(RuntimeError):
    """이 단계가 낼 수 없는 분류를 내려 했다."""


def classify(
    reclassification_2009: str | None,
    genuine_pre_trough_episodes: list[str],
    calendar_band: str | None,
    amber: dict[str, dict[str, Any]],
    all_previous_gates_pass: bool,
    model_or_parameter_changed: bool,
    measurable: bool,
    unresolved_measurement_defect: str | None = None,
) -> dict[str, Any]:
    """결정 트리. B의 조건을 먼저 본다 — 실패는 성공보다 먼저 읽혀야 한다."""

    failed_amber = [name for name, detail in amber.items() if not detail["passes"]]
    rejection_reasons: list[str] = []
    if genuine_pre_trough_episodes:
        rejection_reasons.append(
            f"저점 월보다 앞선 확인된 회복이 있다: {', '.join(genuine_pre_trough_episodes)}"
        )
    if calendar_band == "red":
        rejection_reasons.append("2020년 달력 회복 지연이 red다")
    if not amber["delay_not_extended_by_filter_or_confirmation"]["passes"]:
        rejection_reasons.append("상태 기계가 허용된 확인 한도를 넘겨 회복을 붙잡았다")
    if not amber["no_contraction_recovery_contraction_round_trip_within_13_weeks"]["passes"]:
        rejection_reasons.append("13주 안에 침체-회복-침체 왕복이 있었다")
    if not all_previous_gates_pass:
        rejection_reasons.append("사전 통과 게이트가 퇴행했다")

    if rejection_reasons:
        classification = "operational_rejection_confirmed"
        reason = "; ".join(rejection_reasons)
    elif not measurable or unresolved_measurement_defect:
        classification = "measurement_definition_failure"
        reason = unresolved_measurement_defect or "회복 게이트를 자료에서 잴 수 없다"
    elif (
        reclassification_2009 == "within_turning_month"
        and calendar_band in ("green", "amber")
        and not failed_amber
        and all_previous_gates_pass
        and not model_or_parameter_changed
    ):
        classification = "provisional_operational_adoption"
        reason = (
            "2009년 회복은 NBER 전환 월 **안**이고, 저점보다 앞선 확인된 회복은 없으며, "
            f"2020년 달력 지연은 {calendar_band}이고 §6 조건이 모두 통과했다"
        )
    else:
        classification = "operational_rejection_confirmed"
        reason = f"§6 조건 실패: {', '.join(failed_amber) or '미충족 조건 있음'}"

    if classification == FORBIDDEN_CLASSIFICATION or classification not in CLASSIFICATIONS:
        raise ForbiddenClassification(f"허용되지 않은 분류입니다: {classification}")
    return {
        "classification": classification,
        "reason": reason,
        "allowed_classifications": list(CLASSIFICATIONS),
        "forbidden_classification": FORBIDDEN_CLASSIFICATION,
        "failed_amber_conditions": failed_amber,
        "rejection_reasons": rejection_reasons,
        "reclassification_2009": reclassification_2009,
        "calendar_band_2020": calendar_band,
        "model_or_parameter_changed": model_or_parameter_changed,
        "is_final_validation": False,
        "prior_v1_1_status_under_the_latest_vintage_protocol": "rejected",
    }
