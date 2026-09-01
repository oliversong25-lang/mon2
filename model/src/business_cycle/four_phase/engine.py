"""4국면 엔진. 관측에서 공식 현재국면 하나까지를 인과적으로 만든다.

전처리는 후보 I·J에서 확정된 결론을 그대로 쓴다 — 원래 변환, 재디플레이트 없음,
인과 rolling MAD, 발표 인식 모멘텀. X·Y는 만들지만 **진단용**이며 공식 분류에
쓰이지 않는다. 반지름 임계값은 어디에도 없다.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import numpy as np
import pandas as pd
import yaml

from ..candidate_j import aggregate as A
from ..config import Settings
from ..current_state import domains as D
from ..current_state import scales as S
from ..current_state.signals import indicator_signals
from . import evidence as E
from .filter import confirm_transitions, filter_scores
from .freshness import FreshnessPolicy

#: 중단된 사전검증 설정. 감사 기록으로 보존한다.
STOPPED_CONFIG_NAME = "four_phase.yaml"
#: §10의 개념적 결정을 반영한 새 버전. 이전 스냅샷을 덮어쓰지 않는다.
CONFIG_NAME = "four_phase_v1_1.yaml"
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
    def confirmation_weeks(self) -> int:
        """확인 없이 매주 승자를 그대로 쓰던 v1.0 동작이 1주에 해당한다."""

        return int(self.document["soft_filter"].get("confirmation_weeks", 1))

    @property
    def immediate_margin(self) -> float:
        return float(self.document["soft_filter"].get("immediate_margin", 0.0))

    @property
    def separation_floor(self) -> float:
        return float(self.document.get("evidence_quality", {}).get("separation_floor", 0.10))

    @property
    def stale_weeks(self) -> float:
        return float(self.document.get("evidence_quality", {}).get("stale_weeks", 8.0))

    @property
    def freshness(self) -> FreshnessPolicy:
        """신선도 정책. 중단된 v1.0 설정에는 없으므로 그때 동작과 같은 기본값을 둔다."""

        document = self.document.get("freshness_policy", {})
        return FreshnessPolicy(
            domain_stale_weeks=float(document.get("domain_stale_weeks", self.stale_weeks)),
            panel_silent_grace_weeks=int(document.get("panel_silent_grace_weeks", 10**6)),
            panel_silent_withhold_weeks=int(document.get("panel_silent_withhold_weeks", 10**6)),
            minimum_fresh_coincident_domains=int(
                document.get("minimum_fresh_coincident_domains", 0)
            ),
        )

    @property
    def momentum_weeks(self) -> int:
        return int(self.document["momentum_weeks"])

    @property
    def gates(self) -> dict[str, Any]:
        return dict(self.document["adoption_gates"])


def load_config(settings: Settings, name: str = CONFIG_NAME) -> FourPhaseConfig:
    path: Path = settings.root / "configs" / name
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
    confirming_domains: pd.Series
    alert_level: pd.Series
    alert_character: pd.Series
    evidence_quality_high: pd.Series
    filtered_winner: pd.Series
    confirmation_pending: pd.Series
    weeks_since_release: pd.DataFrame
    arrived: pd.DataFrame
    events: pd.DataFrame
    coordinates: pd.DataFrame
    thresholds: E.Thresholds


def _row(frame: pd.DataFrame, week: pd.Timestamp) -> pd.Series:
    """한 주의 도메인 행. pandas 스텁이 DataFrame 가능성을 남겨 두어 좁혀 준다."""

    return cast("pd.Series", frame.loc[week])


def _persistent_positive(momentum: pd.Series, band: float) -> pd.Series:
    """총량 모멘텀이 연속으로 중립대를 넘어 양수였던 주 수. 그 시점까지만 본다."""

    counts: list[int] = []
    streak = 0
    for value in momentum:
        streak = streak + 1 if float(value) > band else 0
        counts.append(streak)
    return pd.Series(counts, index=momentum.index)


@dataclass(frozen=True)
class PreparedInputs:
    """임계값과 무관한 부분. 프런티어 탐색이 이것을 한 번만 만들고 재사용한다.

    전처리·표준화·유계 총량은 후보 I·J에서 확정됐고 이 스테이지에서 다시 열지 않는다.
    그래서 임계값을 바꿔도 여기까지는 그대로다. 탐색이 같은 생산 코드를 쓰게 하려고
    쪼갠 것이지, 별도 계산 경로를 두려는 것이 아니다.
    """

    index: pd.DatetimeIndex
    level_scaled: pd.DataFrame
    momentum_scaled: pd.DataFrame
    level_aggregate: A.BoundedAggregate
    momentum_aggregate: A.BoundedAggregate
    activity_level: pd.Series
    activity_momentum: pd.Series
    concentration: pd.Series
    weeks_since_release: pd.DataFrame
    arrived: pd.DataFrame
    events: pd.DataFrame
    coordinates: pd.DataFrame


def prepare(
    observations: pd.DataFrame,
    settings: Settings,
    as_of: pd.Timestamp,
    config: FourPhaseConfig,
) -> PreparedInputs:
    """그 시점까지의 자료로 도메인 수준·모멘텀과 유계 총량까지 만든다."""

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
    coordinates = pd.DataFrame(
        {
            "x": activity_momentum,
            "y": activity_level,
            "angle": np.degrees(np.arctan2(activity_level, activity_momentum)) % 360.0,
            "radius": np.sqrt(activity_level.pow(2) + activity_momentum.pow(2)),
        }
    )
    return PreparedInputs(
        index=pd.DatetimeIndex(index),
        level_scaled=level_scaled,
        momentum_scaled=momentum_scaled,
        level_aggregate=level_aggregate,
        momentum_aggregate=momentum_aggregate,
        activity_level=activity_level,
        activity_momentum=activity_momentum,
        concentration=D.concentration(level_scaled),
        weeks_since_release=age.reindex(index),
        arrived=arrived.reindex(index),
        events=events,
        coordinates=coordinates,
    )


@dataclass(frozen=True)
class ObservationLayer:
    """임계값까지 반영한 관측 층. 필터와 확인 규칙은 아직 걸지 않았다.

    프런티어가 필터 모수만 바꿔가며 볼 때 이 층을 다시 만들 필요가 없다. 탐색이
    생산 코드를 그대로 쓰게 하려는 분리다.
    """

    raw_scores: pd.DataFrame
    contraction_detail: pd.DataFrame
    recovery_detail: pd.DataFrame
    negative_level_domains: pd.Series
    negative_momentum_domains: pd.Series
    positive_momentum_domains: pd.Series
    confirming_domains: pd.Series
    alert_level: pd.Series
    alert_character: pd.Series
    neutral_both: pd.Series
    stale: pd.Series
    crowded: pd.Series
    thresholds: E.Thresholds


def observation_layer(
    prepared: PreparedInputs,
    thresholds: E.Thresholds,
    stale_weeks: float,
) -> ObservationLayer:
    """임계값을 걸어 주별 관측 점수와 보조 경보를 만든다."""

    index = prepared.index
    level_scaled = prepared.level_scaled
    momentum_scaled = prepared.momentum_scaled
    activity_level = prepared.activity_level
    activity_momentum = prepared.activity_momentum
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
        level = float(str(activity_level.loc[week]))
        momentum = float(str(activity_momentum.loc[week]))
        contraction = E.contraction_evidence(
            level,
            momentum,
            int(str(negative_level.loc[week])),
            int(str(negative_momentum.loc[week])),
            float(str(level_scaled.at[week, "labor_stress"])),
            float(str(momentum_scaled.at[week, "labor_stress"])),
            thresholds,
        )
        recovery = E.recovery_evidence(
            level,
            momentum,
            int(str(positive_momentum.loc[week])),
            int(str(persistence.loc[week])),
            thresholds,
        )
        breadth = E.breadth_support(
            _row(level_scaled, week), _row(momentum_scaled, week), thresholds
        )
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
    contraction_detail_frame = pd.DataFrame(contraction_rows, index=index)
    recovery_detail_frame = pd.DataFrame(recovery_rows, index=index)
    confirming_counts: list[int] = [
        E.confirming_coincident_domains(
            _row(level_scaled, week), _row(momentum_scaled, week), thresholds
        )
        for week in index
    ]
    confirming = pd.Series(confirming_counts, index=index, name="confirming_domains")
    raw_scores = pd.DataFrame(score_rows, index=index, columns=list(E.PHASES))
    alert_rows = [
        E.recession_alert(
            float(str(contraction_detail_frame.at[week, "alert_evidence"])),
            int(str(confirming.loc[week])),
            thresholds,
        )
        for week in index
    ]
    stale = prepared.weeks_since_release.gt(stale_weeks).any(axis=1)
    crowded = prepared.concentration.gt(thresholds.concentration_flag).fillna(False)
    neutral_both = activity_level.abs().le(thresholds.neutral_level) & activity_momentum.abs().le(
        thresholds.neutral_momentum
    )
    return ObservationLayer(
        raw_scores=raw_scores,
        contraction_detail=contraction_detail_frame,
        recovery_detail=recovery_detail_frame,
        negative_level_domains=negative_level,
        negative_momentum_domains=negative_momentum,
        positive_momentum_domains=positive_momentum,
        confirming_domains=confirming,
        alert_level=pd.Series([a for a, _ in alert_rows], index=index, name="recession_alert"),
        alert_character=pd.Series(
            [c for _, c in alert_rows], index=index, name="recession_alert_character"
        ),
        neutral_both=neutral_both,
        stale=stale,
        crowded=crowded,
        thresholds=thresholds,
    )


def decide(
    prepared: PreparedInputs,
    observation: ObservationLayer,
    lam: float,
    epsilon: float,
    confirmation_weeks: int,
    immediate_margin: float,
    separation_floor: float,
) -> FourPhaseRun:
    """관측 층에 필터와 §8의 확인 규칙을 걸어 공식 현재국면 하나를 낸다.

    필터를 먼저 돌려 사후확률을 얻고, 그 다음 확인 규칙을 건다. 확인 규칙이 쓰는
    "증거 품질이 높은가"는 공식 국면을 **알기 전에** 정해져야 순환하지 않는다. 그래서
    사유 넷 중 공식 국면과 무관한 것만 쓴다 — 중립대, 신선도, 집중도, 분리도.
    """

    index = prepared.index
    thresholds = observation.thresholds
    raw_scores = observation.raw_scores
    prior_pass = filter_scores(raw_scores, lam, epsilon)
    ordered = np.sort(prior_pass.filtered[list(E.PHASES)].to_numpy(dtype=float), axis=1)
    separation = pd.Series(ordered[:, -1] - ordered[:, -2], index=index)
    quality_high = (
        ~observation.neutral_both
        & ~observation.stale
        & ~observation.crowded
        & separation.ge(separation_floor)
    ).rename("evidence_quality_high")
    official_phase, pending = confirm_transitions(
        prior_pass.filtered,
        raw_scores,
        quality_high,
        confirmation_weeks,
        immediate_margin,
    )
    negative_level = observation.negative_level_domains
    negative_momentum = observation.negative_momentum_domains
    positive_momentum = observation.positive_momentum_domains
    confirming = observation.confirming_domains
    contraction_detail_frame = observation.contraction_detail
    recovery_detail_frame = observation.recovery_detail
    alert_level = observation.alert_level
    alert_character = observation.alert_character
    run_age = prepared.weeks_since_release
    run_concentration = prepared.concentration
    return FourPhaseRun(
        raw_scores=raw_scores,
        filtered_scores=prior_pass.filtered,
        raw_phase=prior_pass.raw,
        official_phase=official_phase,
        activity_level=prepared.activity_level,
        activity_momentum=prepared.activity_momentum,
        level_scaled=prepared.level_scaled,
        momentum_scaled=prepared.momentum_scaled,
        level_aggregate=prepared.level_aggregate,
        momentum_aggregate=prepared.momentum_aggregate,
        negative_level_domains=negative_level,
        negative_momentum_domains=negative_momentum,
        positive_momentum_domains=positive_momentum,
        concentration=run_concentration,
        contraction_detail=contraction_detail_frame,
        recovery_detail=recovery_detail_frame,
        confirming_domains=confirming,
        alert_level=alert_level,
        alert_character=alert_character,
        evidence_quality_high=quality_high,
        filtered_winner=prior_pass.filtered_winner,
        confirmation_pending=pending,
        weeks_since_release=run_age,
        arrived=prepared.arrived,
        events=prepared.events,
        coordinates=prepared.coordinates,
        thresholds=thresholds,
    )


def score(
    prepared: PreparedInputs,
    config: FourPhaseConfig,
    thresholds: E.Thresholds | None = None,
    lam: float | None = None,
    epsilon: float | None = None,
    confirmation_weeks: int | None = None,
    immediate_margin: float | None = None,
) -> FourPhaseRun:
    """준비된 입력에 임계값과 필터를 걸어 공식 현재국면 하나까지 만든다."""

    resolved = config.thresholds if thresholds is None else thresholds
    observation = observation_layer(prepared, resolved, config.stale_weeks)
    return decide(
        prepared,
        observation,
        config.lam if lam is None else lam,
        config.epsilon if epsilon is None else epsilon,
        config.confirmation_weeks if confirmation_weeks is None else confirmation_weeks,
        config.immediate_margin if immediate_margin is None else immediate_margin,
        config.separation_floor,
    )


def build(
    observations: pd.DataFrame,
    settings: Settings,
    as_of: pd.Timestamp,
    config: FourPhaseConfig,
) -> FourPhaseRun:
    """그 시점까지의 자료만으로 4국면 실행을 만든다."""

    return score(prepare(observations, settings, as_of, config), config)
