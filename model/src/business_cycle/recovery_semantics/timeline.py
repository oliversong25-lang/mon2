"""§5. 도메인별 회복 관측 타임라인과 지연 층 분해.

동결 모델을 다시 부르되 **읽기만** 한다. ``prepare``·``score``는 생산 코드 그대로이며
이 모듈은 그 결과에서 도메인 단위 사실을 꺼내 적을 뿐이다.

국면 점수는 도메인에 대해 가법이 아니다. 그래서 "도메인 i가 회복 점수의 몇 %를
만들었다"는 수는 존재하지 않는다. 지어내지 않는다. 대신 실제로 존재하는 것을 적는다 —
그 도메인이 총량 수준·총량 모멘텀에 넣은 (상한을 건) 몫, 그리고 그 도메인이 공식
국면을 뒷받침하는지 반대하는지.
"""

from __future__ import annotations

from typing import Any, Final

import numpy as np
import pandas as pd

from ..config import Settings, load_baseline
from ..current_state.domains import COINCIDENT_DOMAINS, DOMAIN_MEMBERS, DOMAINS
from ..data.alfred import observations_as_of, slice_vintage
from ..four_phase import alfred as AL
from ..four_phase import evidence as E
from ..four_phase.engine import FourPhaseConfig, prepare, score
from ..operational_review.review import transition_watch

#: 지연 층. 합이 달력 지연과 정확히 맞아야 한다.
LAYERS: Final[tuple[str, ...]] = (
    "publication_delay_weeks",
    "domain_observation_availability_weeks",
    "transformation_and_raw_score_lag_weeks",
    "confirmation_delay_weeks",
    "transition_filter_delay_weeks",
    "freshness_or_withholding_delay_weeks",
)


def domain_observation_date(
    frames: dict[str, pd.DataFrame], vintage: pd.Timestamp, domain: str
) -> pd.Timestamp | None:
    """그 as-of 시점에 그 도메인이 덮고 있던 가장 늦은 관측 월."""

    dates: list[pd.Timestamp] = []
    for series_id in DOMAIN_MEMBERS[domain]:
        frame = frames.get(series_id)
        if frame is None:
            continue
        visible = slice_vintage(frame, vintage)
        if not visible.empty:
            dates.append(pd.Timestamp(visible["date"].max()))
    return max(dates) if dates else None


def build(
    settings: Settings,
    config: FourPhaseConfig,
    start: pd.Timestamp,
    end: pd.Timestamp,
    baseline_name: str = "candidate_h_breadth_gate",
) -> pd.DataFrame:
    """구간 안 각 as-of 주의 도메인 단위 기록. 캐시만 쓰고 네트워크도 키도 쓰지 않는다."""

    frames = AL.cached_frames(settings)
    baseline = load_baseline(baseline_name, settings)
    rows: list[dict[str, Any]] = []
    for vintage in pd.date_range(start, end, freq="W-FRI"):
        observations = observations_as_of(frames, vintage, settings.indicators["indicators"])
        prepared = prepare(observations, baseline, vintage, config)
        if not len(prepared.index):
            continue
        run = score(prepared, config)
        week = run.official_phase.index[-1]
        stance = E.domain_stance(
            run.level_scaled.loc[week],
            run.momentum_scaled.loc[week],
            str(run.raw_phase.loc[week]),
            config.thresholds,
        )
        row: dict[str, Any] = {
            "as_of": str(vintage.date()),
            "last_modelled_week": str(pd.Timestamp(str(week)).date()),
            "raw_phase": str(run.raw_phase.loc[week]),
            "filtered_winner": str(run.filtered_winner.loc[week]),
            "official_phase": str(run.official_phase.loc[week]),
            "activity_level": float(str(run.activity_level.loc[week])),
            "activity_momentum": float(str(run.activity_momentum.loc[week])),
            "positive_momentum_domains": int(str(run.positive_momentum_domains.loc[week])),
            "recovery_evidence": float(str(run.recovery_detail.at[week, "recovery_evidence"])),
            "recovery_improvement": float(str(run.recovery_detail.at[week, "improvement"])),
            "recovery_breadth": float(str(run.recovery_detail.at[week, "breadth"])),
            "recovery_persistence": float(str(run.recovery_detail.at[week, "persistence"])),
            "contraction_evidence": float(
                str(run.contraction_detail.at[week, "contraction_evidence"])
            ),
        }
        for domain in DOMAINS:
            observed = domain_observation_date(frames, vintage, domain)
            row[f"{domain}__observation_through"] = (
                None if observed is None else str(observed.date())
            )
            row[f"{domain}__level"] = float(str(run.level_scaled.at[week, domain]))
            row[f"{domain}__momentum"] = float(str(run.momentum_scaled.at[week, domain]))
            # 총량은 상한을 건 등가중 평균이다. 그래서 도메인 몫은 존재하고 가법이다.
            row[f"{domain}__level_contribution"] = float(
                str(run.level_aggregate.bounded_frame.at[week, domain])
            ) / len(DOMAINS)
            row[f"{domain}__momentum_contribution"] = float(
                str(run.momentum_aggregate.bounded_frame.at[week, domain])
            ) / len(DOMAINS)
            row[f"{domain}__stance_on_raw_phase"] = stance.get(domain, "")
            row[f"{domain}__weeks_since_release"] = float(
                str(run.weeks_since_release.at[week, domain])
            )
        rows.append(row)
    return pd.DataFrame(rows).set_index("as_of")


def latest_vintage_detail(
    settings: Settings,
    config: FourPhaseConfig,
    as_of: pd.Timestamp,
    baseline_name: str = "candidate_h_breadth_gate",
) -> pd.DataFrame:
    """최신 수정치 인과 경로의 도메인 단위 기록. 2013년 이전 에피소드는 여기서만 볼 수 있다.

    엄격 ALFRED 캐시는 2013-06-14부터다. 2009년 금융위기 저점은 그보다 앞서므로 실시간
    경로가 존재하지 않는다. 그 사실을 숨기지 않고, 이 경로의 역할을 `latest_vintage`로
    적어 둔다.
    """

    from ..validation.phase4 import load_core_observations

    core, _ = load_core_observations(settings)
    prepared = prepare(core, load_baseline(baseline_name, settings), as_of, config)
    run = score(prepared, config)
    frame = pd.DataFrame(
        {
            "raw_phase": run.raw_phase,
            "filtered_winner": run.filtered_winner,
            "official_phase": run.official_phase,
            "phase_status": "official",
            "activity_level": run.activity_level,
            "activity_momentum": run.activity_momentum,
            "positive_momentum_domains": run.positive_momentum_domains,
            "negative_level_domains": run.negative_level_domains,
            "confirming_domains": run.confirming_domains,
            "concentration": run.concentration,
            "evidence_quality_high": run.evidence_quality_high,
            "confirmation_pending": run.confirmation_pending,
            "recession_alert": run.alert_level,
            "recession_alert_character": run.alert_character,
            "recovery_evidence": run.recovery_detail["recovery_evidence"],
            "recovery_improvement": run.recovery_detail["improvement"],
            "recovery_breadth": run.recovery_detail["breadth"],
            "recovery_persistence": run.recovery_detail["persistence"],
            "contraction_evidence": run.contraction_detail["contraction_evidence"],
        }
    )
    ordered = np.sort(run.filtered_scores[list(E.PHASES)].to_numpy(dtype=float), axis=1)
    frame["phase_separation"] = ordered[:, -1] - ordered[:, -2]
    for name in E.PHASES:
        frame[f"raw_{name}"] = run.raw_scores[name]
        frame[f"filtered_{name}"] = run.filtered_scores[name]
    for domain in DOMAINS:
        frame[f"{domain}__level"] = run.level_scaled[domain]
        frame[f"{domain}__momentum"] = run.momentum_scaled[domain]
        frame[f"{domain}__level_contribution"] = run.level_aggregate.bounded_frame[domain] / len(
            DOMAINS
        )
        frame[f"{domain}__momentum_contribution"] = run.momentum_aggregate.bounded_frame[
            domain
        ] / len(DOMAINS)
        frame[f"{domain}__weeks_since_release"] = run.weeks_since_release[domain]
    watches: list[str] = []
    for week in frame.index:
        scores = {name: float(str(frame.at[week, f"filtered_{name}"])) for name in E.PHASES}
        watches.append(transition_watch(scores, str(frame.at[week, "official_phase"])))
    frame["transition_watch"] = watches
    frame.index = pd.Index([str(pd.Timestamp(week).date()) for week in frame.index], name="week")
    return frame


def _first(frame: pd.DataFrame, mask: pd.Series[bool]) -> str | None:
    hits = frame.index[mask.to_numpy(dtype=bool)]
    return str(hits[0]) if len(hits) else None


def domain_recovery_timeline(
    frame: pd.DataFrame,
    trough_month_start: pd.Timestamp,
    trough_month_end: pd.Timestamp,
    thresholds: E.Thresholds,
) -> list[dict[str, Any]]:
    """§5가 요구한 도메인별 날짜. 동행 도메인과 노동시장을 함께 적되 역할을 구분한다."""

    following = (trough_month_end + pd.Timedelta(days=1)).normalize()
    rows: list[dict[str, Any]] = []
    for domain in DOMAINS:
        observed = pd.to_datetime(frame[f"{domain}__observation_through"])
        momentum = frame[f"{domain}__momentum"].astype(float)
        level = frame[f"{domain}__level"].astype(float)
        supports = frame[f"{domain}__stance_on_raw_phase"].astype(str).eq("supports")
        rows.append(
            {
                "domain": domain,
                "role": "coincident" if domain in COINCIDENT_DOMAINS else "bridge_only",
                "series": "|".join(DOMAIN_MEMBERS[domain]),
                "first_as_of_covering_the_trough_month": _first(
                    frame, observed.ge(trough_month_start)
                ),
                "first_as_of_covering_the_following_month": _first(frame, observed.ge(following)),
                "first_positive_momentum_week": _first(frame, momentum.gt(0.0)),
                # 첫 주는 이전 주가 없어 판정할 수 없다. diff의 NaN이 그것을 걸러 준다.
                "first_non_deteriorating_level_week": _first(
                    frame, level.diff().ge(0.0).fillna(False)
                ),
                "first_week_supporting_recovery": _first(
                    frame,
                    momentum.gt(thresholds.neutral_momentum) & level.lt(thresholds.neutral_level),
                ),
                "first_week_the_domain_supported_the_raw_phase": _first(frame, supports),
                "momentum_contribution_at_first_official_recovery": None,
                "level_contribution_at_first_official_recovery": None,
            }
        )
    official_recovery = frame.index[frame["official_phase"].astype(str).eq("recovery")]
    if len(official_recovery):
        moment = official_recovery[0]
        for row in rows:
            domain = row["domain"]
            row["momentum_contribution_at_first_official_recovery"] = round(
                float(str(frame.at[moment, f"{domain}__momentum_contribution"])), 6
            )
            row["level_contribution_at_first_official_recovery"] = round(
                float(str(frame.at[moment, f"{domain}__level_contribution"])), 6
            )
    return rows


def availability_dates(
    frame: pd.DataFrame,
    trough_month_end: pd.Timestamp,
    minimum_coincident_domains: int,
) -> dict[str, str | None]:
    """자료가 언제 존재했는가. 모델이 언제 알아봤는가와 섞지 않는다.

    "인식 가능"의 폭은 동결 모델이 이미 쓰는 개념을 그대로 쓴다 — 공식 침체가 요구하는
    독립 동행 도메인 최소 수. 새 문턱을 만들지 않는다.
    """

    following = (trough_month_end + pd.Timedelta(days=1)).normalize()
    covering = pd.DataFrame(
        {
            domain: pd.to_datetime(frame[f"{domain}__observation_through"]).ge(following)
            for domain in COINCIDENT_DOMAINS
        },
        index=frame.index,
    )
    counts = covering.sum(axis=1)
    return {
        "calendar_trough_interval_end": str(trough_month_end.date()),
        "first_post_trough_data_available": _first(frame, counts.ge(1)),
        "recovery_observable_date": _first(frame, counts.ge(minimum_coincident_domains)),
        "first_raw_recovery": _first(frame, frame["raw_phase"].astype(str).eq("recovery")),
        "recovery_recognizable_date": _first(frame, frame["raw_phase"].astype(str).eq("recovery")),
        "first_filtered_recovery": _first(
            frame, frame["filtered_winner"].astype(str).eq("recovery")
        ),
        "first_official_recovery": _first(
            frame, frame["official_phase"].astype(str).eq("recovery")
        ),
    }
