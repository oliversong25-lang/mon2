"""보호 대상 지문 검증. 하나라도 어긋나면 평가를 시작하지 않는다.

이 단계는 동결 모델을 읽기만 한다. 그 사실을 주장으로 두지 않고 실행 전에 확인한다.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Final

from ..config import Settings, load_settings
from ..four_phase.engine import STOPPED_CONFIG_NAME, load_config

#: 사전 등록된 보호 지문. 접두사로 적어 두고 전체 값과 대조한다.
PROTECTED: Final[dict[str, str]] = {
    "v1_1_config": "e052a4f41ca2d01431bab32e6df8bbd383ea9a2dab09982a6675e789bcc3265a",
    "v1_0_stopped_config": "892fbbfb4b9f72f1611097354298380b14875e955a6f3b0e36a47376c2b53027",
    "selection_rule": "71647ea8e81951229d67f1556bc504aa0f38877ca713c069b8b68a20a88cbcd0",
    "frontier_csv": "2390a22c2e2a877e803767a5ca894573694e92461607f2b30bf7ef666c9b2607",
    "candidate_h": "c367e2a0f8e907b6f927191f03379bab5ea5eace6b671454c4b63e44d4b2bb21",
    "candidate_i": "765e2ee65b70a185159faa928c2df9c734c19e583dc8655ae47c80ec3d056993",
    "candidate_j": "a0d875268f1d720a29659f96695e74391db7fd9a3a0213b8c8970e6399a6098f",
}

SOURCE_COMMIT: Final[str] = "3964e1b12f070d158f3b31ef716a528eecb22b5f"

#: 이 단계가 절대 건드리지 않는 경로. 변경되면 평가를 멈춘다.
PROTECTED_PATHS: Final[tuple[str, ...]] = (
    "configs/four_phase.yaml",
    "configs/four_phase_v1_1.yaml",
    "outputs/four_phase/validation_summary.json",
    "outputs/four_phase_v1_1/validation_summary.json",
    "outputs/four_phase_v1_1/development_frontier.csv",
    "outputs/four_phase_v1_1/alfred_audit/audit_summary.json",
    "src/business_cycle/four_phase/evidence.py",
    "src/business_cycle/four_phase/filter.py",
    "src/business_cycle/four_phase/freshness.py",
    "src/business_cycle/four_phase/engine.py",
)


class ProtectedArtifactChanged(RuntimeError):
    """보호 대상이 바뀌었다. 이 단계는 읽기 전용이므로 즉시 멈춘다."""


#: 등록된 보호 지문은 줄바꿈이 CRLF이던 시절에 계산됐다. 그 뒤 저장소에
#: `.gitattributes`가 들어와 워킹 트리 줄바꿈을 LF로 고정하면서, **내용이 한 글자도
#: 바뀌지 않은 파일의 지문이 어긋나기 시작했다.**
#:
#: 가드가 묻는 것은 "동결 산출물의 **내용**이 바뀌었는가"이지 "어떤 줄바꿈으로 저장돼
#: 있는가"가 아니다. 그래서 해시 전에 줄바꿈을 CRLF로 정규화한다 — 등록된 지문이 그
#: 형태로 계산됐기 때문이고, 내용이 실제로 바뀌면 여전히 잡힌다.
#:
#: 지문 상수를 LF 기준으로 다시 적지 않는 이유는, 그러면 **가드가 통과하도록 기대값을
#: 고친 것**과 구분되지 않기 때문이다. 정규화는 어느 쪽 줄바꿈에서도 같은 값을 내므로
#: 그 의심이 남지 않는다.
CANONICAL_EOL: Final[bytes] = b"\r\n"


def _sha256(path: Path) -> str:
    raw = path.read_bytes()
    normalised = raw.replace(b"\r\n", b"\n").replace(b"\n", CANONICAL_EOL)
    return hashlib.sha256(normalised).hexdigest()


def measure(settings: Settings | None = None) -> dict[str, str]:
    base = settings or load_settings()
    from ..four_phase import frontier as FR

    root = base.root
    return {
        "v1_1_config": load_config(base).sha256,
        "v1_0_stopped_config": load_config(base, STOPPED_CONFIG_NAME).sha256,
        "selection_rule": FR.selection_rule_digest(),
        "frontier_csv": _sha256(root / "outputs/four_phase_v1_1/development_frontier.csv"),
        "candidate_h": json.loads(
            (root / "outputs/robustness_validation/phase6/validation_summary.json").read_text(
                encoding="utf-8"
            )
        )["frozen_hash"],
        "candidate_i": (root / "outputs/current_state/frozen_candidate_config.sha256")
        .read_text(encoding="utf-8")
        .split()[0],
        "candidate_j": (root / "outputs/candidate_j/frozen_candidate_config.sha256")
        .read_text(encoding="utf-8")
        .split()[0],
    }


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


def verify(settings: Settings | None = None) -> dict[str, Any]:
    """§2. 무엇을 심사하는지 못박고, 읽기 전용임을 확인한다."""

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
    return {
        "verified": True,
        "hashes": measured,
        "expected_source_commit": SOURCE_COMMIT,
        "protected_paths_clean": True,
        "stage": "operational_review",
        "stage_is_read_only": True,
        "parameter_search_run": False,
        "v1_1_adoption_status": "rejected",
    }
