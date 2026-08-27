"""§10. 잠금 또는 기각. 정확히 하나.

§5의 통과 목록과 §10의 잠금 조건은 같지 않다. §5는 규칙 표이고 §10은 결정 규칙이다.
둘이 갈리는 지점을 조용히 넘기지 않는다 — §5 항목이 문자 그대로 어긋나면 그 사실을
기록하고, §10의 조건을 그대로 적용한 결과와 **함께** 낸다. 규칙을 결과에 맞춰 고쳐
쓰지 않는다.
"""

from __future__ import annotations

from typing import Any, Final

CLASSIFICATIONS: Final[tuple[str, ...]] = (
    "provisional_model_locked",
    "operational_rejection_confirmed",
)

FORBIDDEN_CLASSIFICATION: Final[str] = "final_validated"


class ForbiddenClassification(RuntimeError):
    """이 단계가 낼 수 없는 분류를 내려 했다."""


def decide(
    high_evidence_conflicts: int,
    bounded_delays_within_limits: bool,
    low_evidence_disclosed: bool,
    path_2001: dict[str, Any],
    previous_gates_pass: bool,
    hashes_unchanged: bool,
    hard_rules: dict[str, dict[str, Any]],
    repeated_contradiction_beyond_the_bound: bool,
) -> dict[str, Any]:
    """§10의 기계적 결정. 기각 조건을 먼저 읽는다."""

    rejection: list[str] = []
    if high_evidence_conflicts > 0:
        rejection.append(f"높은 증거 의미 충돌 {high_evidence_conflicts}건")
    if repeated_contradiction_beyond_the_bound:
        rejection.append("공식 국면이 기존 지연 한도를 넘겨 반복적으로 증거를 거슬렀다")
    if path_2001["exposes_a_real_semantic_failure"]:
        rejection.append("2001년 경로가 실제 의미 실패를 드러냈다")
    if not previous_gates_pass:
        rejection.append("이전 게이트가 퇴행했다")
    if not hashes_unchanged:
        rejection.append("보호 지문이 바뀌었다")
    if not bounded_delays_within_limits:
        rejection.append("유계 지연이 기존 한도를 넘었다")
    if not low_evidence_disclosed:
        rejection.append("낮은 증거가 높다고 보고됐다")

    if rejection:
        classification = "operational_rejection_confirmed"
        reason = "; ".join(rejection)
    else:
        classification = "provisional_model_locked"
        reason = (
            "높은 증거 의미 충돌 0건, 유계 지연이 한도 안, 낮은 증거가 낮다고 보고됐고, "
            "2001년 경로에 숨은 높은 증거 모순이 없으며, 이전 게이트와 보호 지문이 그대로다"
        )

    failed_hard_rules = [name for name, entry in hard_rules.items() if not entry["passes"]]
    if classification == FORBIDDEN_CLASSIFICATION or classification not in CLASSIFICATIONS:
        raise ForbiddenClassification(f"허용되지 않은 분류입니다: {classification}")
    return {
        "classification": classification,
        "reason": reason,
        "allowed_classifications": list(CLASSIFICATIONS),
        "forbidden_classification": FORBIDDEN_CLASSIFICATION,
        "rejection_reasons": rejection,
        "high_evidence_semantic_conflicts": high_evidence_conflicts,
        # §5 표가 문자 그대로 어긋난 항목. §10의 잠금 조건 목록에는 들어 있지 않지만,
        # 어긋났다는 사실 자체를 결정 산출물에 남긴다. 조용히 통과시키지 않는다.
        "hard_rules_literally_failing": failed_hard_rules,
        "hard_rule_failures_are_disclosed_not_gated": bool(failed_hard_rules),
        "counterfactual_if_every_section_five_rule_gated": (
            "operational_rejection_confirmed" if failed_hard_rules else classification
        ),
        "counterfactual_changes_the_decision": bool(
            failed_hard_rules and classification == "provisional_model_locked"
        ),
        "model_status": "provisional",
        "is_final_validation": False,
        "phase_order_reversal_alone_is_not_a_failure": True,
    }
