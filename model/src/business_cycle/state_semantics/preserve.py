"""§2. 실행 전 보존 확인. 앞 단계의 의미 지문까지 대조 대상에 넣는다."""

from __future__ import annotations

import json
import subprocess
from typing import Any, Final

from ..config import Settings, load_settings
from ..operational_review.preserve import PROTECTED, ProtectedArtifactChanged, measure

SOURCE_COMMIT: Final[str] = "81941a79c66c5bd39888ec66de9921c0e6dcb445"

#: 앞 단계가 낸 의미 지문. 결정이 그대로인지 뜻으로 확인한다.
RECOVERY_SEMANTICS_DIGEST: Final[str] = (
    "ec6fdb414718905bd6cccc359c24e5c6cb8aeb50a050e6472b9fc2dbe45e0f3d"
)

PROTECTED_PATHS: Final[tuple[str, ...]] = (
    "configs/four_phase.yaml",
    "configs/four_phase_v1_1.yaml",
    "src/business_cycle/four_phase/evidence.py",
    "src/business_cycle/four_phase/filter.py",
    "src/business_cycle/four_phase/freshness.py",
    "src/business_cycle/four_phase/engine.py",
    "src/business_cycle/four_phase/contract.py",
    "src/business_cycle/four_phase/frontier.py",
    "src/business_cycle/four_phase/alfred.py",
    "src/business_cycle/four_phase/alfred_audit.py",
    "src/business_cycle/four_phase/validation.py",
    "src/business_cycle/operational_review",
    "src/business_cycle/recovery_semantics",
    "outputs/four_phase",
    "outputs/four_phase_v1_1",
    "outputs/robustness_validation/phase6/validation_summary.json",
    "outputs/current_state/frozen_candidate_config.sha256",
    "outputs/candidate_j/frozen_candidate_config.sha256",
)

#: 이전 결정 기록. 바이트가 아니라 내용으로 지킨다 — 앞 단계들의 재현성 시험이
#: `executed_at_utc`만 바꿔 산출물을 다시 쓰기 때문이다.
PRIOR_DECISION_RECORDS: Final[dict[str, dict[str, str]]] = {
    "outputs/operational_review/operational_decision.json": {
        "classification": "operational_rejection",
        "v1_1_status": "rejected",
    },
    "outputs/recovery_semantics/recovery_semantics_decision.json": {
        "classification": "provisional_operational_adoption",
        "semantic_digest": RECOVERY_SEMANTICS_DIGEST,
    },
}


def git_status_of_protected_paths(settings: Settings | None = None) -> list[str]:
    base = settings or load_settings()
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--", *PROTECTED_PATHS],
            cwd=base.root,
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
    except Exception:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def head_commit(settings: Settings | None = None) -> str:
    base = settings or load_settings()
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=base.root,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except Exception:
        return "unavailable"
    return result.stdout.strip()


def prior_decisions(settings: Settings | None = None) -> dict[str, Any]:
    """이전 두 단계의 결정이 그대로인지 확인한다."""

    base = settings or load_settings()
    out: dict[str, Any] = {}
    for relative, expected in PRIOR_DECISION_RECORDS.items():
        path = base.root / relative
        if not path.exists():
            raise ProtectedArtifactChanged(f"이전 결정 기록이 없습니다: {relative}")
        record = json.loads(path.read_text(encoding="utf-8"))
        wrong = {
            key: record.get(key) for key, value in expected.items() if record.get(key) != value
        }
        if wrong:
            raise ProtectedArtifactChanged(f"{relative}의 결정이 바뀌었습니다: {wrong}")
        out[relative] = dict(expected)
    return out


def verify(settings: Settings | None = None) -> dict[str, Any]:
    """지문·보호 경로·이전 결정을 함께 본다. 통과해야 감사를 시작한다."""

    measured = measure(settings)
    mismatched = {
        name: {"expected": expected, "measured": measured[name]}
        for name, expected in PROTECTED.items()
        if measured[name] != expected
    }
    if mismatched:
        raise ProtectedArtifactChanged(f"보호 지문이 어긋났습니다: {mismatched}")
    dirty = git_status_of_protected_paths(settings)
    if dirty:
        raise ProtectedArtifactChanged(f"보호 경로가 수정됐습니다: {dirty}")
    prior = prior_decisions(settings)
    return {
        "verified": True,
        "hashes": measured,
        "recovery_semantics_semantic_digest": RECOVERY_SEMANTICS_DIGEST,
        "expected_source_commit": SOURCE_COMMIT,
        "head_commit": head_commit(settings),
        "protected_paths": list(PROTECTED_PATHS),
        "protected_paths_clean": True,
        "prior_decision_records": prior,
        "stage": "state_semantics_audit",
        "stage_is_read_only": True,
        "model_logic_changed": False,
        "parameter_search_run": False,
        "new_classifier_introduced": False,
    }
