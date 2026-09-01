"""핵심 모델 패리티 검사. 해석층이 모델을 건드리지 않았는지 기계로 확인한다.

"안 건드렸다"는 주장은 검사로만 성립한다. 여기서는 공식 대국면·세부국면·12개 확률·
X·Y·반지름·상태를 정해진 자리수로 정규화해 SHA-256으로 굳힌다. 값이 하나라도 달라지면
해시가 달라지고, 그때는 멈추고 원인을 찾는다. 모델을 고쳐서 해시를 맞추지 않는다.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

#: 비교에 쓰는 자리수. 부동소수 표현 차이로 해시가 흔들리지 않게 고정한다.
DECIMALS: int = 10

CORE_COLUMNS: tuple[str, ...] = ("x", "y", "radius", "phase_code", "broad_phase")


@dataclass(frozen=True)
class ParityResult:
    matches: bool
    recorded_hash: str
    measured_hash: str
    weeks: int
    first_difference: str


def core_frame(history: pd.DataFrame, status: str) -> pd.DataFrame:
    """패리티 대상만 뽑아 정규화한다."""

    probability_columns = sorted(
        str(column) for column in history.columns if str(column).startswith("p_")
    )
    frame = history[[*CORE_COLUMNS, *probability_columns]].copy()
    frame.index = pd.Index([str(pd.Timestamp(str(value)).date()) for value in frame.index])
    for column in frame.columns:
        if frame[column].dtype.kind == "f":
            frame[column] = frame[column].round(DECIMALS)
    frame["model_status"] = status
    return frame


def frame_hash(frame: pd.DataFrame) -> str:
    payload = frame.to_csv(lineterminator="\n").encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def write_baseline(frame: pd.DataFrame, path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, lineterminator="\n")
    digest = frame_hash(frame)
    path.with_suffix(".sha256").write_text(digest + "\n", encoding="utf-8", newline="\n")
    return digest


def compare(frame: pd.DataFrame, path: Path) -> ParityResult:
    """기록된 기준선과 지금 값을 정확히 비교한다."""

    recorded_path = path.with_suffix(".sha256")
    if not path.exists() or not recorded_path.exists():
        digest = write_baseline(frame, path)
        return ParityResult(True, digest, digest, int(len(frame)), "")
    recorded = recorded_path.read_text(encoding="utf-8").split()[0]
    measured = frame_hash(frame)
    if recorded == measured:
        return ParityResult(True, recorded, measured, int(len(frame)), "")
    baseline = pd.read_csv(path, index_col=0)
    difference = ""
    common = baseline.index.intersection(frame.index)
    for week in common:
        left = baseline.loc[week]
        right = frame.loc[week]
        for column in baseline.columns:
            if column not in frame.columns:
                difference = f"{week}: 열 {column}이(가) 사라졌다"
                break
            if str(left[column]) != str(right[column]):
                difference = f"{week}: {column} 기준 {left[column]} → 측정 {right[column]}"
                break
        if difference:
            break
    if not difference:
        difference = f"행 수 기준 {len(baseline)} → 측정 {len(frame)}"
    return ParityResult(False, recorded, measured, int(len(frame)), difference)


def artifact_hashes(paths: list[Path], root: Path | None = None) -> dict[str, str]:
    """후보 H·H2 산출물이 그대로인지 확인할 파일 해시.

    키는 파일명이 아니라 루트 기준 상대경로다. phase7과 phase8에 같은 이름의 파일이
    있어 파일명으로만 키를 잡으면 하나가 다른 하나를 덮어쓴다.
    """

    digests: dict[str, str] = {}
    for path in sorted(paths):
        if not (path.exists() and path.is_file()):
            continue
        key = str(path.relative_to(root)).replace("\\", "/") if root else path.name
        digests[key] = hashlib.sha256(path.read_bytes()).hexdigest()
    return digests


def parity_report(result: ParityResult, artifacts: dict[str, Any]) -> dict[str, Any]:
    return {
        "core_model_parity": result.matches,
        "recorded_hash": result.recorded_hash,
        "measured_hash": result.measured_hash,
        "weeks_compared": result.weeks,
        "first_difference": result.first_difference,
        "compared_fields": [
            "official_broad_phase",
            "official_detailed_phase",
            "phase_probabilities",
            "x",
            "y",
            "radius",
            "model_status",
        ],
        "artifact_hashes": artifacts,
    }


def dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n"
