"""후보 I의 동결 설정을 읽고 해시로 잠근다.

검증 프로그램은 매번 해시를 다시 계산해 개발구간 이후로 값이 바뀌지 않았는지 확인한다.
"바꾸지 않았다"는 주장은 검사로만 성립한다.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ..config import Settings
from .classifier import StateThresholds

CONFIG_NAME = "candidate_i.yaml"
CANDIDATE = "candidate_i_current_state"


@dataclass(frozen=True)
class CandidateConfig:
    candidate: str
    development_window: tuple[str, str]
    scale_method: str
    scale_window_years: float
    scale_minimum_years: float
    momentum_weeks: int
    margin: float
    thresholds: StateThresholds
    document: dict[str, Any]
    sha256: str


def load_candidate(settings: Settings) -> CandidateConfig:
    """설정을 읽고 그 파일의 SHA-256을 함께 돌려준다."""

    path: Path = settings.root / "configs" / CONFIG_NAME
    raw = path.read_bytes()
    document = yaml.safe_load(raw.decode("utf-8"))
    if document["candidate"] != CANDIDATE:
        raise ValueError(f"예상과 다른 후보 이름입니다: {document['candidate']}")
    values = document["thresholds"]
    thresholds = StateThresholds(
        neutral_level=float(values["neutral_level"]),
        neutral_momentum=float(values["neutral_momentum"]),
        contraction_level=float(values["contraction_level"]),
        contraction_level_domains=int(values["contraction_level_domains"]),
        contraction_momentum_domains=int(values["contraction_momentum_domains"]),
        concentration_flag=float(values["concentration_flag"]),
        progression_cuts={
            str(key): (float(pair[0]), float(pair[1]))
            for key, pair in values["progression_cuts"].items()
        },
    )
    scale = document["momentum_scale"]
    stabilizer = document["stabilizer"]
    if stabilizer["method"] != "bounded_margin":
        raise ValueError("이 단계는 유계 여유 안정화만 씁니다")
    if float(stabilizer["margin"]) < 0:
        raise ValueError("안정화 여유는 음수일 수 없습니다")
    if str(document.get("radius_role")) != "visualization_and_evidence_only":
        raise ValueError("반지름은 시각화·증거 용도로만 쓸 수 있습니다")
    return CandidateConfig(
        candidate=str(document["candidate"]),
        development_window=(
            str(document["development_window"][0]),
            str(document["development_window"][1]),
        ),
        scale_method=str(scale["method"]),
        scale_window_years=float(scale["window_years"]),
        scale_minimum_years=float(scale["minimum_years"]),
        momentum_weeks=int(document["momentum_weeks"]),
        margin=float(stabilizer["margin"]),
        thresholds=thresholds,
        document=document,
        sha256=hashlib.sha256(raw).hexdigest(),
    )


def verify_hash(settings: Settings, expected: str) -> bool:
    """개발구간 이후 설정이 바뀌지 않았는지 확인한다."""

    return load_candidate(settings).sha256 == expected
