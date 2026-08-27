"""캐시에 값이 있다는 것과 그 값이 현재 판단을 뒷받침할 만큼 새롭다는 것은 다른 사실이다.

이 층이 세 가지를 **따로** 재는 이유가 그것이다.

``cache_coverage``   그 시점에 쓸 수 있는 판본이 저장돼 있었는가.
``data_freshness``   그 값이 as-of 시점 기준으로 얼마나 오래된 것인가.
``phase_eligibility`` 그 정보집합으로 공식 현재국면을 낼 수 있는가.

셋을 하나로 합치면 2025년 가을에 실제로 일어난 일을 놓친다. 그때 일곱 계열 전부가
7주 동안 새 판본을 내지 않았다. 캐시에는 값이 그대로 있었고, 모델의 마지막 모델링
주는 2025-09-26에 멈춰 있었으며, 엄격 러너는 그 7주 묵은 판정을 **현재** 판정으로
보고했다. 신선도를 마지막 모델링 주에서 재면 그 사실이 보이지 않는다. 그래서 여기서는
**as-of 시점 기준으로** 잰다.

바뀌지 않은 캐시 값이 바뀌지 않은 경제를 뜻하지는 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

import pandas as pd

from ..current_state.domains import COINCIDENT_DOMAINS, DOMAINS

STATUS: Final[tuple[str, ...]] = ("official", "preliminary", "withheld")


@dataclass(frozen=True)
class FreshnessPolicy:
    """개발구간에서 잠근 값만 쓴다. 2025년 사건에서 새 임계값을 만들지 않는다.

    ``domain_stale_weeks``
        한 도메인이 새 관측 없이 견딜 수 있는 주 수. 이미 증거 품질 판정에 쓰던
        ``evidence_quality.stale_weeks``를 그대로 쓴다. 개발구간에서 월간 도메인의
        마지막 발표 이후 주 수는 최대 4주였으므로, 8주는 정규 발표 하나를 통째로
        놓친 뒤에야 걸린다.

    ``panel_silent_grace_weeks``
        패널 전체가 조용해도 되는 주 수. 개발구간 939주 중 **어떤 도메인도 새 관측을
        받지 못한 주는 0주**였다 — 패널에 주간 실업수당청구가 들어 있기 때문이다.
        그래서 정규 도착 간격은 1주이고, 유예는 놓친 발표 하나에 해당하는 1주다.

    ``panel_silent_withhold_weeks``
        패널 전체가 이만큼 넘게 조용하면 어떤 값도 현재가 아니다. 가장 느린 도메인의
        정규 발표 간격이 한 달(약 4.35주)이므로, 패널 전체가 한 발표 주기를 통째로
        건너뛰면 모든 값이 자기 일정보다 뒤처져 이월된 것이다.

    ``minimum_fresh_coincident_domains``
        §2가 고정한 값과 같다. 공식 침체가 동행 도메인 둘의 확인을 요구하듯, 공식
        국면 자체도 신선한 동행 도메인 둘을 요구한다.
    """

    domain_stale_weeks: float
    panel_silent_grace_weeks: int
    panel_silent_withhold_weeks: int
    minimum_fresh_coincident_domains: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain_stale_weeks": self.domain_stale_weeks,
            "panel_silent_grace_weeks": self.panel_silent_grace_weeks,
            "panel_silent_withhold_weeks": self.panel_silent_withhold_weeks,
            "minimum_fresh_coincident_domains": self.minimum_fresh_coincident_domains,
        }


@dataclass(frozen=True)
class Eligibility:
    """한 as-of 시점의 세 가지 사실. 합치지 않는다."""

    as_of: pd.Timestamp
    last_modelled_week: pd.Timestamp
    information_lag_weeks: int
    domain_age_weeks: dict[str, float]
    domain_fresh: dict[str, bool]
    domain_carried_forward: dict[str, bool]
    fresh_coincident_domains: int
    stale_domains: list[str]
    weeks_since_any_new_observation: int
    status: str
    reasons: list[str]

    @property
    def official(self) -> bool:
        return self.status == "official"

    @property
    def withheld(self) -> bool:
        return self.status == "withheld"

    def to_dict(self) -> dict[str, Any]:
        return {
            "as_of": str(self.as_of.date()),
            "last_modelled_week": str(self.last_modelled_week.date()),
            "information_lag_weeks": self.information_lag_weeks,
            "domain_age_weeks_at_as_of": {k: round(v, 1) for k, v in self.domain_age_weeks.items()},
            "domain_fresh": self.domain_fresh,
            "domain_carried_forward": self.domain_carried_forward,
            "fresh_coincident_domains": self.fresh_coincident_domains,
            "stale_domains": self.stale_domains,
            "weeks_since_any_new_observation": self.weeks_since_any_new_observation,
            "phase_eligibility": self.status,
            "reasons": self.reasons,
        }


def _weeks_between(later: pd.Timestamp, earlier: pd.Timestamp) -> int:
    return max(0, int((pd.Timestamp(later) - pd.Timestamp(earlier)).days // 7))


def evaluate(
    as_of: pd.Timestamp,
    index: pd.DatetimeIndex,
    weeks_since_release: pd.DataFrame,
    arrived: pd.DataFrame,
    policy: FreshnessPolicy,
) -> Eligibility:
    """as-of 시점 기준으로 정보가 얼마나 오래됐는지, 그것으로 국면을 낼 수 있는지.

    핵심은 **마지막 모델링 주가 아니라 as-of 시점**에서 잰다는 것이다. 발표가 멈추면
    모델링 주는 그 자리에 멈춰 서고, 그 주에서 잰 나이는 영원히 정상으로 보인다.
    """

    if len(index) == 0:
        return Eligibility(
            as_of=as_of,
            last_modelled_week=as_of,
            information_lag_weeks=0,
            domain_age_weeks={},
            domain_fresh={},
            domain_carried_forward={},
            fresh_coincident_domains=0,
            stale_domains=list(DOMAINS),
            weeks_since_any_new_observation=0,
            status="withheld",
            reasons=["사용할 수 있는 관측이 없다"],
        )

    last = pd.Timestamp(str(index[-1]))
    lag = _weeks_between(as_of, last)

    ages: dict[str, float] = {}
    fresh: dict[str, bool] = {}
    carried: dict[str, bool] = {}
    for domain in DOMAINS:
        if domain not in weeks_since_release.columns:
            continue
        base = float(str(weeks_since_release.at[last, domain]))
        age = base + lag
        ages[domain] = age
        fresh[domain] = age <= policy.domain_stale_weeks
        # 마지막 모델링 주에 새 관측이 없었거나, as-of가 그 주보다 뒤라면 이월된 값이다.
        arrived_last = bool(arrived.at[last, domain]) if domain in arrived.columns else False
        carried[domain] = (not arrived_last) or lag > 0

    any_arrival = arrived.any(axis=1)
    since_last_arrival = 0
    for value in reversed(list(any_arrival)):
        if bool(value):
            break
        since_last_arrival += 1
    silent = since_last_arrival + lag

    stale = sorted(name for name, ok in fresh.items() if not ok)
    fresh_coincident = sum(1 for name in COINCIDENT_DOMAINS if fresh.get(name, False))

    # 판정 순서를 명시한다. 보류가 먼저다 — 정보집합이 너무 낡았다면 그 위에서 무엇을
    # 계산하든 "현재" 판정이 아니다.
    reasons: list[str] = []
    if fresh_coincident < policy.minimum_fresh_coincident_domains:
        reasons.append(
            f"신선한 동행 도메인이 {fresh_coincident}개뿐이다 "
            f"(최소 {policy.minimum_fresh_coincident_domains}개)"
        )
        status = "withheld"
    elif silent > policy.panel_silent_withhold_weeks:
        reasons.append(
            f"패널 전체가 {silent}주째 새 관측을 받지 못했다 "
            f"(가장 느린 정규 발표 주기 {policy.panel_silent_withhold_weeks}주 초과)"
        )
        status = "withheld"
    else:
        if silent > policy.panel_silent_grace_weeks:
            reasons.append(f"패널 전체가 {silent}주째 새 관측을 받지 못했다")
        if stale:
            reasons.append(f"오래된 도메인: {', '.join(stale)}")
        status = "preliminary" if reasons else "official"

    return Eligibility(
        as_of=as_of,
        last_modelled_week=last,
        information_lag_weeks=lag,
        domain_age_weeks=ages,
        domain_fresh=fresh,
        domain_carried_forward=carried,
        fresh_coincident_domains=fresh_coincident,
        stale_domains=stale,
        weeks_since_any_new_observation=silent,
        status=status,
        reasons=reasons,
    )
