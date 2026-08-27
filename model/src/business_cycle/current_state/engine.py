"""현재상태 엔진. 관측에서 도메인 상태·총량·국면·안정화까지를 한 번에 만든다.

X·Y는 계속 만들지만 **파생 요약**일 뿐이다. 공식 국면은 X·Y를 보지 않는다.
좌표 구역 매핑을 바꿔도 공식 국면은 달라지지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from ..config import Settings
from . import domains as D
from . import scales as S
from .classifier import PHASES, StateThresholds, classify_frame, separation
from .signals import freshness_weeks, indicator_signals
from .stabilizer import StabilizerResult, stabilize

WEEKS_PER_YEAR = 52.1775


@dataclass(frozen=True)
class CurrentStateRun:
    scores: pd.DataFrame
    stabilized: StabilizerResult
    activity_level: pd.Series
    activity_momentum: pd.Series
    level_scaled: pd.DataFrame
    momentum_scaled: pd.DataFrame
    level_raw: pd.DataFrame
    momentum_raw: pd.DataFrame
    negative_level_domains: pd.Series
    negative_momentum_domains: pd.Series
    positive_momentum_domains: pd.Series
    concentration: pd.Series
    events: pd.DataFrame
    held: pd.DataFrame
    coordinates: pd.DataFrame
    thresholds: StateThresholds


def _coordinates(activity_level: pd.Series, activity_momentum: pd.Series) -> pd.DataFrame:
    """시각화용 좌표. 공식 국면은 여기에 의존하지 않는다."""

    angle = np.degrees(np.arctan2(activity_level, activity_momentum)) % 360.0
    radius = np.sqrt(activity_level.pow(2) + activity_momentum.pow(2))
    return pd.DataFrame(
        {"x": activity_momentum, "y": activity_level, "angle": angle, "radius": radius}
    )


def build_state(
    observations: pd.DataFrame,
    settings: Settings,
    as_of: pd.Timestamp,
    thresholds: StateThresholds,
    *,
    scale_method: str = "rolling_mad",
    scale_window_years: float = 10.0,
    scale_minimum_years: float = 2.0,
    momentum_weeks: int | None = None,
    margin: float = 0.0,
) -> CurrentStateRun:
    """관측에서 현재상태 실행을 만든다. 전부 그 시점까지의 자료만 쓴다."""

    events, held, _ = indicator_signals(observations, settings, as_of)
    weeks = momentum_weeks or int(settings.model["momentum_weeks"])
    window = int(round(WEEKS_PER_YEAR * scale_window_years))
    minimum = int(round(WEEKS_PER_YEAR * scale_minimum_years))

    level_raw = D.domain_level_frame(held)
    momentum_raw = D.domain_momentum_frame(level_raw, weeks)
    level_columns: dict[str, pd.Series] = {}
    momentum_columns: dict[str, pd.Series] = {}
    for domain in D.DOMAINS:
        level_columns[domain] = S.standardize(level_raw[domain], scale_method, window, minimum)[0]
        momentum_columns[domain] = S.standardize(
            momentum_raw[domain], scale_method, window, minimum
        )[0]
    level_scaled = pd.DataFrame(level_columns)
    momentum_scaled = pd.DataFrame(momentum_columns)

    activity_level = D.aggregate_level(level_scaled)
    activity_momentum = D.aggregate_momentum(momentum_scaled)
    coincident = list(D.COINCIDENT_DOMAINS)
    negative_level, _, _ = D.count_states(level_scaled[coincident], thresholds.neutral_level)
    negative_momentum, positive_momentum, _ = D.count_states(
        momentum_scaled[coincident], thresholds.neutral_momentum
    )
    concentration = D.concentration(level_scaled)

    usable = activity_level.notna() & activity_momentum.notna()
    index = activity_level.index[usable]
    scores = classify_frame(
        activity_level.loc[index],
        activity_momentum.loc[index],
        negative_level.reindex(index).fillna(0),
        negative_momentum.reindex(index).fillna(0),
        thresholds,
    )
    stabilized = stabilize(scores, margin)
    return CurrentStateRun(
        scores=scores,
        stabilized=stabilized,
        activity_level=activity_level.loc[index],
        activity_momentum=activity_momentum.loc[index],
        level_scaled=level_scaled.loc[index],
        momentum_scaled=momentum_scaled.loc[index],
        level_raw=level_raw.loc[index],
        momentum_raw=momentum_raw.loc[index],
        negative_level_domains=negative_level.reindex(index),
        negative_momentum_domains=negative_momentum.reindex(index),
        positive_momentum_domains=positive_momentum.reindex(index),
        concentration=concentration.reindex(index),
        events=events,
        held=held,
        coordinates=_coordinates(activity_level.loc[index], activity_momentum.loc[index]),
        thresholds=thresholds,
    )


def evidence_quality(
    run: CurrentStateRun, settings: Settings, week: pd.Timestamp
) -> dict[str, Any]:
    """증거의 질. 국면을 바꾸지 않고, 그 판정을 얼마나 믿을 수 있는지만 말한다."""

    reasons: list[str] = []
    level = float(str(run.activity_level.loc[week]))
    momentum = float(str(run.activity_momentum.loc[week]))
    thresholds = run.thresholds
    if abs(level) <= thresholds.neutral_level and abs(momentum) <= thresholds.neutral_momentum:
        reasons.append("activity level and momentum both inside the neutral band")
    elif abs(level) <= thresholds.neutral_level:
        reasons.append("activity level inside the neutral band")
    share = float(str(run.concentration.loc[week]))
    if np.isfinite(share) and share > thresholds.concentration_flag:
        reasons.append(f"one domain carries {share:.1%} of the level signal")
    freshness = freshness_weeks(run.events, settings, week)
    core = settings.indicators["indicators"]
    stale = [
        domain
        for domain, age in freshness.items()
        if age
        > max(
            float(core[member]["max_age_weeks"])
            for member in D.DOMAIN_MEMBERS[domain]
            if member in core
        )
    ]
    if stale:
        reasons.append(f"stale domains ({', '.join(sorted(stale))})")
    missing = [
        domain
        for domain in D.DOMAINS
        if not np.isfinite(float(str(run.level_scaled.loc[week, domain])))
    ]
    if missing:
        reasons.append(f"missing domains ({', '.join(missing)})")
    gap = separation({name: float(str(run.scores.loc[week, name])) for name in PHASES})
    if gap < 0.10:
        reasons.append("phases are not strongly separated")
    level_name = "high" if not reasons else ("medium" if len(reasons) == 1 else "low")
    return {
        "evidence_quality": level_name,
        "evidence_reasons": reasons,
        "phase_separation": round(gap, 6),
        "freshness_weeks": {k: round(v, 2) for k, v in freshness.items()},
        "calibrated": False,
    }
