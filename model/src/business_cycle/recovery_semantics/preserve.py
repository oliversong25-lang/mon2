"""§2. 실행 **전에** 보호 대상을 확인한다. 하나라도 어긋나면 즉시 멈춘다.

지문 목록은 앞 단계에서 그대로 가져온다. 여기서 다시 적으면 두 벌이 되고, 두 벌은
언젠가 갈라진다. 이 단계가 더하는 것은 소스 커밋과 보호 경로뿐이다.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any, Final

from ..config import Settings, load_settings
from ..operational_review.preserve import PROTECTED, ProtectedArtifactChanged, measure

#: 이 단계가 이어받는 커밋. 앞 단계의 결론이 여기까지 그대로 온다.
SOURCE_COMMIT: Final[str] = "4f7160445d36f2f064763afd8f1b39590aff1baa"

#: 동결 모델 코드·설정·이전 검증 산출물·이전 기각 기록. 이 단계는 읽기만 한다.
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
    "outputs/four_phase",
    "outputs/four_phase_v1_1",
    "outputs/robustness_validation/phase6/validation_summary.json",
    "outputs/current_state/frozen_candidate_config.sha256",
    "outputs/candidate_j/frozen_candidate_config.sha256",
)

#: 앞 단계가 남긴 기각 기록. 이 단계가 덮어쓰지 않는다.
PRIOR_DECISIONS: Final[dict[str, str]] = {
    "four_phase_v1_1_latest_vintage_protocol": "rejected",
    "operational_review": "operational_rejection",
}

#: 앞 단계의 결정 기록. 파일 바이트가 아니라 **뜻**을 지킨다.
#:
#: 바이트로 잠글 수 없는 이유가 있다. 앞 단계의 재현성 시험이 그 단계를 다시 돌리면
#: `executed_at_utc`만 바뀐 산출물이 다시 쓰인다. 결정도 지문도 그대로인데 git은 수정으로
#: 본다. 그 차이로 이 단계를 멈추면 지키는 것은 결정이 아니라 파일 시각이다.
PRIOR_DECISION_RECORD: Final[str] = "outputs/operational_review/operational_decision.json"


def git_status_of_protected_paths(settings: Settings | None = None) -> list[str]:
    """보호 경로 중 작업 트리에서 수정된 것. 비어 있어야 한다."""

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


def prior_decision_record(settings: Settings | None = None) -> dict[str, Any]:
    """앞 단계의 기각 기록이 그대로인지 뜻으로 확인한다. 어긋나면 멈춘다."""

    base = settings or load_settings()
    path = base.root / PRIOR_DECISION_RECORD
    if not path.exists():
        raise ProtectedArtifactChanged(f"앞 단계의 결정 기록이 없습니다: {PRIOR_DECISION_RECORD}")
    record = json.loads(path.read_text(encoding="utf-8"))
    expected = {
        "classification": PRIOR_DECISIONS["operational_review"],
        "v1_1_status": "rejected",
        "model_status": "rejected",
    }
    wrong = {
        name: record.get(name) for name, value in expected.items() if record.get(name) != value
    }
    if wrong:
        raise ProtectedArtifactChanged(f"앞 단계의 결정이 바뀌었습니다: {wrong}")
    if dict(record.get("hashes", {})) != dict(PROTECTED):
        raise ProtectedArtifactChanged("앞 단계가 기록한 동결 지문이 지금과 다릅니다")
    return {
        "path": PRIOR_DECISION_RECORD,
        "classification": record["classification"],
        "v1_1_status": record["v1_1_status"],
        "failed_gates": list(record.get("failed_gates", [])),
        "preserved": True,
        "compared_by": "decision_content_not_file_bytes",
    }


def verify(settings: Settings | None = None) -> dict[str, Any]:
    """지문과 보호 경로를 함께 본다. 분석은 이 함수가 통과한 뒤에만 시작한다."""

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
    prior = prior_decision_record(settings)
    return {
        "verified": True,
        "hashes": measured,
        "expected_source_commit": SOURCE_COMMIT,
        "head_commit": head_commit(settings),
        "protected_paths": list(PROTECTED_PATHS),
        "protected_paths_clean": True,
        "stage": "recovery_semantics",
        "stage_is_read_only": True,
        "parameter_search_run": False,
        "model_logic_changed": False,
        "prior_decisions_preserved": dict(PRIOR_DECISIONS),
        "prior_decision_record": prior,
    }
