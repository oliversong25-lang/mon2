"""후보 J 엔진과 동결 설정 로더.

설정은 개발구간에서 잠갔고, 모든 진입점이 실행 전에 SHA-256을 다시 계산해 일치하지
않으면 멈춘다. "검증 뒤에 바꾸지 않았다"는 주장은 검사로만 성립한다.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from ..config import Settings
from ..current_state import domains as D
from ..current_state import scales as S
from ..current_state.signals import indicator_signals
from . import aggregate as A
from . import filters as F
from . import hierarchy as H

CONFIG_NAME = "candidate_j.yaml"
CANDIDATE = "candidate_j_hierarchical"
WEEKS_PER_YEAR = 52.1775


@dataclass(frozen=True)
class CandidateJConfig:
    document: dict[str, Any]
    sha256: str
    thresholds: H.MajorThresholds
    cuts: H.SubphaseCuts

    @property
    def cap_level(self) -> float:
        return float(self.document["domain_caps"]["level"])

    @property
    def cap_momentum(self) -> float:
        return float(self.document["domain_caps"]["momentum"])

    @property
    def lambda_major(self) -> float:
        return float(self.document["soft_filter"]["lambda_major"])

    @property
    def lambda_subphase(self) -> float:
        return float(self.document["soft_filter"]["lambda_subphase"])

    @property
    def epsilon(self) -> float:
        return float(self.document["soft_filter"]["epsilon"])

    @property
    def momentum_weeks(self) -> int:
        return int(self.document["momentum_weeks"])

    @property
    def gates(self) -> dict[str, Any]:
        return dict(self.document["acceptance_gates"])


def load_config(settings: Settings) -> CandidateJConfig:
    path: Path = settings.root / "configs" / CONFIG_NAME
    raw = path.read_bytes()
    document = yaml.safe_load(raw.decode("utf-8"))
    if document["candidate"] != CANDIDATE:
        raise ValueError(f"예상과 다른 후보 이름입니다: {document['candidate']}")
    if document["release_carry"]["fabricate_zero_between_releases"]:
        raise ValueError("발표 사이에 0을 만들어내는 설정은 허용하지 않습니다")
    if float(document["soft_filter"]["epsilon"]) <= 0:
        raise ValueError("epsilon은 0보다 커야 합니다 — 0이면 도달 불가능한 상태가 생긴다")
    return CandidateJConfig(
        document=document,
        sha256=hashlib.sha256(raw).hexdigest(),
        thresholds=H.MajorThresholds(**document["major_thresholds"]),
        cuts=H.SubphaseCuts(
            {k: (float(v[0]), float(v[1])) for k, v in document["subphase_cuts"].items()}
        ),
    )


def verify_frozen(settings: Settings, output_dir: Path) -> str:
    """기록된 해시와 지금 파일이 같은지 확인한다. 다르면 실행을 멈춘다."""

    config = load_config(settings)
    recorded_path = output_dir / "frozen_candidate_config.sha256"
    if not recorded_path.exists():
        output_dir.mkdir(parents=True, exist_ok=True)
        recorded_path.write_text(config.sha256 + "\n", encoding="utf-8", newline="\n")
        (output_dir / "frozen_candidate_config.yaml").write_bytes(
            (settings.root / "configs" / CONFIG_NAME).read_bytes()
        )
        return config.sha256
    recorded = recorded_path.read_text(encoding="utf-8").split()[0]
    if recorded != config.sha256:
        raise RuntimeError(
            "동결 이후 후보 J 설정이 바뀌었습니다. 검증을 중단합니다 — "
            f"기록 {recorded[:16]}… 측정 {config.sha256[:16]}…"
        )
    return recorded


@dataclass(frozen=True)
class CandidateJRun:
    official_current_phase: pd.Series
    raw_major_phase: pd.Series
    official_major_phase: pd.Series
    raw_subphase: pd.Series
    official_subphase: pd.Series
    major_scores: pd.DataFrame
    major_filtered: pd.DataFrame
    activity_level: pd.Series
    activity_momentum: pd.Series
    level_scaled: pd.DataFrame
    momentum_scaled: pd.DataFrame
    level_aggregate: A.BoundedAggregate
    momentum_aggregate: A.BoundedAggregate
    negative_level_domains: pd.Series
    negative_momentum_domains: pd.Series
    concentration: pd.Series
    weeks_since_release: pd.DataFrame
    arrived: pd.DataFrame
    events: pd.DataFrame
    coordinates: pd.DataFrame


def build(
    observations: pd.DataFrame,
    settings: Settings,
    as_of: pd.Timestamp,
    config: CandidateJConfig,
) -> CandidateJRun:
    """관측에서 후보 J 실행을 만든다. 전부 그 시점까지의 자료만 쓴다."""

    events, held, _ = indicator_signals(observations, settings, as_of)
    window = int(round(WEEKS_PER_YEAR * float(config.document["momentum_scale"]["window_years"])))
    minimum = int(round(WEEKS_PER_YEAR * float(config.document["momentum_scale"]["minimum_years"])))
    method = str(config.document["momentum_scale"]["method"])

    levels_raw, momentum_raw, arrived, age = A.domain_scores(held, events, config.momentum_weeks)
    level_columns: dict[str, pd.Series] = {}
    momentum_columns: dict[str, pd.Series] = {}
    for domain in D.DOMAINS:
        level_columns[domain] = S.standardize(levels_raw[domain], method, window, minimum)[0]
        momentum_columns[domain] = S.standardize(momentum_raw[domain], method, window, minimum)[0]
    level_scaled = pd.DataFrame(level_columns)[list(D.DOMAINS)]
    momentum_scaled = pd.DataFrame(momentum_columns)[list(D.DOMAINS)]

    usable = level_scaled.notna().all(axis=1) & momentum_scaled.notna().all(axis=1)
    index = level_scaled.index[usable]
    level_scaled = level_scaled.loc[index]
    momentum_scaled = momentum_scaled.loc[index]

    level_aggregate = A.bounded_mean(level_scaled, config.cap_level)
    momentum_aggregate = A.bounded_mean(momentum_scaled, config.cap_momentum)
    activity_level = level_aggregate.aggregate
    activity_momentum = momentum_aggregate.aggregate

    coincident = list(D.COINCIDENT_DOMAINS)
    negative_level, _, _ = D.count_states(level_scaled[coincident], config.thresholds.neutral_level)
    negative_momentum, _, _ = D.count_states(
        momentum_scaled[coincident], config.thresholds.neutral_momentum
    )
    majors = H.major_frame(
        activity_level,
        activity_momentum,
        negative_level,
        negative_momentum,
        level_scaled["labor_stress"],
        config.thresholds,
    )
    subs = H.subphase_frames(
        activity_level, activity_momentum, negative_level, negative_momentum, config.cuts
    )
    result = F.hierarchical_official(
        majors, subs, config.lambda_major, config.lambda_subphase, config.epsilon
    )
    coordinates = pd.DataFrame(
        {
            "x": activity_momentum,
            "y": activity_level,
            "angle": np.degrees(np.arctan2(activity_level, activity_momentum)) % 360.0,
            "radius": np.sqrt(activity_level.pow(2) + activity_momentum.pow(2)),
        }
    )
    return CandidateJRun(
        official_current_phase=result["official_current_phase"],
        raw_major_phase=result["raw_major_phase"],
        official_major_phase=result["official_major_phase"],
        raw_subphase=result["raw_subphase"],
        official_subphase=result["official_subphase"],
        major_scores=majors,
        major_filtered=result["major_filtered"],
        activity_level=activity_level,
        activity_momentum=activity_momentum,
        level_scaled=level_scaled,
        momentum_scaled=momentum_scaled,
        level_aggregate=level_aggregate,
        momentum_aggregate=momentum_aggregate,
        negative_level_domains=negative_level,
        negative_momentum_domains=negative_momentum,
        concentration=D.concentration(level_scaled),
        weeks_since_release=age.reindex(index),
        arrived=arrived.reindex(index),
        events=events,
        coordinates=coordinates,
    )
