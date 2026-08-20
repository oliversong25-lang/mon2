"""4국면 엔진. 관측에서 공식 현재국면 하나까지를 인과적으로 만든다.

전처리는 후보 I·J에서 확정된 결론을 그대로 쓴다 — 원래 변환, 재디플레이트 없음,
인과 rolling MAD, 발표 인식 모멘텀. X·Y는 만들지만 **진단용**이며 공식 분류에
쓰이지 않는다. 반지름 임계값은 어디에도 없다.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from ..candidate_j import aggregate as A
from ..config import Settings
from ..current_state import domains as D
from ..current_state import scales as S
from ..current_state.signals import indicator_signals
from . import evidence as E
from .filter import FourStateFilter, filter_scores

CONFIG_NAME = "four_phase.yaml"
MODEL_NAME = "us_four_phase_v1"
WEEKS_PER_YEAR = 52.1775


@dataclass(frozen=True)
class FourPhaseConfig:
    document: dict[str, Any]
    sha256: str
    thresholds: E.Thresholds

    @property
    def cap_level(self) -> float:
        return float(self.document["domain_caps"]["level"])

    @property
    def cap_momentum(self) -> float:
        return float(self.document["domain_caps"]["momentum"])

    @property
    def lam(self) -> float:
        return float(self.document["soft_filter"]["lambda"])

    @property
    def epsilon(self) -> float:
        return float(self.document["soft_filter"]["epsilon"])

    @property
    def momentum_weeks(self) -> int:
        return int(self.document["momentum_weeks"])

    @property
    def gates(self) -> dict[str, Any]:
        return dict(self.document["adoption_gates"])


def load_config(settings: Settings) -> FourPhaseConfig:
    path: Path = settings.root / "configs" / CONFIG_NAME
    raw = path.read_bytes()
    document = yaml.safe_load(raw.decode("utf-8"))
    if document["model"] != MODEL_NAME:
        raise ValueError(f"예상과 다른 모델 이름입니다: {document['model']}")
    if float(document["soft_filter"]["epsilon"]) <= 0:
        raise ValueError("epsilon은 0보다 커야 합니다 — 0이면 도달 불가능한 상태가 생긴다")
    if document["radius_role"] != "diagnostic_only":
        raise ValueError("반지름은 진단 용도로만 쓸 수 있습니다")
    return FourPhaseConfig(
        document=document,
        sha256=hashlib.sha256(raw).hexdigest(),
        thresholds=E.Thresholds(**document["thresholds"]),
    )


def verify_frozen(settings: Settings, output_dir: Path) -> str:
    """동결 스냅샷과 지금 파일이 같은지 확인한다. 다르면 실행을 멈춘다."""

    config = load_config(settings)
    recorded_path = output_dir / "frozen_config.sha256"
    if not recorded_path.exists():
        output_dir.mkdir(parents=True, exist_ok=True)
        recorded_path.write_text(config.sha256 + "\n", encoding="utf-8", newline="\n")
        (output_dir / "frozen_config.yaml").write_bytes(
            (settings.root / "configs" / CONFIG_NAME).read_bytes()
        )
        return config.sha256
    recorded = recorded_path.read_text(encoding="utf-8").split()[0]
    if recorded != config.sha256:
        raise RuntimeError(
            "동결 이후 4국면 설정이 바뀌었습니다. 검증을 중단합니다 — "
            f"기록 {recorded[:16]}… 측정 {config.sha256[:16]}…"
        )
    return recorded


@dataclass(frozen=True)
class FourPhaseRun:
    raw_scores: pd.DataFrame
    filtered_scores: pd.DataFrame
    raw_phase: pd.Series
    official_phase: pd.Series
    activity_level: pd.Series
    activity_momentum: pd.Series
    level_scaled: pd.DataFrame
    momentum_scaled: pd.DataFrame
    level_aggregate: A.BoundedAggregate
    momentum_aggregate: A.BoundedAggregate
    negative_level_domains: pd.Series
    negative_momentum_domains: pd.Series
    positive_momentum_domains: pd.Series
    concentration: pd.Series
    contraction_detail: pd.DataFrame
    recovery_detail: pd.DataFrame
    weeks_since_release: pd.DataFrame
    arrived: pd.DataFrame
    events: pd.DataFrame
    coordinates: pd.DataFrame
    thresholds: E.Thresholds


def _persistent_positive(momentum: pd.Series, band: float) -> pd.Series:
    """총량 모멘텀이 연속으로 중립대를 넘어 양수였던 주 수. 그 시점까지만 본다."""

    counts: list[int] = []
    streak = 0
    for value in momentum:
        streak = streak + 1 if float(value) > band else 0
        counts.append(streak)
    return pd.Series(counts, index=momentum.index)


def build(
    observations: pd.DataFrame,
    settings: Settings,
    as_of: pd.Timestamp,
    config: FourPhaseConfig,
) -> FourPhaseRun:
    """그 시점까지의 자료만으로 4국면 실행을 만든다."""

    events, held, _ = indicator_signals(observations, settings, as_of)
    scale = config.document["momentum_scale"]
    window = int(round(WEEKS_PER_YEAR * float(scale["window_years"])))
    minimum = int(round(WEEKS_PER_YEAR * float(scale["minimum_years"])))
    method = str(scale["method"])

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

    thresholds = config.thresholds
    coincident = list(D.COINCIDENT_DOMAINS)
    negative_level, _, _ = D.count_states(level_scaled[coincident], thresholds.neutral_level)
    negative_momentum, positive_momentum, _ = D.count_states(
        momentum_scaled[coincident], thresholds.neutral_momentum
    )
    persistence = _persistent_positive(activity_momentum, thresholds.neutral_momentum)

    contraction_rows: list[dict[str, float]] = []
    recovery_rows: list[dict[str, float]] = []
    score_rows: list[dict[str, float]] = []
    for week in index:
        level = float(activity_level.loc[week])
        momentum = float(activity_momentum.loc[week])
        contraction = E.contraction_evidence(
            level,
            momentum,
            int(negative_level.loc[week]),
            int(negative_momentum.loc[week]),
            float(level_scaled.loc[week, "labor_stress"]),
            float(momentum_scaled.loc[week, "labor_stress"]),
            thresholds,
        )
        recovery = E.recovery_evidence(
            level,
            momentum,
            int(positive_momentum.loc[week]),
            int(persistence.loc[week]),
            thresholds,
        )
        breadth = E.breadth_support(level_scaled.loc[week], momentum_scaled.loc[week], thresholds)
        contraction_rows.append(contraction)
        recovery_rows.append(recovery)
        score_rows.append(
            E.observation_scores(
                level,
                momentum,
                contraction["contraction_evidence"],
                recovery["recovery_evidence"],
                breadth,
                thresholds,
            )
        )
    raw_scores = pd.DataFrame(score_rows, index=index, columns=list(E.PHASES))
    result: FourStateFilter = filter_scores(raw_scores, config.lam, config.epsilon)
    coordinates = pd.DataFrame(
        {
            "x": activity_momentum,
            "y": activity_level,
            "angle": np.degrees(np.arctan2(activity_level, activity_momentum)) % 360.0,
            "radius": np.sqrt(activity_level.pow(2) + activity_momentum.pow(2)),
        }
    )
    return FourPhaseRun(
        raw_scores=raw_scores,
        filtered_scores=result.filtered,
        raw_phase=result.raw,
        official_phase=result.official,
        activity_level=activity_level,
        activity_momentum=activity_momentum,
        level_scaled=level_scaled,
        momentum_scaled=momentum_scaled,
        level_aggregate=level_aggregate,
        momentum_aggregate=momentum_aggregate,
        negative_level_domains=negative_level,
        negative_momentum_domains=negative_momentum,
        positive_momentum_domains=positive_momentum,
        concentration=D.concentration(level_scaled),
        contraction_detail=pd.DataFrame(contraction_rows, index=index),
        recovery_detail=pd.DataFrame(recovery_rows, index=index),
        weeks_since_release=age.reindex(index),
        arrived=arrived.reindex(index),
        events=events,
        coordinates=coordinates,
        thresholds=thresholds,
    )
