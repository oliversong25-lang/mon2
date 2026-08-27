"""§11. 의미 지문. 앞 단계의 허용목록에 이번 단계의 실질을 더한다.

빼는 것은 명시적으로 휘발성인 실행 메타데이터뿐이다. 주별 의미 분류, 2001년 경로 분류,
높은 증거 충돌 수, 현재 국면의 의미 분류, 직전 상태 의존도, 최종 잠금·기각 결정이 모두
들어간다.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Final

VOLATILE_FIELDS: Final[tuple[str, ...]] = (
    "executed_at_utc",
    "head_commit",
    "runtime_seconds",
)

COVERED: Final[tuple[str, ...]] = (
    "classification",
    "protected_hashes",
    "weekly_semantic_classifications",
    "path_2001_classification",
    "high_evidence_conflict_count",
    "current_phase_semantic_classification",
    "previous_state_dependence",
    "hard_rules",
    "episode_kinds",
    "decision_reasons",
)


def _weekly(payload: dict[str, Any]) -> dict[str, Any]:
    """주별 분류를 표본별 분포와 순서 지문으로 압축한다. 한 주가 바뀌면 값이 달라진다."""

    out: dict[str, Any] = {}
    for sample, rows in dict(payload["weekly_class_sequence"]).items():
        joined = "|".join(f"{week}:{label}" for week, label in rows)
        out[sample] = {
            "weeks": len(rows),
            "sequence_sha256": hashlib.sha256(joined.encode("utf-8")).hexdigest(),
            "counts": dict(payload["samples"][sample]["class_counts"]),
        }
    return out


def project(payload: dict[str, Any]) -> dict[str, Any]:
    decision = dict(payload["decision"])
    current = dict(payload["current_state_semantics"])
    return {
        "classification": decision["classification"],
        "decision_reasons": {
            "reason": decision["reason"],
            "rejection_reasons": list(decision["rejection_reasons"]),
            "hard_rules_literally_failing": list(decision["hard_rules_literally_failing"]),
            "counterfactual_if_every_section_five_rule_gated": decision[
                "counterfactual_if_every_section_five_rule_gated"
            ],
        },
        "model_status": decision["model_status"],
        "protected_hashes": dict(payload["provenance"]["hashes"]),
        "recovery_semantics_semantic_digest": payload["provenance"][
            "recovery_semantics_semantic_digest"
        ],
        "expected_source_commit": payload["provenance"]["expected_source_commit"],
        "weekly_semantic_classifications": _weekly(payload),
        "high_evidence_conflict_count": {
            sample: entry["high_evidence_semantic_conflicts"]
            for sample, entry in dict(payload["samples"]).items()
        },
        "path_2001_classification": {
            "class_counts": dict(payload["path_2001"]["class_counts"]),
            "semantic_conflict_weeks": payload["path_2001"]["semantic_conflict_weeks"],
            "high_evidence_both_signs_contradicted_weeks": payload["path_2001"][
                "high_evidence_both_signs_contradicted_weeks"
            ],
            "transitions": payload["path_2001"]["official_phase_transitions"],
        },
        "current_phase_semantic_classification": {
            "as_of_date": current["as_of_date"],
            "official_current_phase": current["official_current_phase"],
            "evidence_quality": current["evidence_quality"],
            "semantic_class": current["semantic_class"],
            "previous_state_materially_determines_the_result": current[
                "previous_state_materially_determines_the_result"
            ],
        },
        "previous_state_dependence": {
            sample: {
                "weeks": entry["previous_state_retention_weeks"],
                "share": entry["previous_state_retention_share"],
            }
            for sample, entry in dict(payload["samples"]).items()
        },
        "hard_rules": {
            sample: {name: entry["passes"] for name, entry in dict(rules).items()}
            for sample, rules in dict(payload["hard_rules"]).items()
        },
        "episode_kinds": {episode["episode"]: episode["kind"] for episode in payload["episodes"]},
    }


def semantic_digest(payload: dict[str, Any]) -> str:
    body = json.dumps(project(payload), ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()
