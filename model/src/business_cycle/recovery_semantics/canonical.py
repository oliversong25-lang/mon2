"""§5. 결정을 지키는 지문. 파일 시각이 아니라 **뜻**을 잠근다.

두 깨끗한 프로세스가 같은 결과를 냈는지 보려면 실행 시각처럼 매번 달라지는 값을 빼야
한다. 그런데 빼는 목록을 넓게 잡으면 지키려던 것까지 빠져나간다. 그래서 뺄 것을 고르지
않고 **지킬 것을 고른다** — 허용목록 투영이다. 새 필드가 생겨도 저절로 보호되지 않고,
넣기로 결정해야 들어간다. 조용히 새어 나가는 쪽보다 조용히 빠지는 쪽이 눈에 띈다.

투영에서 제외되는 것은 명시적으로 휘발성인 값뿐이다.

``executed_at_utc``   실행 시각
``head_commit``       이 단계를 커밋하면 바뀐다. 대신 `expected_source_commit`을 넣는다
``window``·경로       임시 경로와 실행 구간 표기

원본 산출물은 그대로 남긴다. 정규화는 비교에만 쓰고 감사 기록을 대체하지 않는다.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Final

#: 비교에서 빼는 값. 이 셋뿐이며, 실질 필드는 하나도 들어가지 않는다.
VOLATILE_FIELDS: Final[tuple[str, ...]] = (
    "executed_at_utc",
    "head_commit",
    "runtime_seconds",
)

#: 지문이 반드시 덮어야 하는 것. §5가 열거한 목록 그대로다.
COVERED: Final[tuple[str, ...]] = (
    "classification",
    "adoption_status",
    "gate_results",
    "protected_hashes",
    "sample_roles",
    "latency_values",
    "episode_classifications",
    "current_official_phase",
    "model_status",
    "decision_reasons",
)


def _gate_results(payload: dict[str, Any]) -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for group, entries in dict(payload["rechecked_gates"]["groups"]).items():
        for name, detail in dict(entries).items():
            flat[f"{group}.{name}"] = {
                "passes": bool(detail["passes"]),
                "value": detail.get("value"),
                "limit": detail.get("limit"),
            }
    for name, detail in dict(payload["amber_conditions"]).items():
        flat[f"amber.{name}"] = {
            "passes": bool(detail["passes"]),
            "value": detail.get("value"),
            "limit": detail.get("limit"),
        }
    flat["regressed_gates"] = list(payload["rechecked_gates"]["regressed_gates"])
    flat["missing_gates"] = list(payload["rechecked_gates"]["missing_gates"])
    return flat


def _latency_values(payload: dict[str, Any]) -> dict[str, Any]:
    decomposition = dict(payload["delay_decomposition"])
    return {
        "calendar_recovery_latency_weeks": decomposition["calendar_recovery_latency_weeks"],
        "calendar_recovery_latency_days": decomposition["calendar_recovery_latency_days"],
        "calendar_band": decomposition["calendar_band"],
        "evidence_availability_adjusted_latency_weeks": decomposition[
            "evidence_availability_adjusted_latency_weeks"
        ],
        "confirmation_allowance_weeks": decomposition["confirmation_allowance_weeks"],
        "state_machine_delay_weeks": decomposition["state_machine_delay_weeks"],
        "limitation_label": decomposition["limitation_label"],
        "layers": dict(decomposition["layers"]),
        "segments": [
            {
                "segment": row["segment"],
                "start_date": row["start_date"],
                "end_date": row["end_date"],
                "duration_weeks": row["duration_weeks"],
            }
            for row in decomposition["segments"]
        ],
        "dates": dict(decomposition["dates"]),
        "invariants_hold": bool(decomposition["invariants"]["holds"]),
    }


def _episode_classifications(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "turning_month_audit": [
            {
                "episode": row["episode"],
                "position": row["position"],
                "calendar_band": row["calendar_band"],
                "calendar_recovery_latency_weeks": row["calendar_recovery_latency_weeks"],
                "gated": row["gated"],
            }
            for row in payload["turning_month_audit"]
        ],
        "episode_2009_reclassification": payload["episode_2009"]["reclassification"],
        "layer_recovery_timelines": [
            {
                "episode": row["episode"],
                "layer": row["layer"],
                "sequence_position": row["sequence_position"],
                "first_four_week_sequence_start": row["first_four_week_sequence_start"],
                "first_four_week_sequence_end": row["first_four_week_sequence_end"],
            }
            for row in payload["layer_recovery_timelines"]
        ],
        "red_scope": {
            key: payload["red_scope_audit"][key]
            for key in (
                "scope",
                "gate_episode",
                "episodes_in_the_red_band",
                "counterfactual_if_the_red_gate_applied_to_every_episode",
            )
        },
        "post_trough_gap": {
            payload["post_trough_gap"]["episode"]: {
                "gap_kind": payload["post_trough_gap"]["gap_kind"],
                "gap_weeks_after_the_trough_month": payload["post_trough_gap"][
                    "gap_weeks_after_the_trough_month"
                ],
                "official_contraction_weeks_in_the_gap": payload["post_trough_gap"][
                    "official_contraction_weeks_in_the_gap"
                ],
            }
        },
    }


def project(payload: dict[str, Any]) -> dict[str, Any]:
    """실질 내용만 남긴 정규 투영. 휘발성 값은 애초에 들어오지 않는다."""

    decision = dict(payload["decision"])
    state = payload.get("current_state") or {}
    return {
        "classification": decision["classification"],
        "adoption_status": decision["classification"] == "provisional_operational_adoption",
        "decision_reasons": {
            "reason": decision["reason"],
            "rejection_reasons": list(decision["rejection_reasons"]),
            "failed_amber_conditions": list(decision["failed_amber_conditions"]),
        },
        "model_status": payload.get("model_status"),
        "protected_hashes": dict(payload["provenance"]["hashes"]),
        "expected_source_commit": payload["provenance"]["expected_source_commit"],
        "prior_decisions_preserved": dict(payload["provenance"]["prior_decisions_preserved"]),
        "sample_roles": dict(payload["sample_roles"]),
        "gate_results": _gate_results(payload),
        "latency_values": _latency_values(payload),
        "episode_classifications": _episode_classifications(payload),
        "current_official_phase": state.get("official_current_phase"),
        "current_phase_status": state.get("phase_status"),
        "current_as_of_date": state.get("as_of_date"),
    }


def semantic_digest(payload: dict[str, Any]) -> str:
    """정규 투영의 SHA-256. 시각만 바뀌면 같고, 실질이 바뀌면 달라진다."""

    body = json.dumps(project(payload), ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()
